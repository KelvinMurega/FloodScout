"""Where inside a region rainfall is measured, and how samples are combined.

A single point says little about a county the size of Turkana: a storm on
one side and dry ground on the other average to "some rain", but the point
sees only one of them. Averaging rainfall over several points spread across
the region measures what the region as a whole received.

The trained model records how its rainfall was measured (`rain_points` in
the artifact). Scoring must measure the same way, or the 7-day peak in
particular comes out on a different scale: a single point is spikier than
an average of five.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from django.contrib.gis.geos import Point as GEOSPoint

from geodata.services.open_meteo import Point

# Snap sample points to this grid and drop duplicates. ERA5-Land, the finest
# reanalysis Open-Meteo serves, is 0.1 degrees, so two points in one cell
# cost two API calls and return the same series.
GRID_DEG = 0.1

# Candidate grid laid over the region's bounding box, before filtering to
# points inside it.
CANDIDATE_GRID = 7

# A day counts only if at least this share of the sample points have it.
MIN_COVERAGE = 0.5


def _snap(value: float) -> float:
    return round(round(value / GRID_DEG) * GRID_DEG, 4)


def rain_sample_points(region, max_points: int) -> list[Point]:
    """Up to `max_points` distinct points inside `region`, spread across it.

    The first is always the representative point `seed_terrain` uses, so a
    model that reads one point (max_points=1) reads exactly the point it
    always did.
    """
    surface = region.geom.point_on_surface
    points = [Point(lon=surface.x, lat=surface.y)]
    if max_points <= 1:
        return points

    seen = {(_snap(surface.x), _snap(surface.y))}
    xmin, ymin, xmax, ymax = region.geom.extent
    inside = []
    for i in range(CANDIDATE_GRID):
        for j in range(CANDIDATE_GRID):
            lon = xmin + (i + 0.5) * (xmax - xmin) / CANDIDATE_GRID
            lat = ymin + (j + 0.5) * (ymax - ymin) / CANDIDATE_GRID
            key = (_snap(lon), _snap(lat))
            if key in seen or not region.geom.contains(GEOSPoint(lon, lat, srid=4326)):
                continue
            seen.add(key)
            inside.append(Point(lon=lon, lat=lat))

    # Greedy farthest-point selection, so the few points kept cover the
    # region instead of bunching in one corner.
    while inside and len(points) < max_points:
        farthest = max(
            inside,
            key=lambda c: min((c.lon - p.lon) ** 2 + (c.lat - p.lat) ** 2 for p in points),
        )
        points.append(farthest)
        inside.remove(farthest)
    return points


def mean_series(series: Sequence[dict[dt.date, float]]) -> dict[dt.date, float]:
    """Daily mean over the samples, for days at least MIN_COVERAGE of them have."""
    if len(series) == 1:
        return dict(series[0])
    totals: dict[dt.date, list[float]] = {}
    for one in series:
        for day, mm in one.items():
            totals.setdefault(day, []).append(mm)
    needed = MIN_COVERAGE * len(series)
    return {day: sum(values) / len(values) for day, values in totals.items() if len(values) >= needed}


def regional_rainfall(regions, fetch, max_points: int) -> list[dict[dt.date, float]]:
    """One daily series per region, averaged over its sample points.

    `fetch` takes a list of Points and returns one {date: mm} per point, in
    order, like the functions in open_meteo.py. All regions' points go in
    one call so batching stays the fetcher's business.
    """
    per_region = [rain_sample_points(region, max_points) for region in regions]
    flat = [point for points in per_region for point in points]
    fetched = fetch(flat)
    out, start = [], 0
    for points in per_region:
        out.append(mean_series(fetched[start : start + len(points)]))
        start += len(points)
    return out
