"""What the map's themes colour by, besides the risk category.

For each region scored on a day, three things a reader can check against
the risk:

  times_usual       the score as a multiple of the usual chance (as everywhere)
  change_7d         that multiple now minus seven days before: is risk rising?
  rain_30d_ratio    rain in the 30 days to the date over what the region's
                    county normally gets in that month: the main driver,
                    in terms anyone can read

They are worked out from what is stored, in a handful of queries for all
the regions at once, so the dashboard can colour 290 sub-counties without
asking about each one.
"""

from __future__ import annotations

import datetime as dt

from django.db.models import Count, Sum

from geodata.models import EnvironmentalData
from riskengine.ml import infer as scorer
from riskengine.ml.features import MAX_MISSING_DAYS, WINDOW_DAYS

CHANGE_DAYS = 7


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def rain_totals(region_ids, day: dt.date) -> dict[int, float]:
    """{region id: mm in the WINDOW_DAYS to `day`}, for regions missing few enough days."""
    rows = (
        EnvironmentalData.objects.filter(
            region_id__in=region_ids,
            record_date__gt=day - dt.timedelta(days=WINDOW_DAYS),
            record_date__lte=day,
        )
        .values("region_id")
        .annotate(total=Sum("rainfall_intensity"), days=Count("id"))
    )
    return {
        row["region_id"]: row["total"]
        for row in rows
        if row["days"] >= WINDOW_DAYS - MAX_MISSING_DAYS
    }


def map_context(scores, earlier_scores, day: dt.date, model=None) -> list[dict]:
    """One row per score in `scores`, all for `day`.

    `earlier_scores` are the same regions' scores CHANGE_DAYS before, where
    there are any.
    """
    model = scorer._MODEL if model is None else model
    normals = model["normals"] if model else {}
    before = {score.region_id: score.predicted_value for score in earlier_scores}
    rain = rain_totals([score.region_id for score in scores], day)

    rows = []
    for score in scores:
        now = scorer.times_usual(score.predicted_value)
        then = scorer.times_usual(before.get(score.region_id))
        rain_30d = rain.get(score.region_id)
        normal = normals.get(score.region.county_name, {}).get(day.month)
        rows.append(
            {
                "region": score.region_id,
                "times_usual": now,
                "change_7d": _round(now - then, 1)
                if now is not None and then is not None
                else None,
                "rain_30d": _round(rain_30d, 1),
                "rain_30d_normal": _round(normal, 1),
                "rain_30d_ratio": _round(rain_30d / normal)
                if rain_30d is not None and normal
                else None,
            }
        )
    return rows
