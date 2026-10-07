"""County-mean daily rainfall from Google Earth Engine, for the training set.

Open-Meteo serves one point per request and counts a long history as many
calls (roughly one per two weeks of data per point), so averaging several
points per county over 26 years would take over a week of its free daily
quota. Earth Engine averages over the whole county polygon on Google's side
and returns the result in a few dozen requests.

The dataset is ERA5-Land daily aggregates: the same reanalysis family as
Open-Meteo's archive, at 0.1 degrees (about 11 km), so a model trained on
it is scored on rainfall from the same kind of source. `rain_points` in the
training metadata tells scoring to average several Open-Meteo points to
approximate the county mean.

Setup, once per machine (needs the Earth Engine project registered for
FloodScout, EE_PROJECT_ID in .env):

    pip install earthengine-api
    earthengine authenticate

In Docker, run `earthengine authenticate --auth_mode=notebook` inside the
web container; docker-compose.yml keeps the credential in .secrets/.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

DATASET = "ECMWF/ERA5_LAND/DAILY_AGGR"
BAND = "total_precipitation_sum"  # metres of water
SCALE_M = 11132  # the dataset's native 0.1-degree pixel
# Simplify county outlines before upload. 0.005 degrees is about 500 m, far
# finer than the pixels, and keeps the request small.
SIMPLIFY_DEG = 0.005

# Rainfall in the training set is averaged over this many points per county
# at scoring time (see geodata/services/sampling.py).
SCORING_POINTS = 5


class EarthEngineError(RuntimeError):
    """Raised when Earth Engine is not installed, not authorised, or fails."""


def _ee():
    try:
        import ee  # noqa: PLC0415  (optional dependency)
    except ImportError as exc:
        raise EarthEngineError(
            "earthengine-api is not installed. Run `pip install earthengine-api` "
            "and `earthengine authenticate`, or use --rain-source open-meteo."
        ) from exc
    project = os.environ.get("EE_PROJECT_ID")
    try:
        ee.Initialize(project=project) if project else ee.Initialize()
    except Exception as exc:  # ee raises its own exception types
        raise EarthEngineError(
            f"Earth Engine could not start ({exc}). Run `earthengine authenticate` "
            "and set EE_PROJECT_ID in .env."
        ) from exc
    return ee


def quarters(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    """Calendar quarters covering start to end, clipped to them.

    One quarter of 47 counties is about 4,300 values, under Earth Engine's
    5,000-element limit for a single request.
    """
    spans = []
    year, quarter = start.year, (start.month - 1) // 3
    while dt.date(year, quarter * 3 + 1, 1) <= end:
        first = dt.date(year, quarter * 3 + 1, 1)
        next_year, next_quarter = (year + 1, 0) if quarter == 3 else (year, quarter + 1)
        last = dt.date(next_year, next_quarter * 3 + 1, 1) - dt.timedelta(days=1)
        spans.append((max(first, start), min(last, end)))
        year, quarter = next_year, next_quarter
    return spans


def parse_features(features: list[dict]) -> dict[str, dict[dt.date, float]]:
    """{county: {date: mm}} from Earth Engine's feature list."""
    out: dict[str, dict[dt.date, float]] = {}
    for feature in features:
        props = feature.get("properties", {})
        metres = props.get("mean")
        if metres is None:
            continue
        out.setdefault(props["county"], {})[dt.date.fromisoformat(props["date"])] = (
            float(metres) * 1000.0
        )
    return out


def _fetch_span(ee, collection, first: dt.date, last: dt.date) -> dict[str, dict[dt.date, float]]:
    images = (
        ee.ImageCollection(DATASET)
        .filterDate(first.isoformat(), (last + dt.timedelta(days=1)).isoformat())
        .select(BAND)
    )

    def per_image(image):
        day = image.date().format("YYYY-MM-dd")
        stats = image.reduceRegions(
            collection=collection, reducer=ee.Reducer.mean(), scale=SCALE_M, tileScale=4
        )
        return stats.map(
            lambda f: ee.Feature(None, {"county": f.get("county"), "date": day, "mean": f.get("mean")})
        )

    return parse_features(images.map(per_image).flatten().getInfo()["features"])


def fetch_county_rainfall(counties, start: dt.date, end: dt.date, cache_dir: Path, log=print):
    """{county name: {date: mm}} averaged over each county, start to end inclusive.

    Each quarter is written to `cache_dir` as it arrives, so a run that is
    interrupted picks up where it stopped and a re-run is offline.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    spans = quarters(start, end)
    rainfall: dict[str, dict[dt.date, float]] = {county.name: {} for county in counties}
    missing = []
    for first, last in spans:
        path = cache_dir / f"{first}_{last}.json"
        if path.exists():
            cached = json.loads(path.read_text())
            for name, series in cached.items():
                rainfall.setdefault(name, {}).update(
                    {dt.date.fromisoformat(day): mm for day, mm in series.items()}
                )
        else:
            missing.append((first, last, path))

    if missing:
        ee = _ee()
        collection = ee.FeatureCollection(
            [
                ee.Feature(
                    ee.Geometry(json.loads(county.geom.simplify(SIMPLIFY_DEG, preserve_topology=True).geojson)),
                    {"county": county.name},
                )
                for county in counties
            ]
        )
        log(f"Earth Engine: fetching {len(missing)} of {len(spans)} quarters of county-mean rainfall")
        for number, (first, last, path) in enumerate(missing, start=1):
            try:
                span = _fetch_span(ee, collection, first, last)
            except Exception as exc:
                raise EarthEngineError(f"Earth Engine request for {first} to {last} failed: {exc}") from exc
            path.write_text(
                json.dumps({name: {d.isoformat(): mm for d, mm in s.items()} for name, s in span.items()})
            )
            for name, series in span.items():
                rainfall.setdefault(name, {}).update(series)
            if number % 10 == 0 or number == len(missing):
                log(f"  {number}/{len(missing)} quarters")
    return rainfall
