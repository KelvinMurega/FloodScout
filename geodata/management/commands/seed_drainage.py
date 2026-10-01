"""Fill the drainage fields of geodata.GeospatialFeature from real maps.

Three numbers per Region, each from a published dataset:

  distance_to_river     metres from the region's representative point to the
                        nearest river mapped in OpenStreetMap.
  flood_zone_fraction   share of the region inside the Copernicus 100-year
                        river flood extent (any depth above zero).
  built_up_fraction     share of the region covered by buildings, from the
                        JRC Global Human Settlement Layer for 2020.

Together they stand in for "how badly does this place drain": ground that
rivers spill onto, and ground sealed under buildings. There is no national
dataset of drains themselves.

The two fractions are zonal statistics. The regions are burnt onto the flood
map's own 90 m grid with `gdal_rasterize`, so every pixel knows which region
it is in, and the pixels are then counted. Downloads and the intermediate
rasters are cached under data/external/drainage/, so a re-run is quick.

Run `seed_terrain` first: this updates the rows it creates.
"""

from __future__ import annotations

import json
import math
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from django.conf import settings
from django.contrib.gis.gdal import DataSource, GDALRaster
from django.contrib.gis.geos import MultiLineString
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from geodata.models import GeospatialFeature, Region

from .seed_terrain import METRES_PER_DEGREE, representative_point

DRAINAGE_DIR = Path(settings.BASE_DIR) / "data" / "external" / "drainage"

WATERWAYS_URL = (
    "https://production-raw-data-api.s3.amazonaws.com/ISO3/KEN/waterways/"
    "hotosm_ken_waterways_osm_gpkg.zip"
)
# The four 10-degree tiles of the 100-year map that cover Kenya.
FLOOD_URL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard/RP100/"
FLOOD_TILES = (
    "ID150_N10_E30_RP100_depth.tif",
    "ID151_N0_E30_RP100_depth.tif",
    "ID161_N10_E40_RP100_depth.tif",
    "ID162_N0_E40_RP100_depth.tif",
)
BUILT_UP_NAME = "GHS_BUILT_S_E2020_GLOBE_R2023A_4326_30ss_V1_0"
BUILT_UP_URL = (
    "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/"
    f"GHS_BUILT_S_E2020_GLOBE_R2023A_4326_30ss/V1-0/{BUILT_UP_NAME}.zip"
)

# The flood map's resolution, 3 arc seconds. Everything is put on this grid.
PIXEL_DEG = 1 / 1200
# The built-up map's cells are 30 arc seconds, and each holds square metres
# of building. Dividing by the cell's area gives the built-up share.
BUILT_UP_CELL_DEG = 1 / 120

# Arc 1960 / UTM 36S, metres. The CRS `seed_regions` measures area in.
METRIC_SRID = 21037

ROWS_PER_BLOCK = 1024
DOWNLOAD_TIMEOUT_SECONDS = 600


def download(url: str, path: Path) -> Path:
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            with partial.open("wb") as handle:
                while chunk := response.read(1 << 20):
                    handle.write(chunk)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CommandError(f"Could not download {url}: {exc}") from exc
    partial.rename(path)
    return path


def run(*args: str) -> None:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise CommandError(f"{args[0]} failed: {result.stderr.strip()}")


def grid_extent(regions) -> tuple[float, float, float, float]:
    """(west, south, east, north) around every region, snapped to the pixel grid."""
    boxes = [region.geom.extent for region in regions]
    west = math.floor(min(box[0] for box in boxes) / PIXEL_DEG) * PIXEL_DEG
    south = math.floor(min(box[1] for box in boxes) / PIXEL_DEG) * PIXEL_DEG
    east = math.ceil(max(box[2] for box in boxes) / PIXEL_DEG) * PIXEL_DEG
    north = math.ceil(max(box[3] for box in boxes) / PIXEL_DEG) * PIXEL_DEG
    return west, south, east, north


def zonal_fractions(zones_path: Path, flood_path: Path, built_path: Path, north: float):
    """({region id: flood-zone share}, {region id: built-up share}).

    Read a block of rows at a time: the grids are about 100 million pixels.
    """
    zones = GDALRaster(str(zones_path)).bands[0]
    flood = GDALRaster(str(flood_path)).bands[0]
    built = GDALRaster(str(built_path)).bands[0]

    size = int(zones.max) + 1
    pixels = np.zeros(size)
    flooded = np.zeros(size)
    built_share = np.zeros(size)

    for top in range(0, zones.height, ROWS_PER_BLOCK):
        rows = min(ROWS_PER_BLOCK, zones.height - top)
        window = {"offset": (0, top), "size": (zones.width, rows)}
        zone = zones.data(**window).ravel()
        inside = zone > 0
        if not inside.any():
            continue
        zone = zone[inside]

        pixels += np.bincount(zone, minlength=size)
        flooded += np.bincount(zone, weights=flood.data(**window).ravel()[inside] > 0, minlength=size)

        # The area of a built-up cell shrinks with the cosine of latitude.
        latitudes = north - (top + np.arange(rows) + 0.5) * PIXEL_DEG
        cell_m2 = (BUILT_UP_CELL_DEG * METRES_PER_DEGREE) ** 2 * np.cos(np.radians(latitudes))
        share = built.data(**window).astype(float) / cell_m2[:, None]
        built_share += np.bincount(
            zone, weights=np.clip(share.ravel()[inside], 0.0, 1.0), minlength=size
        )

    covered = np.flatnonzero(pixels)
    return (
        {int(i): float(flooded[i] / pixels[i]) for i in covered},
        {int(i): float(built_share[i] / pixels[i]) for i in covered},
    )


