"""A score against an ordinary year for the same county and month.

The usual chance (`infer.times_usual`) is one figure for all of Kenya and
the whole year, so in a rainy season nearly every county is above it and the
map is red from end to end. The question a reader has in late October is
different: is this place worse than it normally is at this time of year?

`times_season` answers it. A county's ordinary chance for a month is what the
shipped model gives it with that month's median rain over 1996 to 2016 and
the ocean indices at zero (neither El Niño nor La Niña, no Indian Ocean
Dipole). Today's score over that is above 1 where the rain, the ground or the
oceans make this window worse than an ordinary one, and below 1 where they
make it better. On 27 October 2026 it ran from 0.60 (Kericho, a third of its
normal October rain) to 2.16 (Garissa, more than twice its normal), where
the plain multiple had every county between 1.4 and 2.7.

`manage.py build_season_normals` writes the table (SEASON_NORMALS_PATH) from
the training set and the model on disk; it belongs to that model version and
is ignored under any other, so a retrain needs it run again.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from riskengine.ml import infer as scorer

SEASON_NORMALS_PATH = Path(__file__).resolve().parent / "ml" / "artifacts" / "season_normals.json"

# Set to zero for an ordinary year: no El Niño, no La Niña, no dipole.
OCEAN_FEATURES = ("oni", "dmi", "oni_short_rains", "dmi_short_rains")


def ordinary_rows(data, years_before: int) -> dict[tuple[str, int], list[float]]:
    """{(county, month): the median input row of that county-month before `years_before`, oceans at zero}."""
    rows = {}
    development = data.years < years_before
    for county in np.unique(data.counties):
        for month in range(1, 13):
            chosen = development & (data.counties == county) & (data.months == month)
            if not chosen.any():
                continue
            row = np.median(data.X[chosen], axis=0)
            for name in OCEAN_FEATURES:
                if name in data.features:
                    row[data.column(name)] = 0.0
            rows[(str(county), month)] = row.tolist()
    return rows


def build(data, model, years_before: int) -> dict:
    """The table `times_season` reads: {model_version, counties: {county: {month: chance}}}."""
    if list(data.features) != scorer.model_features(model):
        raise ValueError("The training set's columns are not the model's.")
    rows = ordinary_rows(data, years_before)
    chances = scorer.flood_probabilities(model, list(rows.values()))
    counties: dict[str, dict[str, float]] = {}
    for (county, month), chance in zip(rows, chances):
        counties.setdefault(county, {})[str(month)] = round(chance, 4)
    return {"model_version": model["version"], "counties": counties}


@lru_cache(maxsize=1)
def normals() -> dict[str, dict[str, float]]:
    """{county: {month: ordinary chance}} for the model in use, or {} when there is none."""
    if not SEASON_NORMALS_PATH.exists():
        return {}
    table = json.loads(SEASON_NORMALS_PATH.read_text())
    if table.get("model_version") != scorer.MODEL_VERSION:
        return {}
    return table["counties"]


def times_season(probability: float | None, county: str, month: int) -> float | None:
    """`probability` as a multiple of an ordinary year's chance for `county` in `month`."""
    ordinary = normals().get(county, {}).get(str(month))
    if probability is None or not ordinary:
        return None
    return round(probability / ordinary, 2)
