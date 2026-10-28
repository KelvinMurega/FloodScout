"""The dry gate: a window with little rain is shown no higher than Moderate.

The model leans on the season and the ocean indices far more than on the
rain itself (permutation importance: month 0.092, ONI 0.028, the window's
rain 0.005), so in a wet season it paints all of Kenya High. On 27 October
2026 Kajiado had 6 mm in 14 days and was High. The gate lets the rain that
fell overrule the season: when a region's 30-day window is dry for it, its
category is capped at Moderate. The probability is left as the model gave
it; only the category, and so the colour and the alerts, change.

"Dry" was chosen by `manage.py backtest_dry_gate` from candidates fixed
before it ran (GATES), on 28 October 2026. G4 won: a window under 25 mm or
under half the county's normal rain. Replayed on held-out years:

  1996-2016   High or above 36% of county-months -> 30%; 781 of the 855
              floods there kept (91%); flood rate there 20.0% -> 22.3%
  2017-2021   51% -> 42%; 161 of 166 kept (97%); 12.2% -> 14.4% (scored once)

The floods it moves below High are mostly river floods in dry counties
(Isiolo, Marsabit, Samburu, Kitui), where the water comes from rain upstream:
a blind spot the map says out loud.
"""

from __future__ import annotations

import math

from riskengine.models import RiskScore

# The candidates, fixed on 28 October 2026 before the replay was run:
# (rain_30d below this many mm, rain_30d_ratio below this share of normal, how they combine).
GATES = {
    "G1": "under half the county's normal rain for the window",
    "G2": "under 25 mm in the window",
    "G3": "both: under 25 mm and under half of normal",
    "G4": "either: under 25 mm or under half of normal",
}
DRY_MM = 25.0
DRY_RATIO = 0.5

ORDER = [
    RiskScore.RiskCategory.LOW,
    RiskScore.RiskCategory.MODERATE,
    RiskScore.RiskCategory.HIGH,
    RiskScore.RiskCategory.VERY_HIGH,
]
CAP = RiskScore.RiskCategory.MODERATE


def dry_by(gate: str, rain_30d, ratio) -> bool:
    """Whether a window with `rain_30d` mm, `ratio` of normal, is dry under `gate`."""
    low_mm = rain_30d is not None and not math.isnan(rain_30d) and rain_30d < DRY_MM
    low_ratio = ratio is not None and not math.isnan(ratio) and ratio < DRY_RATIO
    return {
        "G1": low_ratio,
        "G2": low_mm,
        "G3": low_mm and low_ratio,
        "G4": low_mm or low_ratio,
    }[gate]


GATE = "G4"


def capped(category: str) -> str:
    """`category`, no higher than CAP."""
    return category if ORDER.index(category) <= ORDER.index(CAP) else CAP


def gated(category: str, row, names) -> str:
    """`category` for a model input row (`names` its columns), capped if its window is dry."""
    values = dict(zip(names, row))
    if dry_by(GATE, values.get("rain_30d"), values.get("rain_30d_ratio")):
        return capped(category)
    return category
