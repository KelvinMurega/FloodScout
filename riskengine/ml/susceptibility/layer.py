"""The shipped susceptibility layer, summed up per sub-county and county.

Refocus Phase D's aggregation rules, fixed here before the layer is shown:

- **mean**: the area-weighted mean of the pixels' susceptibility, the chance a
  pixel goes under water given its county floods.
- **high share**: the share of the region's land at or above HIGH_PERCENTILE
  of every usable pixel in Kenya: how much of it is among the country's most
  flood-prone ground.
- **expected km²**: the sum of susceptibility x pixel area, the same measure
  the UNOSAT test scores sub-counties by (`unosat_test.expected_area`).
- **land km²**: the usable land the three are taken over.

Usable means as in the test: in a sub-county, not permanent water, every
feature present. A county is summed over all its sub-counties' pixels.

Plain NumPy, so every rule is tested without Earth Engine.
"""

from __future__ import annotations

import numpy as np

from . import train
from .unosat_test import FEATURE_BANDS, usable

HIGH_PERCENTILE = 80


def high_line(probabilities: list[np.ndarray]) -> float:
    """HIGH_PERCENTILE of every usable pixel's susceptibility in Kenya."""
    return float(np.percentile(np.concatenate(probabilities), HIGH_PERCENTILE))


def _summary(probability: np.ndarray, area_m2: np.ndarray, line: float) -> dict:
    land = float(area_m2.sum())
    if land <= 0:
        return {"mean": None, "high_share": None, "expected_km2": 0.0, "land_km2": 0.0}
    return {
        "mean": round(float((probability * area_m2).sum() / land), 4),
        "high_share": round(float(area_m2[probability >= line].sum() / land), 4),
        "expected_km2": round(float((probability * area_m2).sum() / 1e6), 2),
        "land_km2": round(land / 1e6, 2),
    }


def usable_scores(pixels: dict, model) -> dict:
    """The usable pixels of one county: their sub-county, area and susceptibility."""
    keep = usable(pixels)
    probability = np.zeros(int(keep.sum()), dtype=np.float32)
    if keep.any():
        inputs = train.matrix({k: v[keep] for k, v in pixels.items()}, FEATURE_BANDS)
        probability = model.predict_proba(inputs)[:, 1].astype(np.float32)
    return {
        "sub_county": pixels["sub_county"][keep].astype(int),
        "area_m2": pixels["area_m2"][keep].astype(np.float64),
        "probability": probability,
    }


def summarise(scored: dict, line: float) -> dict[int, dict]:
    """{sub-county number: summary} for one county's usable pixels, and 0 for the county."""
    numbers, p, area = scored["sub_county"], scored["probability"], scored["area_m2"]
    result = {
        int(n): _summary(p[numbers == n], area[numbers == n], line) for n in np.unique(numbers)
    }
    result[0] = _summary(p, area, line)
    return result


def ranks(rows: list[dict], key: str = "high_share") -> None:
    """Add `rank_in_kenya` (1 is the most flood-prone) to each row, among rows of its level."""
    for level in {row["level"] for row in rows}:
        same = sorted(
            (row for row in rows if row["level"] == level and row[key] is not None),
            key=lambda row: -row[key],
        )
        for position, row in enumerate(same, start=1):
            row["rank_in_kenya"] = position
            row["of"] = len(same)
