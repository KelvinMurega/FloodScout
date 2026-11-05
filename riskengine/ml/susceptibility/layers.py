"""The susceptibility features as one Earth Engine image.

Definitions are fixed by SUSCEPTIBILITY_PROTOCOL.md, section 2. Every band is
brought onto the 250 m grid of the flood event being sampled. MERIT Hydro is
90 m, so a 250 m pixel takes the mean of what falls inside it, except upstream
area, which takes the maximum: the mean would dilute a river with the slopes
beside it.
"""

from __future__ import annotations

import math

MERIT = "MERIT/Hydro/v1_0_1"
GFD = "GLOBAL_FLOOD_DB/MODIS_EVENTS/V1"
SURFACE_WATER = "JRC/GSW1_4/GlobalSurfaceWater"
FLOOD_HAZARD = "JRC/CEMS_GLOFAS/FloodHazard/v1"
FLOOD_HAZARD_METRES = 92.77

# 90 m pixels inside a 250 m pixel, with room to spare at the tile edges.
MAX_PIXELS_INSIDE = 64
# Pixels searched for the nearest river, about 64 km at 250 m.
RIVER_SEARCH_PIXELS = 256
LAKE_GRID_METRES = 250
# A feature Earth Engine has no value for. JSON cannot carry NaN.
MISSING = -9999


def _onto(image, reducer, grid):
    return image.reduceResolution(reducer, maxPixels=MAX_PIXELS_INSIDE).reproject(grid)


def feature_image(ee, grid, slope_floor_degrees, river_upstream_km2):
    """The seven protocol features, on `grid`, one band each."""
    merit = ee.Image(MERIT)
    elv = merit.select("elv")
    mean = ee.Reducer.mean()

    hand = _onto(merit.select("hnd"), mean, grid).rename("hand")
    elev = _onto(elv, mean, grid).rename("elev")
    slope = _onto(ee.Terrain.slope(elv), mean, grid).rename("slope")
    upa = _onto(merit.select("upa"), ee.Reducer.max(), grid)

    radians = slope.max(slope_floor_degrees).multiply(math.pi / 180)
    twi = upa.multiply(1e6).divide(radians.tan()).log().rename("twi")
    log_upa = upa.add(1).log10().rename("log_upa")

    rivers = upa.gte(river_upstream_km2).unmask(0)
    pixels_away = rivers.fastDistanceTransform(
        RIVER_SEARCH_PIXELS, "pixels", "squared_euclidean"
    ).sqrt()
    dist_river = pixels_away.multiply(ee.Image.pixelArea().sqrt()).reproject(grid)

    return ee.Image.cat(
        hand,
        twi,
        log_upa,
        dist_river.rename("dist_river"),
        elev,
        slope,
        flood_zone(ee, grid),
    )


def test_image(ee, grid, sub_counties, derived, permanent_water_occurrence, lakes):
    """Everything the UNOSAT test needs for a pixel except its label.

    The seven features, the pixel's sub-county number (0 outside Kenya), the
    permanent water mask, the lake flag and the pixel's area in m². Every
    band is unmasked, so a pixel with a missing feature reads as MISSING.
    """
    features = feature_image(
        ee, grid, derived["slope_floor_degrees"], derived["river_upstream_km2"]
    ).unmask(MISSING, False)
    occurrence = ee.Image(SURFACE_WATER).select("occurrence").unmask(0)
    permanent = occurrence.gte(permanent_water_occurrence).rename("permanent")
    lake = near_lake(ee, lakes["occurrence"], lakes["min_area_km2"], lakes["buffer_metres"])
    return ee.Image.cat(
        features.toFloat(),
        sub_counties.unmask(0).reproject(grid).toFloat(),
        permanent.unmask(0).reproject(grid).toFloat(),
        lake.unmask(0).reproject(grid).toFloat(),
        ee.Image.pixelArea().reproject(grid).rename("area_m2").toFloat(),
    )


def near_lake(ee, occurrence, min_area_km2, buffer_metres):
    """1 close to a large water body, else 0. For the sensitivity score only."""
    grid = ee.Projection("EPSG:4326").atScale(LAKE_GRID_METRES)
    wet = ee.Image(SURFACE_WATER).select("occurrence").unmask(0).gte(occurrence).reproject(grid)
    pixels = math.ceil(min_area_km2 * 1e6 / LAKE_GRID_METRES**2)
    large = wet.selfMask().connectedPixelCount(pixels + 1, True).gte(pixels).unmask(0)
    away = large.fastDistanceTransform(32, "pixels", "squared_euclidean").sqrt()
    return away.multiply(LAKE_GRID_METRES).lte(buffer_metres).rename("near_lake").reproject(grid)


def flood_zone(ee, grid):
    """1 where at least half the pixel is inside the 100-year extent, else 0."""
    depth = (
        ee.ImageCollection(FLOOD_HAZARD)
        .filter(ee.Filter.eq("return_period", 100))
        .mosaic()
        .select("depth")
    )
    native = ee.Projection("EPSG:4326").atScale(FLOOD_HAZARD_METRES)
    inside = depth.gt(0).unmask(0).setDefaultProjection(native)
    return _onto(inside, ee.Reducer.mean(), grid).gte(0.5).rename("flood_zone_100y")
