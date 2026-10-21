"""Build the dashboard's static map layers from the drainage downloads.

Two files, written to frontend/public/layers/ and served with the dashboard:

  rivers.geojson        Kenya's named rivers from OpenStreetMap, the longest
                        ones only, simplified for the web. Most of Kenya's
                        floods are rivers leaving their banks, so the map
                        draws them over the risk.
  flood_zone_100yr.png  The Copernicus GloFAS 100-year river flood map: the
  layers.json           ground a once-in-a-century flood would cover,
                        shaded by depth, reprojected to the map's Web
                        Mercator so it lies exactly on the basemap.
                        layers.json gives its corners and the sources.

Both come from the files `seed_drainage` downloads into
data/external/drainage/; this command downloads them too if they are not
there. Re-run it only to refresh the layers.

    python manage.py build_map_layers
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import numpy as np
from django.conf import settings
from django.contrib.gis.gdal import GDALRaster
from django.core.management.base import BaseCommand, CommandError

from .seed_drainage import (
    DRAINAGE_DIR,
    FLOOD_TILES,
    FLOOD_URL,
    WATERWAYS_URL,
    download,
    run,
)

OUT_DIR = Path(settings.BASE_DIR) / "frontend" / "public" / "layers"
WORK_DIR = DRAINAGE_DIR / "map_layers"

# Rivers whose named length in Kenya is under this are left out: thousands
# of short streams would bury the ones that flood. In degrees (~40 km).
MIN_RIVER_DEG = 0.36
# Douglas-Peucker tolerance for the river lines, degrees (~200 m): no
# visible change at the zooms the dashboard allows.
RIVER_SIMPLIFY_DEG = 0.002

# Pixel size of the flood-zone image, Web Mercator metres. The source is
# 90 m; the largest depth in each 300 m pixel is kept, so narrow floodplains
# survive the shrink.
FLOOD_PIXEL_M = 300

# Depth bands (m) and their colour, shallow to deep, as RGBA.
DEPTH_COLOURS = (
    (0.0, (125, 190, 240, 150)),
    (0.5, (55, 135, 215, 185)),
    (1.5, (20, 85, 175, 210)),
    (3.0, (8, 48, 120, 230)),
)

EARTH_RADIUS_M = 6378137.0


def mercator_to_lonlat(x: float, y: float) -> tuple[float, float]:
    lon = math.degrees(x / EARTH_RADIUS_M)
    lat = math.degrees(2 * math.atan(math.exp(y / EARTH_RADIUS_M)) - math.pi / 2)
    return lon, lat


def colour_depths(depth: np.ndarray) -> np.ndarray:
    """An RGBA image (4, rows, cols) of `depth`: clear where dry or no data."""
    rgba = np.zeros((4,) + depth.shape, dtype=np.uint8)
    wet = np.isfinite(depth) & (depth > 0) & (depth < 1e5)
    for floor, colour in DEPTH_COLOURS:
        band = wet & (depth >= floor)
        for channel, value in enumerate(colour):
            rgba[channel][band] = value
    return rgba


class Command(BaseCommand):
    help = "Build the rivers and 100-year flood-zone layers the dashboard draws."

    def handle(self, *args, **options):
        if shutil.which("ogr2ogr") is None or shutil.which("gdalwarp") is None:
            raise CommandError(
                "GDAL's command-line tools are needed; run this in the web container."
            )
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        WORK_DIR.mkdir(parents=True, exist_ok=True)

        rivers = self._rivers(
            download(WATERWAYS_URL, DRAINAGE_DIR / "hotosm_ken_waterways_osm_gpkg.zip")
        )
        tiles = [download(FLOOD_URL + name, DRAINAGE_DIR / name) for name in FLOOD_TILES]
        bounds = self._flood_zone(tiles)

        manifest = {
            "rivers": {
                "file": "rivers.geojson",
                "count": rivers,
                "source": "OpenStreetMap contributors (ODbL), via the HOT export of Kenya's waterways",
            },
            "flood_zone_100yr": {
                "file": "flood_zone_100yr.png",
                # [[south, west], [north, east]], as Leaflet takes them.
                "bounds": bounds,
                "depth_bands_m": [floor for floor, _colour in DEPTH_COLOURS],
                "source": "Copernicus Emergency Management Service, GloFAS river flood "
                "hazard map, 100-year return period (CC BY 4.0)",
            },
        }
        (OUT_DIR / "layers.json").write_text(json.dumps(manifest, indent=2) + "\n")
        self.stdout.write(self.style.SUCCESS(f"Layers written to {OUT_DIR}"))

    def _rivers(self, waterways: Path) -> int:
        out = OUT_DIR / "rivers.geojson"
        out.unlink(missing_ok=True)
        sql = (
            f"SELECT name, ST_SimplifyPreserveTopology(geom, {RIVER_SIMPLIFY_DEG}) AS geom "
            "FROM waterways WHERE waterway = 'river' AND name IN ("
            "SELECT name FROM waterways WHERE waterway = 'river' AND name IS NOT NULL "
            f"GROUP BY name HAVING SUM(ST_Length(geom)) >= {MIN_RIVER_DEG})"
        )
        run(
            "ogr2ogr", "-f", "GeoJSON", "-dialect", "sqlite", "-sql", sql,
            "-lco", "COORDINATE_PRECISION=4", "-lco", "RFC7946=YES",
            str(out), f"/vsizip/{waterways}/waterways.gpkg",
        )  # fmt: skip
        # A river mapped as a single point (OSM has a few) is not a line to draw.
        features = [
            feature
            for feature in json.loads(out.read_text())["features"]
            if (feature.get("geometry") or {}).get("type") in ("LineString", "MultiLineString")
        ]
        # Shorter files and stable output: just the name on each line.
        for feature in features:
            feature["properties"] = {"name": feature["properties"].get("name")}
        out.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        names = {feature["properties"]["name"] for feature in features}
        self.stdout.write(f"Rivers: {len(names)} named rivers, {len(features)} lines")
        return len(names)

    def _flood_zone(self, tiles: list[Path]) -> list[list[float]]:
        warped = WORK_DIR / "flood_100yr_3857.tif"
        coloured = WORK_DIR / "flood_100yr_rgba.tif"
        for path in (warped, coloured):
            path.unlink(missing_ok=True)
        run(
            "gdalwarp", "-t_srs", "EPSG:3857", "-tr", str(FLOOD_PIXEL_M), str(FLOOD_PIXEL_M),
            "-r", "max", "-te_srs", "EPSG:4326", "-te", "33.9", "-4.8", "41.95", "5.1",
            "-dstnodata", "0", "-co", "COMPRESS=DEFLATE",
            *[str(tile) for tile in tiles], str(warped),
        )  # fmt: skip

        source = GDALRaster(str(warped))
        depth = source.bands[0].data().astype(float)
        rgba = colour_depths(depth)
        target = GDALRaster(
            {
                "driver": "GTiff",
                "name": str(coloured),
                "srid": 3857,
                "width": source.width,
                "height": source.height,
                "origin": source.origin,
                "scale": source.scale,
                "datatype": 1,  # Byte
                "nr_of_bands": 4,
                "bands": [{"data": rgba[i]} for i in range(4)],
            }
        )
        del target  # flush to disk before gdal_translate reads it

        png = OUT_DIR / "flood_zone_100yr.png"
        png.unlink(missing_ok=True)
        run("gdal_translate", "-of", "PNG", "-co", "ZLEVEL=9", str(coloured), str(png))
        png.with_suffix(".png.aux.xml").unlink(missing_ok=True)

        west, north = source.origin
        east = west + source.width * source.scale[0]
        south = north + source.height * source.scale[1]
        (lon0, lat0), (lon1, lat1) = (
            mercator_to_lonlat(west, south),
            mercator_to_lonlat(east, north),
        )
        wet = int(np.count_nonzero(rgba[3]))
        self.stdout.write(
            f"Flood zone: {source.width}x{source.height} px at {FLOOD_PIXEL_M} m, "
            f"{wet * FLOOD_PIXEL_M**2 / 1e6:,.0f} km² inside the 100-year extent"
        )
        return [[round(lat0, 6), round(lon0, 6)], [round(lat1, 6), round(lon1, 6)]]
