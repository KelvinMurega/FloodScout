"""The model inputs behind one score, in a form a person can read.

The dashboard's detail panel shows these under "Inputs behind this score"
(Chapter 4, wireframe 4.8). They are what the model was given for a region
and a date, each set against what is usual where there is a usual to set it
against. They are inputs, not causes: the model's explanation is the
permutation importance in riskengine/ml/README.md, not this list.

Everything is read through the same functions the scorer uses
(riskengine/ml/infer.py and features.py), so the panel cannot drift from
what was scored. It is worked out again on request from the rainfall stored
now, which can differ slightly from the day the score was written if
rainfall has been corrected since.
"""

from __future__ import annotations

import calendar
import datetime as dt
import math

from geodata.services.climate_indices import dmi_for, oni_for
from riskengine.ml.features import (
    SHORT_RAINS_MONTHS,
    WINDOW_DAYS,
    rainfall_features,
)
from riskengine.ml.infer import model_features, region_rain

# A value within this share of the usual one is called "near" it.
NEAR_USUAL = 0.10

# Kenya's March to May rains. The short rains are in features.py because the
# model uses them; the long rains are named here only for the reader.
LONG_RAINS_MONTHS = frozenset({3, 4, 5})

# The conventional thresholds for calling a phase: half a degree for the
# Oceanic Niño Index, 0.4 for the Dipole Mode Index.
ONI_PHASE = 0.5
DMI_PHASE = 0.4


def _number(value) -> float | None:
    """A float rounded for display, or None for a missing value."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return round(float(value), 2)


def compare(value: float | None, usual: float | None) -> str | None:
    """"above", "below" or "near" the usual value; None when either is unknown."""
    if value is None or usual is None:
        return None
    if usual <= 0:
        return "above" if value > 0 else "near"
    ratio = value / usual
    if ratio > 1 + NEAR_USUAL:
        return "above"
    if ratio < 1 - NEAR_USUAL:
        return "below"
    return "near"


def _phase(value: float | None, threshold: float, positive: str, negative: str) -> str | None:
    if value is None:
        return None
    if value >= threshold:
        return positive
    if value <= -threshold:
        return negative
    return "Neutral"


def _season(month: int) -> str:
    if month in SHORT_RAINS_MONTHS:
        return "Short rains (October to December)"
    if month in LONG_RAINS_MONTHS:
        return "Long rains (March to May)"
    return "Outside the two rainy seasons"


def _item(key, label, value=None, unit=None, usual=None, comparison=None, note=None) -> dict:
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "usual": usual,
        "comparison": comparison,
        "note": note,
    }


def score_inputs(model: dict, region, date: dt.date) -> list[dict]:
    """What `model` is given for `region` over the 30 days ending on `date`.

    One entry per input a reader would recognise, in the order rainfall,
    oceans, season, land. Inputs the model derives from these (the rainfall
    ratio, the short-rains versions of the ocean indices, the two month
    terms) are covered by the entry they come from. An input `model` was not
    trained on is left out.
    """
    used = set(model_features(model))
    county = region.parent if region.parent_id else region
    # A sub-county is measured against its county's normal, as in scoring.
    normals = model.get("normals", {}).get(region.county_name, {})
    previous_end = date - dt.timedelta(days=WINDOW_DAYS)

    rain = rainfall_features(region_rain(region, date), date)
    terrain = region.geospatial_features.order_by("-pk").first()
    items = []

    if used & {"rain_30d", "rain_30d_ratio"}:
        value, usual = _number(rain["rain_30d"]), _number(normals.get(date.month))
        items.append(
            _item(
                "rain_30d",
                f"Rain in the {WINDOW_DAYS} days to this date",
                value,
                "mm",
                usual,
                compare(value, usual),
                f"Usual is the county's mean for {calendar.month_name[date.month]} in the training years.",
            )
        )
    if "rain_prev_30d" in used:
        value, usual = _number(rain["rain_prev_30d"]), _number(normals.get(previous_end.month))
        items.append(
            _item(
                "rain_prev_30d",
                f"Rain in the {WINDOW_DAYS} days before that",
                value,
                "mm",
                usual,
                compare(value, usual),
                "How wet the ground already was.",
            )
        )
    if "rain_7d_max" in used:
        items.append(
            _item("rain_7d_max", "Wettest 7 days in a row", _number(rain["rain_7d_max"]), "mm")
        )

    if used & {"oni", "oni_short_rains"}:
        value = _number(oni_for(date, model.get("oni", {})))
        items.append(
            _item(
                "oni",
                "El Niño index (ONI)",
                value,
                None,
                0.0,
                None,
                _phase(value, ONI_PHASE, "El Niño conditions", "La Niña conditions"),
            )
        )
    if used & {"dmi", "dmi_short_rains"}:
        value = _number(dmi_for(date, model.get("dmi", {})))
        items.append(
            _item(
                "dmi",
                "Indian Ocean Dipole index (DMI)",
                value,
                None,
                0.0,
                None,
                _phase(value, DMI_PHASE, "Positive phase", "Negative phase"),
            )
        )

    if used & {"month_sin", "month_cos"}:
        items.append(
            _item("season", "Season", note=f"{calendar.month_name[date.month]}: {_season(date.month)}")
        )

    if "elevation" in used:
        items.append(
            _item("elevation", "Elevation", _number(getattr(terrain, "elevation", None)), "m")
        )
    if "slope" in used:
        items.append(
            _item("slope", "Slope", _number(getattr(terrain, "slope", None)), "degrees")
        )
    if "log_area" in used:
        items.append(
            _item(
                "area",
                "County area",
                _number(county.area_size),
                "km²",
                note="A control for how floods were recorded, not a cause of flooding.",
            )
        )
    return items
