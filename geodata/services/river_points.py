"""Where each region's largest river runs, for reading its discharge.

GloFAS gives river discharge on a 5 km grid, and a river is one cell wide on
it: a region's representative point usually sits on dry land and reads
nothing. So each region is given a point on its largest river: the HydroRIVERS
reach inside it with the highest long-term mean discharge (`DIS_AV_CMS`),
read through Earth Engine's copy of the dataset (FreeFlowingRivers).

Largest by discharge, not by drainage area. Garissa drains a larger area
through the Lagh Dera, which is dry most of the year, than through the Tana,
which is what floods it.

HydroRIVERS also draws lines through lakes, carrying everything that drains
into them, so a reach whose point lies in permanent water is passed over
for the next largest. Without that, Kisumu would be read in Winam Gulf.

`open_meteo.river_cell` then moves the point onto the GloFAS cell that
carries the river.
"""

from __future__ import annotations

import json

from geodata.services.earth_engine_rain import SIMPLIFY_DEG, EarthEngineError, _ee

RIVERS = "WWF/HydroSHEDS/v1/FreeFlowingRivers"
SURFACE_WATER = "JRC/GSW1_4/GlobalSurfaceWater"  # band occurrence: % of months with water, 1984-2021
# A point counts as lake when water was there in more than this share of
# months. Rivers are narrower than the 30 m pixels around a reach's line, so
# the test is made at the line's own vertex.
LAKE_OCCURRENCE = 50
# How many of a region's largest reaches to consider, then how many more
# when all of those are in a lake. The lake reaches of Winam Gulf fill more
# than the first five for Kisumu, and Lake Turkana's the first 25 for
# Marsabit.
CANDIDATE_REACHES = (25, 300)


def middle_vertex(geometry: dict) -> tuple[float, float] | None:
    """A vertex from the middle of a (multi)line, which lies on the line itself."""
    kind, coords = geometry.get("type"), geometry.get("coordinates") or []
    if kind == "LineString":
        lines = [coords]
    elif kind == "MultiLineString":
        lines = coords
    elif kind == "GeometryCollection":
        points = [middle_vertex(part) for part in geometry.get("geometries", [])]
        points = [point for point in points if point]
        return points[0] if points else None
    else:
        return None
    longest = max(lines, key=len, default=[])
    if not longest:
        return None
    lon, lat = longest[len(longest) // 2][:2]
    return float(lon), float(lat)


def choose_reach(candidates: list[dict]) -> dict | None:
    """The first candidate, largest first, whose point is not in a lake."""
    for candidate in candidates:
        if candidate["point"] and (candidate["water"] or 0) <= LAKE_OCCURRENCE:
            return candidate
    return None


def outlet_points(regions, log=print) -> dict[str, dict]:
    """{region name: {"lon", "lat", "mean_m3s", "upland_km2"}} on its largest river."""
    ee = _ee()
    rivers = ee.FeatureCollection(RIVERS)
    water = ee.Image(SURFACE_WATER).select("occurrence").unmask(0)
    out = {}
    for region in regions:
        geometry = ee.Geometry(
            json.loads(region.geom.simplify(SIMPLIFY_DEG, preserve_topology=True).geojson)
        )
        for limit in CANDIDATE_REACHES:
            chosen = _largest_reach(ee, rivers, water, geometry, limit, region.name)
            if chosen:
                break
        if chosen is None:
            log(f"  {region.name}: no river found")
            continue
        out[region.name] = {
            "lon": chosen["point"][0],
            "lat": chosen["point"][1],
            "mean_m3s": chosen["mean_m3s"],
            "upland_km2": chosen["upland_km2"],
        }
    return out


def _largest_reach(ee, rivers, water, geometry, limit: int, name: str) -> dict | None:
    """The largest of the region's `limit` largest reaches that is not in a lake."""
    reaches = rivers.filterBounds(geometry).sort("DIS_AV_CMS", False).limit(limit)
    try:
        found = reaches.map(
            lambda reach: ee.Feature(
                reach.geometry().intersection(geometry, ee.ErrorMargin(10)),
                {"mean_m3s": reach.get("DIS_AV_CMS"), "upland_km2": reach.get("UPLAND_SKM")},
            )
        ).getInfo()["features"]
    except Exception as exc:
        raise EarthEngineError(f"Earth Engine could not read the rivers of {name}: {exc}") from exc

    candidates = []
    for feature in found:
        point = middle_vertex(feature.get("geometry") or {})
        candidates.append({**feature["properties"], "point": point, "water": None})
    points = [candidate["point"] for candidate in candidates if candidate["point"]]
    if points:
        sampled = water.reduceRegions(
            collection=ee.FeatureCollection([ee.Feature(ee.Geometry.Point(list(p))) for p in points]),
            reducer=ee.Reducer.first(),
            scale=30,
        ).aggregate_array("first").getInfo()
        for candidate, occurrence in zip(
            (c for c in candidates if c["point"]), sampled, strict=True
        ):
            candidate["water"] = occurrence

    return choose_reach(candidates)
