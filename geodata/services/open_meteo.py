"""Thin client for the two Open-Meteo endpoints FloodScout ingests from.

Open-Meteo is used because it needs no API key, no registration and no
credentials file — unlike Earth Engine, which is the long-term source for
these same variables (see `geodata/services/earth_engine.py` and Sprint 2).
This module exists so the walking skeleton can run on *real* elevation and
*real* rainfall today, on any machine, without a credentials dance.

Sources, both free for non-commercial use and both citable:

  Elevation   https://api.open-meteo.com/v1/elevation
              Copernicus DEM GLO-90 (90 m), the same family of digital
              elevation model as SRTM.
  Rainfall    https://api.open-meteo.com/v1/forecast  with `past_days`
              Daily `precipitation_sum` in mm, from the operational
              analysis — measured/analysed values, not a forecast, for
              dates already past.

Nothing here interprets the numbers. Turning them into a risk score is
`riskengine/ml/infer.py`'s job, and it is still a placeholder.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo accepts many coordinates per call. Kept well under the
# documented ceilings so one slow county cannot fail the whole run.
MAX_ELEVATION_POINTS = 100
MAX_FORECAST_LOCATIONS = 25

REQUEST_TIMEOUT_SECONDS = 60
USER_AGENT = "FloodScout/1.0 (university project; contact via repo)"


class OpenMeteoError(RuntimeError):
    """Raised when the upstream API cannot be reached or returns nonsense."""


@dataclass(frozen=True)
class Point:
    """A longitude/latitude pair. Ordered lon/lat to match GEOS, not the API."""

    lon: float
    lat: float


def _get_json(url: str, params: dict[str, str]):
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OpenMeteoError(f"Open-Meteo request failed ({url}): {exc}") from exc


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def fetch_elevations(points: list[Point]) -> list[float]:
    """Metres above sea level for each point, in the order given."""
    if not points:
        return []

    elevations: list[float] = []
    for batch in _chunks(points, MAX_ELEVATION_POINTS):
        payload = _get_json(
            ELEVATION_URL,
            {
                "latitude": ",".join(f"{p.lat:.5f}" for p in batch),
                "longitude": ",".join(f"{p.lon:.5f}" for p in batch),
            },
        )
        values = payload.get("elevation")
        if not isinstance(values, list) or len(values) != len(batch):
            raise OpenMeteoError(
                f"Expected {len(batch)} elevations, got {values!r}"
            )
        elevations.extend(float(v) for v in values)
    return elevations


def fetch_daily_rainfall(
    points: list[Point], past_days: int
) -> list[dict[dt.date, float]]:
    """Daily rainfall in mm per point: {date: mm}, for the last `past_days`.

    `forecast_days=0` keeps the response to dates that have already
    happened, so nothing stored is a prediction.
    """
    if not points:
        return []

    series: list[dict[dt.date, float]] = []
    for batch in _chunks(points, MAX_FORECAST_LOCATIONS):
        payload = _get_json(
            FORECAST_URL,
            {
                "latitude": ",".join(f"{p.lat:.5f}" for p in batch),
                "longitude": ",".join(f"{p.lon:.5f}" for p in batch),
                "daily": "precipitation_sum",
                "past_days": str(past_days),
                "forecast_days": "0",
                "timezone": "Africa/Nairobi",
            },
        )
        # A single location comes back as an object, several as a list.
        locations = payload if isinstance(payload, list) else [payload]
        if len(locations) != len(batch):
            raise OpenMeteoError(
                f"Expected {len(batch)} locations, got {len(locations)}"
            )
        for location in locations:
            daily = location.get("daily") or {}
            dates = daily.get("time") or []
            totals = daily.get("precipitation_sum") or []
            if len(dates) != len(totals):
                raise OpenMeteoError("Mismatched date/precipitation arrays")
            series.append(
                {
                    dt.date.fromisoformat(day): float(mm)
                    # A null means the model had no value for that day; it is
                    # not the same as zero rain, so it is dropped rather than
                    # stored as 0.0.
                    for day, mm in zip(dates, totals)
                    if mm is not None
                }
            )
    return series
