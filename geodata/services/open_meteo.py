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
              Daily `precipitation_sum`. The request names ERA5, so the
              whole record is one reanalysis. Left to itself (`best_match`)
              the service gives ERA5 before 2017 and the ECMWF IFS analysis
              from 2017 on, which is how the training sets up to October
              2026 were built. See docs/DATA_DICTIONARY.md, section 3.
              The training record.
  Forecast    https://api.open-meteo.com/v1/forecast  with `forecast_days`
              Daily `precipitation_sum` for the days ahead. A forecast,
              used only by the 7-day risk forecast and never stored as
              rainfall that fell.
  Outlook     https://seasonal-api.open-meteo.com/v1/seasonal
              ECMWF seasonal ensemble, daily `precipitation_sum` per member
              for the months ahead. A forecast, and used only as one.
  Rivers      https://flood-api.open-meteo.com/v1/flood
              Daily `river_discharge` in m³/s from the Global Flood
              Awareness System (GloFAS v4, 0.05°), named as `seamless_v4`:
              the reanalysis, then the forecast for the days ahead, so the
              same series serves training, daily scoring and the forecast.
              For Kenya the reanalysis starts on 1 January 1997.

Nothing here interprets the numbers. Turning them into a risk score is
`riskengine/ml/infer.py`'s job.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SEASONAL_URL = "https://seasonal-api.open-meteo.com/v1/seasonal"
FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"

# The GloFAS record asked for by name, so it cannot change underneath us.
DISCHARGE_MODEL = "seamless_v4"
# The GloFAS v4 grid: a river is one cell wide on it, so a point has to sit
# on the right cell to read the river and not the dry land beside it.
GLOFAS_CELL_DEG = 0.05
# Open-Meteo counts a request for one location as one call per this many
# days of data (https://open-meteo.com/en/pricing). Used to keep a long
# download inside a daily budget.
DAYS_PER_CALL = 14

# Open-Meteo accepts many coordinates per call. Kept well under the
# documented ceilings so one slow county cannot fail the whole run.
MAX_ELEVATION_POINTS = 100
MAX_FORECAST_LOCATIONS = 25
# Decades of days, or 51 ensemble members, per location: keep batches small.
MAX_ARCHIVE_LOCATIONS = 5
MAX_SEASONAL_LOCATIONS = 10
MAX_FLOOD_LOCATIONS = 25

# Rainfall models the archive is asked for. `best_match` is the service's
# own choice, kept so the older training sets can be rebuilt as they were.
ARCHIVE_MODELS = ("era5", "best_match")
DEFAULT_ARCHIVE_MODEL = "era5"

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

# The name usage is counted under (geodata.models.ApiUsage), and the free
# tier's documented limits (https://open-meteo.com/en/terms).
SERVICE = "open-meteo"
FREE_CALLS_PER_DAY = 10_000
FREE_CALLS_PER_HOUR = 5_000
FREE_CALLS_PER_MINUTE = 600


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


def calls_in(params: dict[str, str]) -> float:
    """The calls Open-Meteo counts for one request with these parameters."""
    locations = len(params["latitude"].split(",")) if params.get("latitude") else 1
    if params.get("start_date") and params.get("end_date"):
        days = (
            dt.date.fromisoformat(params["end_date"]) - dt.date.fromisoformat(params["start_date"])
        ).days + 1
    else:
        days = int(params.get("past_days", 0)) + int(params.get("forecast_days", 7))
    return calls_for(locations, days)


def _record(params: dict[str, str] | None, *, rate_limited: bool = False, failed: bool = False) -> None:
    """Add one request to today's ApiUsage row (the analytics page's quota chart).

    Never allowed to break a download: outside Django, or with the database
    unavailable, the request simply goes uncounted. The savepoint keeps a
    failed write from spoiling a transaction the caller has open.
    """
    try:
        from django.db import transaction
        from django.db.models import F
        from django.utils import timezone

        from geodata.models import ApiUsage

        with transaction.atomic():
            row, _ = ApiUsage.objects.get_or_create(service=SERVICE, day=timezone.localdate())
            ApiUsage.objects.filter(pk=row.pk).update(
                requests=F("requests") + (1 if params is not None else 0),
                calls=F("calls") + (calls_in(params) if params is not None else 0),
                rate_limited=F("rate_limited") + int(rate_limited),
                failed=F("failed") + int(failed),
            )
    except Exception:  # noqa: BLE001 - counting is best effort by design
        pass


