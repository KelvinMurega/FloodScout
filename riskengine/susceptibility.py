"""What the shipped susceptibility layer says about a county or sub-county.

The hazard model says *when* a county is likely to flood. The susceptibility
layer (riskengine/ml/SUSCEPTIBILITY_PROTOCOL.md) says *where inside it* the
water goes, from the ground alone: height above the nearest river, upstream
area, distance to a river, slope and the 100-year flood zone. It does not
change with the weather.

`build_susceptibility` sums it up per region into `regions.json` beside the
run's model. This module only reads that file, so the server needs neither
the pixels nor Earth Engine. Until the file exists every lookup is None and
the dashboard says nothing about it.

The UNOSAT test result (`unosat_test.json`, written by the one look) is read
too, so the dashboard can say how far the layer has been checked.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings

SHIPPED_RUN = (
    Path(settings.BASE_DIR) / "riskengine" / "ml" / "artifacts" / "susceptibility" / "20261102T1246Z_77a8329"
)


@lru_cache(maxsize=4)
def _load(run: Path) -> dict | None:
    path = run / "regions.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    data["by_place"] = {(row["level"], row["county"], row["name"]): row for row in data["rows"]}
    test = run / "unosat_test.json"
    data["test"] = json.loads(test.read_text()) if test.exists() else None
    return data


def for_region(region, run: Path | None = None) -> dict | None:
    """The layer's summary for `region`, with Kenya's line and the test result; None if unknown."""
    data = _load(run or SHIPPED_RUN)
    if data is None:
        return None
    row = data["by_place"].get((region.level, region.county_name, region.name))
    if row is None:
        return None
    county = data["by_place"].get(("county", region.county_name, region.county_name))
    test = data["test"]
    return {
        "run": data["run"],
        "high_percentile": data["high_percentile"],
        "mean": row["mean"],
        "high_share": row["high_share"],
        "expected_km2": row["expected_km2"],
        "land_km2": row["land_km2"],
        "rank_in_kenya": row.get("rank_in_kenya"),
        "of": row.get("of"),
        "county_high_share": county["high_share"] if county and region.level != "county" else None,
        "checked": (
            {
                "sub_county_roc_auc": test["sub_county"]["model"]["within_county_roc_auc"],
                "engine_roc_auc": test["engine_sub_county_roc_auc"],
                "date": test["date"],
            }
            if test
            else None
        ),
    }
