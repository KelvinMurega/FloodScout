"""Placeholder risk scorer — the interface, not the model.

`infer(region, date)` returns `(predicted_value, risk_category)`, and nothing
downstream (the `score_regions` command, the API, the dashboard) knows or
cares how that tuple was produced. When the scikit-learn model from Chapter 3
lands it replaces the body of `infer()` and bumps `MODEL_VERSION`; the
signature does not change. That is the whole point of this module existing
before the model does.

Until then the score is a deliberately crude, documented rule of thumb:

    predicted_value = RAINFALL_WEIGHT  * rainfall_component
                    + ELEVATION_WEIGHT * elevation_component

Both components are in [0, 1], where 1 means "more dangerous":

  rainfall_component   total rainfall over the RAINFALL_WINDOW_DAYS ending on
                       `date`, divided by RAINFALL_SATURATION_MM, clamped.
                       Wetter ground, less capacity to absorb more.
  elevation_component  1.0 at or below sea level, falling linearly to 0.0 at
                       ELEVATION_CEILING_M. Water collects in low ground.

`GeospatialFeature` and `EnvironmentalData` are mostly empty at this stage of
the project, which is expected. A missing component is replaced by
NEUTRAL_COMPONENT rather than dropped, so the weights stay fixed and a region
with no data at all scores exactly 0.5 — the top of "moderate". That is the
designed fallback, not an accident of the arithmetic.

None of this is calibrated against observed floods, and it must never be
read as a prediction. Every row produced from it is stamped with
MODEL_VERSION so a placeholder can not be mistaken for a trained-model
result, in the database or in the API.
"""

from __future__ import annotations

import datetime as dt

from django.db.models import Sum

from riskengine.models import RiskScore

# Bumped by whatever replaces the heuristic. Stored on every RiskScore row.
MODEL_VERSION = "heuristic-stub-v0"

# --- The rule of thumb, in full ------------------------------------------

RAINFALL_WINDOW_DAYS = 7
RAINFALL_SATURATION_MM = 200.0  # a week this wet is treated as maximally wet
ELEVATION_CEILING_M = 2500.0  # above this, elevation contributes no risk

RAINFALL_WEIGHT = 0.6
ELEVATION_WEIGHT = 0.4

# Substituted for any component with no data behind it. With both missing,
# 0.6 * 0.5 + 0.4 * 0.5 == 0.5 exactly.
NEUTRAL_COMPONENT = 0.5

# Inclusive upper bound of each band, walked in order; anything above the
# last one is VERY_HIGH. The no-data value of 0.5 lands at the top of
# MODERATE by design.
CATEGORY_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.25, RiskScore.RiskCategory.LOW),
    (0.50, RiskScore.RiskCategory.MODERATE),
    (0.75, RiskScore.RiskCategory.HIGH),
)
TOP_CATEGORY = RiskScore.RiskCategory.VERY_HIGH


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def rainfall_component(region, date: dt.date) -> float | None:
    """Wetness in [0, 1] over the window ending on `date`.

    Returns None when no EnvironmentalData row falls in the window, which is
    the normal case today.
    """
    window_start = date - dt.timedelta(days=RAINFALL_WINDOW_DAYS - 1)
    total = region.environmental_records.filter(
        record_date__gte=window_start, record_date__lte=date
    ).aggregate(total=Sum("rainfall_intensity"))["total"]
    if total is None:
        return None
    return _clamp(total / RAINFALL_SATURATION_MM)


def elevation_component(region) -> float | None:
    """Lowness in [0, 1]. Returns None when the region has no elevation on file."""
    feature = region.geospatial_features.order_by("-pk").first()
    if feature is None or feature.elevation is None:
        return None
    return _clamp(1.0 - (feature.elevation / ELEVATION_CEILING_M))


def classify(predicted_value: float) -> str:
    """Map a raw score to a RiskScore.RiskCategory value (FR-04)."""
    for upper_bound, category in CATEGORY_THRESHOLDS:
        if predicted_value <= upper_bound:
            return category
    return TOP_CATEGORY


def infer(region, date) -> tuple[float, str]:
    """Return (predicted_value, risk_category).

    `region` is a geodata.Region, `date` a datetime.date. `risk_category` is
    always one of RiskScore.RiskCategory. This signature is the contract the
    trained model will honour — callers should import nothing else from here
    except MODEL_VERSION.
    """
    rainfall = rainfall_component(region, date)
    elevation = elevation_component(region)

    predicted_value = _clamp(
        RAINFALL_WEIGHT * (NEUTRAL_COMPONENT if rainfall is None else rainfall)
        + ELEVATION_WEIGHT * (NEUTRAL_COMPONENT if elevation is None else elevation)
    )
    predicted_value = round(predicted_value, 4)
    return predicted_value, classify(predicted_value)
