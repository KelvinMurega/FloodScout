"""Load Kenya's county (ADM1) or sub-county (ADM2) boundaries into geodata.Region.

Source: geoBoundaries gbOpen, KEN ADM1 and ADM2 (see docs/DATA_DICTIONARY.md).
The release is pinned to a specific upstream commit so that a rebuild a
year from now loads byte-identical geometry.

Counties load first (`--level county`, the default). Sub-counties
(`--level sub_county`) need the counties already in place, because the ADM2
file does not say which county a sub-county is in: each one is assigned to
the county it overlaps most.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.contrib.gis.gdal import DataSource, GDALException
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from geodata.models import Region

# Both files pinned to upstream commit 9469f09 (geoBoundaries release of
# 2023-12-12). ADM1 is built from RCMRD GeoPortal data, ADM2 from IEBC /
# OCHA ROSEA (290 sub-counties, CC BY 3.0 IGO). Discover newer releases via
# https://www.geoboundaries.org/api/current/gbOpen/KEN/ADM1/ -> gjDownloadURL.
_RELEASE_URL = "https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/KEN"
_CACHE_DIR = Path(settings.BASE_DIR) / "data" / "raw"  # gitignored by design


@dataclass(frozen=True)
class LevelSource:
    url: str
    cache_path: Path
    # Verified against the real files: geoBoundaries features carry
    # shapeName/shapeISO/shapeID/shapeGroup/shapeType, and shapeName holds
    # the name. The fallbacks cover the OCHA/IEBC COD shapefiles, so that
    # --path also works against that source without a code change.
    name_fields: tuple[str, ...]
    expected_count: int


SOURCES = {
    Region.Level.COUNTY: LevelSource(
        url=f"{_RELEASE_URL}/ADM1/geoBoundaries-KEN-ADM1.geojson",
        cache_path=_CACHE_DIR / "geoBoundaries-KEN-ADM1.geojson",
        name_fields=("shapeName", "ADM1_EN", "COUNTY_NAM", "COUNTY", "NAME_1"),
        expected_count=47,
    ),
    Region.Level.SUB_COUNTY: LevelSource(
        url=f"{_RELEASE_URL}/ADM2/geoBoundaries-KEN-ADM2.geojson",
        cache_path=_CACHE_DIR / "geoBoundaries-KEN-ADM2.geojson",
        name_fields=("shapeName", "ADM2_EN", "SUBCOUNTY", "CONSTITUEN", "NAME_2"),
        expected_count=290,
    ),
}

# The county-level names, kept because the download tests patch them.
SOURCE_URL = SOURCES[Region.Level.COUNTY].url
DEFAULT_CACHE_PATH = SOURCES[Region.Level.COUNTY].cache_path
NAME_FIELD_CANDIDATES = SOURCES[Region.Level.COUNTY].name_fields

# A sub-county whose best-matching county covers less than this share of it
# is reported: the two boundary sets were drawn by different agencies, so
# slivers of overlap with a neighbour are normal, but a weak best match is not.
MIN_PARENT_OVERLAP = 0.5

# Geometry is stored in WGS 84; areas are measured in Arc 1960 / UTM 36S,
# the projected CRS appropriate for Kenya. Degrees have no metric area.
STORAGE_SRID = 4326
METRIC_SRID = 21037

EXPECTED_COUNTY_COUNT = SOURCES[Region.Level.COUNTY].expected_count


def download_boundaries(url: str, dest: Path) -> Path:
    """Fetch the boundary file to `dest`. Isolated so tests can patch it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "FloodScout/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CommandError(f"Could not download boundaries from {url}: {exc}") from exc
    dest.write_bytes(payload)
    return dest


def resolve_name_field(layer, candidates: tuple[str, ...] = NAME_FIELD_CANDIDATES) -> str:
    """Return the attribute holding the region name, or fail loudly."""
    for candidate in candidates:
        if candidate in layer.fields:
            return candidate
    raise CommandError(
        "Could not find a region-name attribute in the source layer. "
        f"Fields present: {sorted(layer.fields)}. "
        "Pass --name-field to name it explicitly."
    )


def to_multipolygon(geom) -> MultiPolygon:
    """Normalise to MultiPolygon.

    Region.geom is a MultiPolygonField, but 43 of the 47 counties arrive
    from the source as plain Polygons — only the four coastal counties
    with islands (Mombasa, Kwale, Kilifi, Lamu) are already MultiPolygons.
    """
    if isinstance(geom, MultiPolygon):
        return geom
    if isinstance(geom, Polygon):
        return MultiPolygon(geom, srid=geom.srid)
    raise CommandError(f"Unsupported geometry type for a county boundary: {geom.geom_type}")


def find_parent_county(geom, counties) -> tuple[Region | None, float]:
    """The county `geom` overlaps most, and the share of `geom` it covers.

    Areas are compared in degrees, which is fine for a ratio between shapes
    that lie next to each other.
    """
    best, best_overlap = None, 0.0
    for county in counties:
        if not county.geom.intersects(geom):
            continue
        overlap = county.geom.intersection(geom).area
        if overlap > best_overlap:
            best, best_overlap = county, overlap
    share = best_overlap / geom.area if geom.area else 0.0
    return best, share


