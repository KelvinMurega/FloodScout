"""The map's risk bands: what Low, Moderate, High and Very High mean.

Until 15 November 2026 the map used the training run's bands: High from a
10% chance, Very High from 20%. The usual chance of a recorded flood in a
county-month is 10.3%, so High started at the usual chance. On held-out
years (1996 to 2016) a county at 10 to 15% flooded 10.9% of the time, an
ordinary month painted red. In the short rains of 2026 that put 40 of 47
counties at High or above, among them Mandera, Wajir and Garissa on 31 to
66% of their normal rain.

The bands are now fixed by what each colour has to mean, measured as the
flood rate of the county-months in it against the usual chance:

  Low        at most half the usual chance
  High       at least 1.5 times the usual chance
  Very High  at least 3 times the usual chance

and High or above also needs the window's rain to be at least the county's
normal for it (`dry.RAIN_FOR_HIGH`): the model leans on the season and the
ocean indices far more than on the rain (dry.py), so without rain the
chance is the season's, and the season belongs to the outlook. On held-out
years the flood rate at High and above runs from 6.5% when the window had
under half its normal rain to 31% when it had over twice.

Replayed by `manage.py backtest_map_bands` on the held-out scores of
alerts/backtest.py (each county-month scored by models that never saw its
year, mean over seeds 0 to 4), 1996 to 2016 only; 2017 to 2021 stays
closed. Candidates fixed before the run (CANDIDATES), 15 November 2026:

  Candidate                                Low   Mod   High   VH    High+  Floods at High+
  S0 5/10/20, dry gate (the old map)       0.24x 1.01x 1.40x 2.80x  29.6%  781 of 1,225
  S1 5/15/30, dry gate                     0.24x 1.06x 1.71x 3.49x  21.2%  655
  S2 5/15/30, High needs 75% of normal     0.24x 1.12x 1.79x 3.49x  18.6%  593
  S3 5/15/30, High needs normal rain       0.24x 1.23x 1.93x 3.52x  14.4%  485
  S4 5/10/20, High needs 75% of normal     0.24x 1.07x 1.52x 2.84x  25.7%  711
  S5 5/10/20, High needs normal rain       0.24x 1.18x 1.77x 2.92x  19.4%  583

The old bands fail the meanings (High 1.40x, Very High 2.80x). S1 to S3
pass. The rule written before the run, the most floods at High and above
among those that pass, picks S1, which still showed 32 counties High on
10 November 2026. S3 was chosen instead, by Kelvin, after seeing that:
it is the one where the map follows the rain, at the cost of 170 of the
floods S1 has at High and above. A typical short-rains month on history
has no county at High (8 on the old bands), a typical long-rains month 6
(22). Very High, which a Watch needs (alerts/rules.py), fell from 1,891
county-months to 885 and from 547 floods to 322, and its flood rate rose
from 28.9% to 36.4%.

The model's own bands (`probability_bands` in metrics.json, 5/10/20) are
left as they are: they are what its evaluation was reported in.
"""

from __future__ import annotations

import numpy as np

# Upper bounds of Low, Moderate and High; above the last is Very High.
MAP_BANDS = (0.05, 0.15, 0.30)

# What each band has to mean, as a multiple of the usual chance.
LOW_AT_MOST, HIGH_AT_LEAST, VERY_HIGH_AT_LEAST = 0.5, 1.5, 3.0

# Fixed on 15 November 2026 before the replay was run:
# name: (what it is, band edges, the share of normal rain High needs or None).
CANDIDATES = {
    "S0": ("5/10/20, dry gate (the old map)", (0.05, 0.10, 0.20), None),
    "S1": ("5/15/30, dry gate", (0.05, 0.15, 0.30), None),
    "S2": ("5/15/30, High needs 75% of normal rain", (0.05, 0.15, 0.30), 0.75),
    "S3": ("5/15/30, High needs normal rain", (0.05, 0.15, 0.30), 1.0),
    "S4": ("5/10/20, High needs 75% of normal rain", (0.05, 0.10, 0.20), 0.75),
    "S5": ("5/10/20, High needs normal rain", (0.05, 0.10, 0.20), 1.0),
}
CHOSEN = "S3"


def levels(probability, rain_30d, ratio, edges, rain_for_high) -> np.ndarray:
    """0 to 3 (Low to Very High) per row, capped at Moderate when the window is dry.

    Dry is the dry gate (dry.py: under 25 mm or under half of normal) or,
    when `rain_for_high` is set, under that share of normal. A ratio of NaN
    (no normal on file) caps nothing.
    """
    from riskengine.dry import DRY_MM, DRY_RATIO

    low, moderate, high = edges
    level = np.select([probability > high, probability > moderate, probability > low], [3, 2, 1], 0)
    with np.errstate(invalid="ignore"):
        dry = (rain_30d < DRY_MM) | (ratio < DRY_RATIO)
        if rain_for_high is not None:
            dry |= ratio < rain_for_high
    return np.where(dry & (level > 1), 1, level)


def replay(probability, flooded, rain_30d, ratio, months, years) -> dict:
    """Every candidate on 1996 to 2016: each band's flood rate against the usual chance."""
    from riskengine.ml import train

    keep = years < train.FINAL_TEST_FROM
    probability, flooded, rain_30d, ratio = probability[keep], flooded[keep], rain_30d[keep], ratio[keep]
    months, years = months[keep], years[keep]
    usual = float(flooded.mean())
    dates = years * 100 + months
    results = {}
    for name, (_text, edges, rain_for_high) in CANDIDATES.items():
        level = levels(probability, rain_30d, ratio, edges, rain_for_high)
        times = [
            round(float(flooded[level == k].mean()) / usual, 2) if (level == k).any() else None
            for k in range(4)
        ]
        high = level >= 2

        def typical(season):
            rows = np.isin(months, season)
            return float(np.median([high[rows & (dates == d)].sum() for d in np.unique(dates[rows])]))

        results[name] = {
            "times_usual": dict(zip(("low", "moderate", "high", "very_high"), times)),
            "high_or_above_share": round(float(high.mean()), 3),
            "floods_high_or_above": int((high & flooded).sum()),
            "floods": int(flooded.sum()),
            "typical_high_short_rains": typical((10, 11, 12)),
            "typical_high_long_rains": typical((3, 4, 5)),
            "meets_meanings": _meets(times),
        }
    passing = [n for n, r in results.items() if r["meets_meanings"]]
    by_rule = max(passing, key=lambda n: results[n]["floods_high_or_above"]) if passing else None
    return {
        "usual_chance": round(usual, 4),
        "candidates": {name: text for name, (text, _e, _r) in CANDIDATES.items()},
        "results": results,
        "chosen_by_rule": by_rule,
        "chosen": CHOSEN,
    }


def _meets(times) -> bool:
    low, _moderate, high, very_high = times
    return (
        low is not None
        and high is not None
        and very_high is not None
        and low <= LOW_AT_MOST
        and high >= HIGH_AT_LEAST
        and very_high >= VERY_HIGH_AT_LEAST
    )
