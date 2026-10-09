"""Mapped flood extents from the Global Flood Database, to check the labels against.

The training labels place each Dartmouth Flood Observatory event as a
circle (see dfo.py). The Global Flood Database mapped the water of some of
the same events from MODIS at 250 m, and keeps the DFO event id, so the two
can be compared event by event: which counties under a circle held mapped
flood water, and which counties outside it did.

Source: Tellman et al. (2021), *Satellite imaging reveals increased
proportion of population exposed to floods*, Nature 596. On Earth Engine as
GLOBAL_FLOOD_DB/MODIS_EVENTS/V1: 913 events, 2000 to 2018.

It covers a minority of the events that reach Kenya, so it measures how
good the circles are; it cannot replace them. MODIS also misses water under
cloud, under trees and in narrow or urban channels, so "no water mapped" is
weaker evidence than "water mapped".

Needs Earth Engine: see earth_engine_rain.py for the setup.
"""

from __future__ import annotations

import json
from pathlib import Path

from geodata.services import earth_engine_rain

DATASET = "GLOBAL_FLOOD_DB/MODIS_EVENTS/V1"
SCALE_M = 250  # the dataset's native pixel

# Kenya, padded: only used to list which events to look at.
KENYA_BOX = (33.5, -5.0, 42.3, 5.5)


def mapped_event_ids() -> set[int]:
    """DFO ids of the events the database mapped in or near Kenya."""
    ee = earth_engine_rain._ee()
    try:
        ids = (
            ee.ImageCollection(DATASET)
            .filterBounds(ee.Geometry.Rectangle(list(KENYA_BOX)))
            .aggregate_array("id")
            .getInfo()
        )
    except Exception as exc:  # ee raises its own exception types
        raise earth_engine_rain.EarthEngineError(f"Earth Engine request failed: {exc}") from exc
    return {int(event_id) for event_id in ids}


def flooded_km2(regions, event_ids, cache_path: Path, log=print) -> dict[int, dict[str, float]]:
    """{event id: {region name: km² of mapped flood water}} for `event_ids`.

    Flood water is a pixel the database marks as flooded that is not
    permanent water. Each event is written to `cache_path` as it arrives, so
    a re-run is offline.
    """
    cached = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    missing = [event_id for event_id in event_ids if str(event_id) not in cached]
    if missing:
        ee = earth_engine_rain._ee()
        collection = ee.FeatureCollection(
            [
                ee.Feature(
                    ee.Geometry(
                        json.loads(
                            region.geom.simplify(
                                earth_engine_rain.SIMPLIFY_DEG, preserve_topology=True
                            ).geojson
                        )
                    ),
                    {"region": region.name},
                )
                for region in regions
            ]
        )
        events = ee.ImageCollection(DATASET)
        log(f"Earth Engine: mapped flood water for {len(missing)} events")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        for event_id in missing:
            image = ee.Image(events.filter(ee.Filter.eq("id", event_id)).first())
            water = image.select("flooded").eq(1).And(image.select("jrc_perm_water").eq(0))
            area = water.multiply(ee.Image.pixelArea()).divide(1e6).rename("km2")
            try:
                features = area.reduceRegions(
                    collection=collection, reducer=ee.Reducer.sum(), scale=SCALE_M, tileScale=4
                ).getInfo()["features"]
            except Exception as exc:
                raise earth_engine_rain.EarthEngineError(
                    f"Earth Engine request for event {event_id} failed: {exc}"
                ) from exc
            cached[str(event_id)] = {
                feature["properties"]["region"]: round(feature["properties"].get("sum") or 0.0, 2)
                for feature in features
            }
            cache_path.write_text(json.dumps(cached))
    return {int(event_id): cached[str(event_id)] for event_id in event_ids}


def compare_with_circles(
    circles: dict[int, set[str]], mapped: dict[int, dict[str, float]], min_km2: float
) -> list[dict]:
    """Per event: regions under its circle, and regions with mapped flood water.

    `circles` is {event id: names under the circle}; `mapped` is the result
    of `flooded_km2`. A region counts as mapped wet at `min_km2` or more.
    """
    rows = []
    for event_id in sorted(mapped):
        circle = circles.get(event_id, set())
        wet = {name for name, km2 in mapped[event_id].items() if km2 >= min_km2}
        rows.append(
            {
                "event": event_id,
                "circle": len(circle),
                "circle_and_wet": len(circle & wet),
                "circle_only": len(circle - wet),
                "wet_only": len(wet - circle),
                "mapped_km2": round(sum(mapped[event_id].values()), 1),
            }
        )
    return rows