def area_sq_km(geom) -> float:
    """Area in km², measured on a projected clone so the number is metric."""
    metric = geom.clone()
    metric.transform(METRIC_SRID)
    return metric.area / 1_000_000.0


class Command(BaseCommand):
    help = (
        "Load Kenya's 47 county boundaries (ADM1), or with --level sub_county "
        "its 290 sub-counties (ADM2), into the Region table."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--level",
            choices=[level.value for level in Region.Level],
            default=Region.Level.COUNTY.value,
            help="Which boundaries to load. Sub-counties need the counties loaded first.",
        )
        parser.add_argument(
            "--path",
            type=Path,
            default=None,
            help="Local shapefile/GeoJSON to load. Defaults to downloading the documented source.",
        )
        parser.add_argument(
            "--county",
            type=str,
            default=None,
            help=(
                "Load a single county by name (case-insensitive). With --level "
                "sub_county, loads that county's sub-counties. Defaults to all."
            ),
        )
        parser.add_argument(
            "--name-field",
            type=str,
            default=None,
            help="Override the source attribute holding the region name.",
        )
        parser.add_argument(
            "--refresh",
            action="store_true",
            help="Re-download even if a cached copy already exists.",
        )

    def handle(self, *args, **options):
        level = Region.Level(options["level"])
        source = SOURCES[level]
        source_path = self._resolve_source(level, options["path"], refresh=options["refresh"])
        self.stdout.write(f"Reading {level.label.lower()} boundaries from {source_path}")

        try:
            layer = DataSource(str(source_path))[0]
        except (GDALException, IndexError) as exc:
            raise CommandError(f"Could not open {source_path} as spatial data: {exc}") from exc

        name_field = options["name_field"] or resolve_name_field(layer, source.name_fields)
        wanted = options["county"]
        if wanted:
            self.stdout.write(f"Filtering to county: {wanted}")

        counties = []
        if level == Region.Level.SUB_COUNTY:
            counties = list(Region.objects.filter(level=Region.Level.COUNTY))
            if not counties:
                raise CommandError(
                    "No counties in the database — run `manage.py seed_regions` "
                    "before loading sub-counties."
                )

        created_count = 0
        updated_count = 0
        loaded_names: list[str] = []

        with transaction.atomic():
            for feature in layer:
                name = str(feature.get(name_field)).strip()

                geom = feature.geom.geos
                # GeoJSON CRS84 is lon/lat, equivalent to EPSG:4326.
                if geom.srid is None:
                    geom.srid = STORAGE_SRID
                elif geom.srid != STORAGE_SRID:
                    geom.transform(STORAGE_SRID)

                multipolygon = to_multipolygon(geom)

                if level == Region.Level.COUNTY:
                    parent, county_name = None, name
                else:
                    parent, share = find_parent_county(multipolygon, counties)
                    if parent is None:
                        self.stdout.write(self.style.WARNING(
                            f"Skipped {name}: it overlaps no county in the database."
                        ))
                        continue
                    if share < MIN_PARENT_OVERLAP:
                        self.stdout.write(self.style.WARNING(
                            f"{name} assigned to {parent.name}, which covers only "
                            f"{share:.0%} of it — check the boundaries."
                        ))
                    county_name = parent.name

                if wanted and county_name.casefold() != wanted.strip().casefold():
                    continue

                _region, created = Region.objects.update_or_create(
                    level=level,
                    name=name,
                    defaults={
                        "county_name": county_name,
                        "parent": parent,
                        "geom": multipolygon,
                        "area_size": area_sq_km(multipolygon),
                    },
                )
                loaded_names.append(name)
                if created:
                    created_count += 1
                else:
                    updated_count += 1

            if wanted and not loaded_names:
                raise CommandError(
                    f"No county named {wanted!r} found in {source_path}. "
                    "Check the spelling against the county names."
                )

        self._report(
            level, source, created_count, updated_count, loaded_names, single=bool(wanted)
        )

    def _resolve_source(self, level, path: Path | None, *, refresh: bool) -> Path:
        if path is not None:
            if not path.exists():
                raise CommandError(f"--path does not exist: {path}")
            return path

        # Looked up through the module-level names for the county level so
        # tests can patch the download location.
        if level == Region.Level.COUNTY:
            url, cache_path = SOURCE_URL, DEFAULT_CACHE_PATH
        else:
            url, cache_path = SOURCES[level].url, SOURCES[level].cache_path

        if cache_path.exists() and not refresh:
            self.stdout.write(f"Using cached download at {cache_path} (--refresh to re-fetch)")
            return cache_path

        self.stdout.write(f"Downloading {url}")
        return download_boundaries(url, cache_path)

    def _report(self, level, source, created_count, updated_count, loaded_names, *, single):
        total = created_count + updated_count
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Created: {created_count}"))
        self.stdout.write(self.style.SUCCESS(f"Updated: {updated_count}"))
        self.stdout.write(self.style.SUCCESS(f"Total loaded this run: {total}"))
        self.stdout.write(
            f"{level.label} regions now in database: {Region.objects.filter(level=level).count()}"
        )

        if not single and total != source.expected_count:
            self.stdout.write(
                self.style.WARNING(
                    f"Expected {source.expected_count} {level.label.lower()} regions "
                    f"but loaded {total}. "
                    "The upstream source may have changed — verify before relying on this data."
                )
            )