class Command(BaseCommand):
    help = "Ingest river distance, flood-zone share and built-up share per Region."

    def handle(self, *args, **options):
        regions = list(Region.objects.order_by("level", "name"))
        if not regions:
            raise CommandError("No regions in the database — run `seed_regions` first.")
        if not GeospatialFeature.objects.exists():
            raise CommandError("No terrain rows to update — run `seed_terrain` first.")

        self.stdout.write("Downloading source maps (skipped when already cached)")
        waterways = download(WATERWAYS_URL, DRAINAGE_DIR / "hotosm_ken_waterways_osm_gpkg.zip")
        tiles = [download(FLOOD_URL + name, DRAINAGE_DIR / name) for name in FLOOD_TILES]
        built_zip = download(BUILT_UP_URL, DRAINAGE_DIR / f"{BUILT_UP_NAME}.zip")

        west, south, east, north = extent = grid_extent(regions)
        grid = ["-te", *map(str, extent), "-tr", str(PIXEL_DEG), str(PIXEL_DEG)]

        self.stdout.write("Putting the flood and built-up maps on one 90 m grid")
        flood_path = DRAINAGE_DIR / "flood_100yr_kenya.tif"
        built_path = DRAINAGE_DIR / "built_up_kenya.tif"
        compress = ["-co", "COMPRESS=DEFLATE", "-co", "TILED=YES", "-overwrite"]
        run("gdalwarp", "-q", *grid, *compress, *map(str, tiles), str(flood_path))
        run(
            "gdalwarp", "-q", *grid, *compress, "-r", "near",
            f"/vsizip/{built_zip}/{BUILT_UP_NAME}.tif", str(built_path),
        )  # fmt: skip

        flood_share: dict[int, float] = {}
        built_share: dict[int, float] = {}
        # Counties and sub-counties overlap, so each level gets its own zones.
        for level in Region.Level.values:
            members = [region for region in regions if region.level == level]
            if not members:
                continue
            self.stdout.write(f"Counting pixels in {len(members)} {level} regions")
            shapes = DRAINAGE_DIR / f"zones_{level}.geojson"
            shapes.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "properties": {"id": region.pk},
                                "geometry": json.loads(region.geom.geojson),
                            }
                            for region in members
                        ],
                    }
                )
            )
            zones_path = DRAINAGE_DIR / f"zones_{level}.tif"
            zones_path.unlink(missing_ok=True)
            run(
                "gdal_rasterize", "-q", "-a", "id", "-ot", "UInt16", "-init", "0", *grid,
                "-co", "COMPRESS=DEFLATE", str(shapes), str(zones_path),
            )  # fmt: skip
            flood, built = zonal_fractions(zones_path, flood_path, built_path, north)
            flood_share.update(flood)
            built_share.update(built)

        self.stdout.write("Measuring distance to the nearest river")
        rivers = self._rivers(waterways)

        updated = 0
        rows = []
        with transaction.atomic():
            for region in regions:
                point = representative_point(region)
                location = region.geom.point_on_surface
                location.transform(METRIC_SRID)
                values = {
                    "distance_to_river": round(location.distance(rivers), 1),
                    "flood_zone_fraction": _rounded(flood_share.get(region.pk)),
                    "built_up_fraction": _rounded(built_share.get(region.pk)),
                }
                updated += GeospatialFeature.objects.filter(region=region).update(**values)
                rows.append((str(region), values, point))

        self._report(updated, len(regions), rows)

    def _rivers(self, waterways: Path):
        """Every OpenStreetMap river line in Kenya as one geometry, in metres."""
        extract = DRAINAGE_DIR / "rivers.geojson"
        extract.unlink(missing_ok=True)
        run(
            "ogr2ogr", "-f", "GeoJSON", "-t_srs", f"EPSG:{METRIC_SRID}",
            # Rivers only: streams, drains and ditches are mapped too unevenly
            # across the country to compare one region with another.
            "-where", "waterway = 'river'", "-select", "waterway",
            str(extract), f"/vsizip/{waterways}/waterways.gpkg", "waterways",
        )  # fmt: skip
        lines = []
        for feature in DataSource(str(extract))[0]:
            geometry = feature.geom.geos
            if geometry.geom_type == "LineString":
                lines.append(geometry)
            elif geometry.geom_type == "MultiLineString":
                lines.extend(geometry)
        if not lines:
            raise CommandError("No rivers found in the waterways file.")
        self.stdout.write(f"  {len(lines)} river lines")
        return MultiLineString(lines, srid=METRIC_SRID)

    def _report(self, updated, total, rows):
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Updated: {updated} of {total} regions"))
        for field, label, scale, unit in (
            ("flood_zone_fraction", "Most in the flood zone", 100, "%"),
            ("built_up_fraction", "Most built up", 100, "%"),
            ("distance_to_river", "Furthest from a river", 0.001, " km"),
        ):
            known = [(name, values[field]) for name, values, _point in rows if values[field] is not None]
            if known:
                name, value = max(known, key=lambda item: item[1])
                self.stdout.write(f"{label}: {name} at {value * scale:.1f}{unit}")


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 5)
