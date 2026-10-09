"""The model's inputs, defined once for training, scoring and the outlook.

A row describes one region over the 30 days ending on a date. Everything in
it is something that can be known on that date, so the same function builds
a training row from 1997, today's row from the database, and a row for next
December from a rainfall forecast.

Missing inputs are NaN, never a made-up number; the model pipeline imputes
them.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence

WINDOW_DAYS = 30
PEAK_DAYS = 7
# How far back from the scoring date rainfall is read: this window and the
# one before it.
HISTORY_DAYS = 2 * WINDOW_DAYS

# A window with more than this many days missing is treated as unknown
# rather than summed short.
MAX_MISSING_DAYS = 3

# The windows and threshold of the inputs under trial below.
LONG_WINDOW_DAYS = 3 * WINDOW_DAYS
SHORT_PEAK_DAYS = 3
HEAVY_DAY_MM = 50.0

# Fixed properties of the region, read off its GeospatialFeature.
#
# The drainage fields on the same row (distance_to_river,
# flood_zone_fraction, built_up_fraction) are deliberately not here. They
# were tried on 1 October 2026 and the model learned them backwards: more
# built-up and more flood-zone meant *less* flood. The cause is the labels,
# not the maps. A flood is placed as a circle, a big county is hit by more
# circles than a small one, and Kenya's built-up counties are its smallest.
# Until there are labels that say where inside a county it flooded, adding
# these would lower the score of exactly the places that drain worst. See
# riskengine/ml/README.md. `log_area` below is the control that should make
# them usable; check their signs before adding them back.
TERRAIN_FEATURES = (
    "elevation",  # m
    "slope",  # degrees
)

FEATURE_NAMES = (
    "rain_30d",  # mm over the window
    "rain_7d_max",  # wettest 7 consecutive days in the window, mm
    "rain_prev_30d",  # mm over the 30 days before the window: how wet the ground already was
    "rain_30d_ratio",  # rain_30d over what the county normally gets in that month
    "oni",  # El Niño / La Niña index available on the date
    "dmi",  # Indian Ocean Dipole index available on the date
    # The same two indices inside the short rains, zero outside them. Both
    # oceans drive Kenya's October-December rains far more than the long
    # rains, and a linear model cannot learn "only in these months" from
    # the plain index and the month separately.
    "oni_short_rains",
    "dmi_short_rains",
    "month_sin",
    "month_cos",
    *TERRAIN_FEATURES,
    # Natural log of the county's area in km². Not a cause of flooding: a
    # control for how the labels are made. A flood is placed as a circle,
    # so a big county is touched by more circles than a small one. With
    # area in the model, the other inputs stop absorbing that effect.
    "log_area",
)

# Inputs under trial: `rainfall_features` works them out, but the model is
# not given them. `manage.py compare_inputs` scores the model with and
# without each on held-out years, and the result goes in the experiments log
# in riskengine/ml/README.md. One that is kept moves into FEATURE_NAMES.
#
# All four were tried on 9 October 2026 and none raised held-out average
# precision. They stay here so they can be tried again once the labels say
# where the water was.
CANDIDATE_FEATURES = (
    "rain_60d",  # mm over this window and the one before: rain_30d + rain_prev_30d
    "rain_90d",  # mm over the 90 days ending on the date, so June still sees April
    "rain_3d_max",  # wettest 3 consecutive days in the window, mm
    "heavy_days",  # days in the window with HEAVY_DAY_MM or more
)

NAN = float("nan")

# The short rains, by the month the 30-day window ends in.
SHORT_RAINS_MONTHS = frozenset({10, 11, 12})


def _window(
    rain: Mapping[dt.date, float], end: dt.date, days: int = WINDOW_DAYS
) -> list[float] | None:
    """Daily mm for the `days` ending on `end`, gaps as 0, or None if too gappy.

    A longer window may miss proportionally more days.
    """
    values = [rain.get(end - dt.timedelta(days=offset)) for offset in range(days - 1, -1, -1)]
    if sum(value is None for value in values) > MAX_MISSING_DAYS * days // WINDOW_DAYS:
        return None
    return [value or 0.0 for value in values]


def _wettest(window: list[float], days: int) -> float:
    """The largest total over `days` consecutive days of `window`."""
    return max(sum(window[i : i + days]) for i in range(len(window) - days + 1))


def rainfall_features(rain: Mapping[dt.date, float], end: dt.date) -> dict[str, float]:
    """The rainfall inputs for the window ending on `end`, CANDIDATE_FEATURES included."""
    current = _window(rain, end)
    previous = _window(rain, end - dt.timedelta(days=WINDOW_DAYS))
    long = _window(rain, end, LONG_WINDOW_DAYS)
    return {
        "rain_30d": sum(current) if current else NAN,
        "rain_7d_max": _wettest(current, PEAK_DAYS) if current else NAN,
        "rain_prev_30d": sum(previous) if previous else NAN,
        "rain_60d": sum(current) + sum(previous) if current and previous else NAN,
        "rain_90d": sum(long) if long else NAN,
        "rain_3d_max": _wettest(current, SHORT_PEAK_DAYS) if current else NAN,
        "heavy_days": float(sum(mm >= HEAVY_DAY_MM for mm in current)) if current else NAN,
    }


def feature_row(
    rain: Mapping[dt.date, float],
    end: dt.date,
    *,
    normal_30d: float | None,
    oni: float | None,
    terrain,
    dmi: float | None = None,
    area_km2: float | None = None,
    names: Sequence[str] = FEATURE_NAMES,
) -> list[float]:
    """One model input row, in the order of `names` (FEATURE_NAMES by default).

    A trained model records the names it was trained on; pass them as
    `names` so a model from before an input was added still gets the row it
    expects.

    `normal_30d` is the county's long-term mean `rain_30d` for the month of
    `end`. Dividing by it is what lets one model serve both Garissa, where
    100 mm in a month is exceptional, and Kakamega, where it is ordinary.

    `terrain` is the region's GeospatialFeature, or anything else with the
    TERRAIN_FEATURES as attributes, or None when there is none on file.
    """
    row = rainfall_features(rain, end)
    row["rain_30d_ratio"] = row["rain_30d"] / normal_30d if normal_30d else NAN
    row["oni"] = NAN if oni is None else oni
    row["dmi"] = NAN if dmi is None else dmi
    in_short_rains = end.month in SHORT_RAINS_MONTHS
    row["oni_short_rains"] = row["oni"] if in_short_rains else 0.0
    row["dmi_short_rains"] = row["dmi"] if in_short_rains else 0.0
    row["log_area"] = math.log(area_km2) if area_km2 and area_km2 > 0 else NAN
    angle = 2 * math.pi * (end.month - 1) / 12
    row["month_sin"] = math.sin(angle)
    row["month_cos"] = math.cos(angle)
    for name in TERRAIN_FEATURES:
        value = getattr(terrain, name, None)
        row[name] = NAN if value is None else value
    return [row[name] for name in names]