def _get_json(url: str, params: dict[str, str]):
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": USER_AGENT})
    _record(params)
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                _record(None, rate_limited=True)
            if exc.code == 429 and attempt < RATE_LIMIT_RETRIES:
                _sleep(_retry_after_seconds(exc))
                continue
            # A 5xx is the service shedding load, seen part-way through the
            # thousands of points of an area grid: wait, like a dropped connection.
            if exc.code >= 500 and attempt < CONNECTION_RETRIES:
                _sleep(CONNECTION_WAIT_SECONDS)
                continue
            _record(None, failed=True)
            raise OpenMeteoError(f"Open-Meteo request failed ({url}): {exc}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            # A dropped connection part-way through a long run. Unlike a 429
            # it carries no hint of how long to wait, so wait briefly.
            if attempt < CONNECTION_RETRIES:
                _sleep(CONNECTION_WAIT_SECONDS)
                continue
            _record(None, failed=True)
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


def _daily_locations(
    url: str, batch: list[Point], params: dict[str, str], variable: str = "precipitation_sum"
) -> list[dict]:
    """The `daily` block of each location in `batch`, in the order given."""
    payload = _get_json(
        url,
        {
            "latitude": ",".join(f"{p.lat:.5f}" for p in batch),
            "longitude": ",".join(f"{p.lon:.5f}" for p in batch),
            "daily": variable,
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


def fetch_forecast_rainfall(points: list[Point], days: int) -> list[dict[dt.date, float]]:
    """Forecast daily rainfall in mm per point, for today and the `days` after.

    Predictions, from the same ECMWF-led forecast the observed series comes
    from. They feed `forecast_scores` only and are never stored as
    EnvironmentalData, so nothing observed is ever overwritten by a forecast.
    """
    series: list[dict[dt.date, float]] = []
    for batch in _chunks(points, MAX_FORECAST_LOCATIONS):
        params = {"past_days": "0", "forecast_days": str(days + 1)}
        series.extend(_series(daily) for daily in _daily_locations(FORECAST_URL, batch, params))
    return series


def fetch_archive_rainfall(
    points: list[Point], start: dt.date, end: dt.date, model: str = DEFAULT_ARCHIVE_MODEL
) -> list[dict[dt.date, float]]:
    """Historical daily rainfall in mm per point, from the rainfall `model`.

    This is what the model is trained on: one consistent record back to 1940,
    which the forecast endpoint (a few months of history) cannot give. It is
    only consistent with `model="era5"`; `best_match` changes dataset at 2017.
    """
    if model not in ARCHIVE_MODELS:
        raise ValueError(f"Unknown archive model {model!r}; expected one of {ARCHIVE_MODELS}")
    series: list[dict[dt.date, float]] = []
    for batch in _chunks(points, MAX_ARCHIVE_LOCATIONS):
        params = {"start_date": start.isoformat(), "end_date": end.isoformat(), "models": model}
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


def calls_for(locations: int, days: int) -> float:
    """How many calls Open-Meteo counts for `days` of data at `locations` points."""
    return locations * max(1.0, days / DAYS_PER_CALL)


def fetch_discharge(points: list[Point], start: dt.date, end: dt.date) -> list[dict[dt.date, float]]:
    """Daily river discharge in m³/s per point, from `start` to `end` inclusive.

    Past days are the GloFAS reanalysis and days ahead its forecast. A day
    with no value (before 1997, or a cell off the river network) is left
    out, not stored as zero flow.
    """
    series: list[dict[dt.date, float]] = []
    for batch in _chunks(points, MAX_FLOOD_LOCATIONS):
        params = {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "models": DISCHARGE_MODEL,
        }
        series.extend(
            _series(daily, "river_discharge")
            for daily in _daily_locations(FLOOD_URL, batch, params, variable="river_discharge")
        )
    return series


def glofas_cell(value: float) -> float:
    """The centre of the GloFAS cell holding `value` (degrees), e.g. 39.575."""
    return (math.floor(value / GLOFAS_CELL_DEG) + 0.5) * GLOFAS_CELL_DEG


def river_cell(
    point: Point, start: dt.date, end: dt.date, expected_m3s: float, reach: int = 1
) -> tuple[Point, float]:
    """The GloFAS cell near `point` carrying its river, and its mean m³/s.

    `point` lies on the river's line; on GloFAS's coarser grid the river may
    sit a cell away. The cells within `reach` of it are read over `start` to
    `end`, and the one whose mean flow is nearest `expected_m3s`, as a ratio,
    is taken. Nearest and not wettest: the wettest is often a lake or a
    larger river next to the one wanted.
    """
    centre = Point(glofas_cell(point.lon), glofas_cell(point.lat))
    cells = [
        Point(
            round(centre.lon + dx * GLOFAS_CELL_DEG, 3),
            round(centre.lat + dy * GLOFAS_CELL_DEG, 3),
        )
        for dy in range(-reach, reach + 1)
        for dx in range(-reach, reach + 1)
    ]
    means = [
        sum(flows.values()) / len(flows) if flows else 0.0
        for flows in fetch_discharge(cells, start, end)
    ]

    def mismatch(index: int) -> float:
        # A cell with no flow is off the river whatever is expected.
        return abs(math.log(means[index] / expected_m3s)) if means[index] > 0 else math.inf

    best = min(range(len(cells)), key=mismatch)
    return cells[best], means[best]
