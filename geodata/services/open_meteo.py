"""Thin client for the two Open-Meteo endpoints FloodScout ingests from.

Open-Meteo is used because it needs no API key, no registration and no
credentials file — unlike Earth Engine, which is used only as an optional
source of county-mean rainfall for training (see
`geodata/services/earth_engine_rain.py`). This module lets the system run on
*real* elevation and *real* rainfall on any machine, without a credentials
dance.

Sources, both free for non-commercial use and both citable:

  Elevation   https://api.open-meteo.com/v1/elevation
              Copernicus DEM GLO-90 (90 m), the same family of digital
              elevation model as SRTM.
  Rainfall    https://api.open-meteo.com/v1/forecast  with `past_days`
              Daily `precipitation_sum` in mm, from the operational
              analysis — measured/analysed values, not a forecast, for
              dates already past.
  History     https://archive-api.open-meteo.com/v1/archive
              Daily `precipitation_sum` from the ERA5 reanalysis, 1940 on.
              The training record.
  Outlook     https://seasonal-api.open-meteo.com/v1/seasonal
              ECMWF seasonal ensemble, daily `precipitation_sum` per member
              for the months ahead. A forecast, and used only as one.

Nothing here interprets the numbers. Turning them into a risk score is
`riskengine/ml/infer.py`'s job.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SEASONAL_URL = "https://seasonal-api.open-meteo.com/v1/seasonal"

# Open-Meteo accepts many coordinates per call. Kept well under the
# documented ceilings so one slow county cannot fail the whole run.
MAX_ELEVATION_POINTS = 100
MAX_FORECAST_LOCATIONS = 25
# Decades of days, or 51 ensemble members, per location: keep batches small.
MAX_ARCHIVE_LOCATIONS = 5
MAX_SEASONAL_LOCATIONS = 10

REQUEST_TIMEOUT_SECONDS = 60

# The free tier counts every coordinate in a request as a call and caps them
# per minute, so a full run over all 337 regions (five terrain samples each)
# trips HTTP 429 part-way through. A 429 is waited out rather than failed:
# the wait honours Retry-After when sent, else a minute, which is the window
# the limit resets on.
RATE_LIMIT_RETRIES = 6
RATE_LIMIT_WAIT_SECONDS = 65
CONNECTION_RETRIES = 3
CONNECTION_WAIT_SECONDS = 20

# Indirection so tests can skip the wait.
_sleep = time.sleep
USER_AGENT = "FloodScout/1.0 (university project; contact via repo)"


class OpenMeteoError(RuntimeError):
    """Raised when the upstream API cannot be reached or returns nonsense."""


@dataclass(frozen=True)
class Point:
    """A longitude/latitude pair. Ordered lon/lat to match GEOS, not the API."""

    lon: float
    lat: float


def _retry_after_seconds(error: urllib.error.HTTPError) -> float:
    header = error.headers.get("Retry-After") if error.headers else None
    try:
        return max(1.0, float(header))
    except (TypeError, ValueError):
        return RATE_LIMIT_WAIT_SECONDS


def _get_json(url: str, params: dict[str, str]):
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": USER_AGENT})
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < RATE_LIMIT_RETRIES:
                _sleep(_retry_after_seconds(exc))
                continue
            raise OpenMeteoError(f"Open-Meteo request failed ({url}): {exc}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            # A dropped connection part-way through a long run. Unlike a 429
            # it carries no hint of how long to wait, so wait briefly.
            if attempt < CONNECTION_RETRIES:
                _sleep(CONNECTION_WAIT_SECONDS)
                continue
            raise OpenMeteoError(f"Open-Meteo request failed ({url}): {exc}") from exc
        except json.JSONDecodeError as exc:
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


def _daily_locations(url: str, batch: list[Point], params: dict[str, str]) -> list[dict]:
    """The `daily` block of each location in `batch`, in the order given."""
    payload = _get_json(
        url,
        {
            "latitude": ",".join(f"{p.lat:.5f}" for p in batch),
            "longitude": ",".join(f"{p.lon:.5f}" for p in batch),
            "daily": "precipitation_sum",
            "timezone": "Africa/Nairobi",
            **params,
        },
    )
    # A single location comes back as an object, several as a list.
    locations = payload if isinstance(payload, list) else [payload]
    if len(locations) != len(batch):
        raise OpenMeteoError(f"Expected {len(batch)} locations, got {len(locations)}")
    return [location.get("daily") or {} for location in locations]


def _series(daily: dict, key: str = "precipitation_sum") -> dict[dt.date, float]:
    dates = daily.get("time") or []
    totals = daily.get(key) or []
    if len(dates) != len(totals):
        raise OpenMeteoError("Mismatched date/precipitation arrays")
    return {
        dt.date.fromisoformat(day): float(mm)
        # A null means the model had no value for that day; it is not the
        # same as zero rain, so it is dropped rather than stored as 0.0.
        for day, mm in zip(dates, totals)
        if mm is not None
    }


def fetch_daily_rainfall(
    points: list[Point], past_days: int
) -> list[dict[dt.date, float]]:
    """Daily rainfall in mm per point: {date: mm}, for the last `past_days`.

    `forecast_days=0` keeps the response to dates that have already
    happened, so nothing stored is a prediction.
    """
    series: list[dict[dt.date, float]] = []
    for batch in _chunks(points, MAX_FORECAST_LOCATIONS):
        params = {"past_days": str(past_days), "forecast_days": "0"}
        series.extend(_series(daily) for daily in _daily_locations(FORECAST_URL, batch, params))
    return series


def fetch_archive_rainfall(
    points: list[Point], start: dt.date, end: dt.date
) -> list[dict[dt.date, float]]:
    """Historical daily rainfall in mm per point, from the ERA5 reanalysis.

    This is what the model is trained on: one consistent record back to 1940,
    which the forecast endpoint (a few months of history) cannot give.
    """
    series: list[dict[dt.date, float]] = []
    for batch in _chunks(points, MAX_ARCHIVE_LOCATIONS):
        params = {"start_date": start.isoformat(), "end_date": end.isoformat()}
        series.extend(_series(daily) for daily in _daily_locations(ARCHIVE_URL, batch, params))
    return series


def fetch_seasonal_rainfall(
    points: list[Point], forecast_days: int
) -> list[list[dict[dt.date, float]]]:
    """Forecast daily rainfall in mm per point, one series per ensemble member.

    Unlike everything else in this module these are predictions, starting
    today. They feed `flood_outlook` only and are never stored as
    EnvironmentalData.
    """
    members: list[list[dict[dt.date, float]]] = []
    for batch in _chunks(points, MAX_SEASONAL_LOCATIONS):
        params = {"forecast_days": str(forecast_days)}
        for daily in _daily_locations(SEASONAL_URL, batch, params):
            keys = sorted(key for key in daily if key.startswith("precipitation_sum"))
            members.append([_series(daily, key) for key in keys])
    return members
