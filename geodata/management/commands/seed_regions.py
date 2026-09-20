"""Load Kenya's 47 county (ADM1) boundaries into geodata.Region.

Source: geoBoundaries gbOpen, KEN ADM1 (see docs/DATA_DICTIONARY.md).
The release is pinned to a specific upstream commit so that a rebuild a
year from now loads byte-identical geometry.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.contrib.gis.gdal import DataSource, GDALException
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from geodata.models import Region

# Pinned to upstream commit 9469f09 (geoBoundaries release of 2023-12-12,
# built from RCMRD GeoPortal source data). Discover newer releases via
# https://www.geoboundaries.org/api/current/gbOpen/KEN/ADM1/ -> gjDownloadURL.
SOURCE_URL = (
    "https://github.com/wmgeolab/geoBoundaries/raw/9469f09"
    "/releaseData/gbOpen/KEN/ADM1/geoBoundaries-KEN-ADM1.geojson"
)

# Where a downloaded copy is cached. data/raw/ is gitignored by design.
DEFAULT_CACHE_PATH = Path(settings.BASE_DIR) / "data" / "raw" / "geoBoundaries-KEN-ADM1.geojson"

# Verified against the real file: geoBoundaries ADM1 features carry
# shapeName/shapeISO/shapeID/shapeGroup/shapeType, and shapeName holds the
# county name. The fallbacks cover the OCHA/IEBC COD shapefile, so that
# --path also works against that source without a code change.
NAME_FIELD_CANDIDATES = ("shapeName", "ADM1_EN", "COUNTY_NAM", "COUNTY", "NAME_1")

# Geometry is stored in WGS 84; areas are measured in Arc 1960 / UTM 36S,
# the projected CRS appropriate for Kenya. Degrees have no metric area.
STORAGE_SRID = 4326
METRIC_SRID = 21037

EXPECTED_COUNTY_COUNT = 47


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


def resolve_name_field(layer) -> str:
    """Return the attribute holding the county name, or fail loudly."""
    for candidate in NAME_FIELD_CANDIDATES:
        if candidate in layer.fields:
            return candidate
    raise CommandError(
        "Could not find a county-name attribute in the source layer. "
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


def area_sq_km(geom) -> float:
    """Area in km², measured on a projected clone so the number is metric."""
    metric = geom.clone()
    metric.transform(METRIC_SRID)
    return metric.area / 1_000_000.0


class Command(BaseCommand):
    help = "Load Kenya's 47 county boundaries (ADM1) into the Region table."

    def add_arguments(self, parser):
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
            help="Load a single county by name (case-insensitive). Defaults to all 47.",
        )
        parser.add_argument(
            "--name-field",
            type=str,
            default=None,
            help="Override the source attribute holding the county name.",
        )
        parser.add_argument(
            "--refresh",
            action="store_true",
            help="Re-download even if a cached copy already exists.",
        )

    def handle(self, *args, **options):
        source_path = self._resolve_source(options["path"], refresh=options["refresh"])
        self.stdout.write(f"Reading boundaries from {source_path}")

        try:
            layer = DataSource(str(source_path))[0]
        except (GDALException, IndexError) as exc:
            raise CommandError(f"Could not open {source_path} as spatial data: {exc}") from exc

        name_field = options["name_field"] or resolve_name_field(layer)
        wanted = options["county"]
        if wanted:
            self.stdout.write(f"Filtering to county: {wanted}")

        created_count = 0
        updated_count = 0
        loaded_names: list[str] = []

        with transaction.atomic():
            for feature in layer:
                county_name = str(feature.get(name_field)).strip()
                if wanted and county_name.casefold() != wanted.strip().casefold():
                    continue

                geom = feature.geom.geos
                # GeoJSON CRS84 is lon/lat, equivalent to EPSG:4326.
                if geom.srid is None:
                    geom.srid = STORAGE_SRID
                elif geom.srid != STORAGE_SRID:
                    geom.transform(STORAGE_SRID)

                multipolygon = to_multipolygon(geom)

                _region, created = Region.objects.update_or_create(
                    county_name=county_name,
                    defaults={
                        "geom": multipolygon,
                        "area_size": area_sq_km(multipolygon),
                    },
                )
                loaded_names.append(county_name)
                if created:
                    created_count += 1
                else:
                    updated_count += 1

            if wanted and not loaded_names:
                raise CommandError(
                    f"No county named {wanted!r} found in {source_path}. "
                    f"Check the spelling against the {name_field} attribute."
                )

        self._report(created_count, updated_count, loaded_names, single=bool(wanted))

    def _resolve_source(self, path: Path | None, *, refresh: bool) -> Path:
        if path is not None:
            if not path.exists():
                raise CommandError(f"--path does not exist: {path}")
            return path

        if DEFAULT_CACHE_PATH.exists() and not refresh:
            self.stdout.write(
                f"Using cached download at {DEFAULT_CACHE_PATH} (--refresh to re-fetch)"
            )
            return DEFAULT_CACHE_PATH

        self.stdout.write(f"Downloading {SOURCE_URL}")
        return download_boundaries(SOURCE_URL, DEFAULT_CACHE_PATH)

    def _report(self, created_count, updated_count, loaded_names, *, single):
        total = created_count + updated_count
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Created: {created_count}"))
        self.stdout.write(self.style.SUCCESS(f"Updated: {updated_count}"))
        self.stdout.write(self.style.SUCCESS(f"Total loaded this run: {total}"))
        self.stdout.write(f"Regions now in database: {Region.objects.count()}")

        if not single and total != EXPECTED_COUNTY_COUNT:
            self.stdout.write(
                self.style.WARNING(
                    f"Expected {EXPECTED_COUNTY_COUNT} counties but loaded {total}. "
                    "The upstream source may have changed — verify before relying on this data."
                )
            )
