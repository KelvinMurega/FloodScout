"""Draw the susceptibility sample from the Global Flood Database.

Follows SUSCEPTIBILITY_PROTOCOL.md, section 1. Earth Engine does the raster
work; the helpers at the top are plain Python so the rules can be tested
without it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from . import layers

CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "susceptibility_v1.toml"
DRY, FLOODED = 0, 1
# Pixels that are neither: cloud, permanent water, or outside the event's area.
UNUSED = 2
PAGE_ROWS = 2500


def load_config(path=CONFIG_PATH):
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def touched_counties(flooded_by_county):
    """The counties an event has at least one flooded pixel in."""
    return sorted(county for county, pixels in flooded_by_county.items() if pixels >= 1)


def is_usable(flooded_by_county, min_flooded_pixels):
    return sum(flooded_by_county.values()) >= min_flooded_pixels


def sampling_weight(pixels_in_area, pixels_drawn):
    """How many pixels of the class one drawn pixel stands for."""
    if pixels_drawn < 1:
        raise ValueError("No pixels drawn for this class.")
    return pixels_in_area / pixels_drawn


def county_image(ee, counties):
    """Each pixel's county as a number. `counties` is [(number, geojson), ...]."""
    features = [ee.Feature(geometry, {"county": number}) for number, geometry in counties]
    return (
        ee.FeatureCollection(features)
        .reduceToImage(["county"], ee.Reducer.first())
        .toInt()
        .rename("county")
    )


def _grid(event):
    return event.select("flooded").projection()


def _masks(ee, event, permanent_water_occurrence):
    """Flooded pixels with permanent water removed, and that water."""
    occurrence = ee.Image(layers.SURFACE_WATER).select("occurrence").unmask(0)
    permanent = event.select("jrc_perm_water").eq(1).Or(occurrence.gte(permanent_water_occurrence))
    return event.select("flooded").eq(1).And(permanent.Not()), permanent


def flooded_by_county(ee, event, county, region, permanent_water_occurrence):
    """{county number: flooded pixels} for one event."""
    flooded, _ = _masks(ee, event, permanent_water_occurrence)
    grid = _grid(event)
    result = (
        flooded.selfMask()
        .rename("flooded")
        .addBands(county.reproject(grid))
        .reduceRegion(
            ee.Reducer.count().group(groupField=1, groupName="county"),
            geometry=region,
            crs=grid,
            maxPixels=1e10,
            tileScale=4,
        )
        .getInfo()
    )
    return {int(row["county"]): int(row["count"]) for row in result["groups"]}


def draw(ee, event, county, touched, data, derived):
    """One event's sample, not yet computed, and {class: pixels in the event's area}."""
    grid = _grid(event)
    flooded, permanent = _masks(ee, event, data["permanent_water_occurrence"])
    clear = event.select("clear_perc").gte(data["min_clear_perc"])
    dry = event.select("flooded").eq(0).And(permanent.Not()).And(clear)
    on_grid = county.reproject(grid)
    in_area = on_grid.remap(touched, [1] * len(touched), 0).eq(1)

    features = layers.feature_image(
        ee, grid, derived["slope_floor_degrees"], derived["river_upstream_km2"]
    )
    complete = features.reduce(ee.Reducer.count()).eq(len(data["features"]))
    label = ee.Image(UNUSED).where(dry, DRY).where(flooded, FLOODED).rename("label").toInt()
    label = label.updateMask(label.neq(UNUSED)).updateMask(in_area).updateMask(complete)

    region = event.geometry()
    counts = (
        label.reduceRegion(
            ee.Reducer.frequencyHistogram(),
            geometry=region,
            crs=grid,
            maxPixels=1e10,
            tileScale=4,
        )
        .get("label")
        .getInfo()
    )
    totals = {int(float(value)): int(pixels) for value, pixels in counts.items()}

    table = (
        label.addBands(features)
        .addBands(on_grid)
        .addBands(ee.Image.pixelLonLat())
        .stratifiedSample(
            numPoints=data["dry_pixels_per_event"],
            classBand="label",
            classValues=[DRY, FLOODED],
            classPoints=[data["dry_pixels_per_event"], data["flooded_pixels_per_event"]],
            region=region,
            projection=grid,
            seed=data["sampling_seed"],
            dropNulls=True,
            tileScale=8,
            geometries=True,  # an export refuses rows with no geometry
        )
    )
    return table, totals


def near_lake_flags(ee, rows, settings):
    """1 or 0 for each row, in order, read from Earth Engine a page at a time."""
    image = layers.near_lake(
        ee, settings["occurrence"], settings["min_area_km2"], settings["buffer_metres"]
    )
    flags = {}
    for start in range(0, len(rows), PAGE_ROWS):
        points = [
            ee.Feature(ee.Geometry.Point([row["lon"], row["lat"]]), {"row": start + offset})
            for offset, row in enumerate(rows[start : start + PAGE_ROWS])
        ]
        found = image.reduceRegions(
            ee.FeatureCollection(points), ee.Reducer.first(), scale=layers.LAKE_GRID_METRES
        ).getInfo()
        for feature in found["features"]:
            flags[feature["properties"]["row"]] = int(feature["properties"]["first"])
    return [flags[number] for number in range(len(rows))]


def read_rows(ee, asset):
    """Every row of a finished export, a page at a time: one request is capped at 5,000."""
    table = ee.FeatureCollection(asset)
    total = table.size().getInfo()
    rows = []
    for offset in range(0, total, PAGE_ROWS):
        page = table.toList(PAGE_ROWS, offset).getInfo()
        rows += [feature["properties"] for feature in page]
    return rows
