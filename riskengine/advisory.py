"""What a risk category means for the people living in a region.

The one place the dashboard's relocation guidance comes from. It is a fixed
reading of RiskScore.risk_category, so it is exactly as trustworthy as the
scorer behind that category — and while MODEL_VERSION is a placeholder, it
is a demonstration of the feature, not advice anyone should act on. Real
evacuation orders come from the National Disaster Operations Centre and
county governments.
"""

from __future__ import annotations

from .models import RiskScore

RELOCATE = "relocate"
PREPARE = "prepare"
NONE = "none"

_BY_CATEGORY = {
    RiskScore.RiskCategory.VERY_HIGH: RELOCATE,
    RiskScore.RiskCategory.HIGH: PREPARE,
}

# Position of each category from least to most severe, for "lower risk than"
# comparisons. Follows the declaration order of RiskCategory.
CATEGORY_RANK = {category.value: rank for rank, category in enumerate(RiskScore.RiskCategory)}


def relocation_advisory(risk_category: str) -> str:
    """RELOCATE for very high risk, PREPARE for high, NONE otherwise."""
    return _BY_CATEGORY.get(risk_category, NONE)
