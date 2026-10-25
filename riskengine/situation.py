"""Everything known about each region on the newest scored day, in one place.

The briefing (riskengine/briefing.py) and the alert rules (alerts/rules.py)
both read the same facts about a region: its category and score today, the
change over the week, the rain against normal and the newest 7-day
forecast. This gathers them once, in a handful of queries for all regions.
"""

from __future__ import annotations

import datetime as dt

from django.db.models import Max
from django.utils import timezone

from riskengine.briefing import Place
from riskengine.context import CHANGE_DAYS, map_context
from riskengine.forecast import latest_issue
from riskengine.ml import infer as scorer
from riskengine.models import RiskForecast
from riskengine.scoring import start_of_day


def newest_places(scores_qs) -> tuple[dt.date | None, list[Place], dt.date | None]:
    """(the newest day scored, a Place per region scored that day, the forecast issue).

    `scores_qs` is a RiskScore queryset, already narrowed to the regions
    wanted, with `region` selected.
    """
    newest = scores_qs.aggregate(newest=Max("prediction_date"))["newest"]
    if newest is None:
        return None, [], latest_issue()

    day = timezone.localtime(newest).date()
    scores = list(scores_qs.filter(prediction_date=newest))
    earlier = scores_qs.filter(prediction_date=start_of_day(day - dt.timedelta(days=CHANGE_DAYS)))
    context = {row["region"]: row for row in map_context(scores, earlier, day)}

    week: dict[int, list] = {}
    issued = latest_issue()
    if issued:
        rows = RiskForecast.objects.filter(
            issued_on=issued, region_id__in=[s.region_id for s in scores], target_date__gt=day
        ).order_by("target_date")
        for row in rows:
            week.setdefault(row.region_id, []).append(
                (
                    row.target_date,
                    row.risk_category,
                    scorer.times_usual(row.predicted_value),
                    row.rain_forecast_max_24h,
                    row.rain_forecast_mm,
                )
            )

    places = [
        Place(
            region=score.region_id,
            name=score.region.name,
            category=score.risk_category,
            times_usual=scorer.times_usual(score.predicted_value),
            change_7d=context[score.region_id]["change_7d"],
            rain_ratio=context[score.region_id]["rain_30d_ratio"],
            week=week.get(score.region_id, []),
            county=score.region.county_name,
            level=score.region.level,
        )
        for score in scores
    ]
    return day, places, issued
