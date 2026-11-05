"""How the model is doing now: its daily scores against floods reported since.

The figures in metrics.json are fixed when the model is trained. This is
the record that keeps growing: each county-month since scoring began is
counted the way the training run counted them, so the two can be set side
by side.

* A county-month was **warned** when the county's score on the last day
  scored that month reached the training run's warning threshold
  (`error_breakdown.threshold` in metrics.json). Only scores from the model
  in use count.
* It **flooded** when a ReliefWeb flood report published that month named
  the county (alerts come from rain, the reports from people on the ground).

Reports are what make this coarse. A flood nobody wrote about counts as a
false alarm, and a report that names a county for another reason counts as
a flood. So the catch rate is the firmer number; false alarms are an upper
bound. Months still under way are left out: their floods may not be
reported yet.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from django.conf import settings
from django.utils import timezone

from geodata.models import Region
from riskengine.ml.infer import MODEL_VERSION
from riskengine.models import ReportedFlood, RiskScore
from riskengine.scoring import start_of_day

# Shown when the training run did not record its threshold.
DEFAULT_THRESHOLD = 0.1


def _month(day: dt.date) -> dt.date:
    return day.replace(day=1)


def _training_rates(errors: dict) -> dict | None:
    rows = errors.get("by_month") or []
    floods = sum(r["floods"] for r in rows)
    caught = sum(r["caught"] for r in rows)
    alarms = caught + sum(r["false_alarms"] for r in rows)
    if not floods:
        return None
    return {
        "recall": round(caught / floods, 4),
        "precision": round(caught / alarms, 4) if alarms else None,
    }


def live_record(errors: dict | None = None, today: dt.date | None = None) -> dict:
    """The scorecard for the analytics page's "Where it fails" tab."""
    today = today or timezone.localdate()
    errors = errors or {}
    threshold = errors.get("threshold") or DEFAULT_THRESHOLD
    switched_on = bool(settings.RELIEFWEB_APPNAME)
    this_month = _month(today)

    # The training rows are calendar months, and their main input is the
    # month's 30 days of rain, so the score that matches one is the county's
    # last of the month: its window is the month. The highest score in a
    # month reads a window straddling two and would warn nearly every time.
    # Only scores from the model in use are counted.
    month_end: dict[tuple[str, dt.date], float] = {}
    scores = (
        RiskScore.objects.filter(
            region__level=Region.Level.COUNTY,
            model_version=MODEL_VERSION,
            prediction_date__lt=start_of_day(this_month),
        )
        .order_by("prediction_date")
        .values_list("region__name", "prediction_date", "predicted_value")
    )
    for county, when, value in scores:
        month_end[(county, _month(timezone.localtime(when).date()))] = value
    warned = {key: value >= threshold for key, value in month_end.items()}

    reports = list(
        ReportedFlood.objects.select_related("county").order_by("-published", "county__name")
    )
    flooded = {(r.county.name, _month(r.published)) for r in reports}

    keys = ("county_months", "floods", "caught", "missed", "false_alarms")
    by_month = defaultdict(lambda: dict.fromkeys(keys, 0))
    for (county, month), alarm in warned.items():
        row = by_month[month]
        row["county_months"] += 1
        flood = (county, month) in flooded
        row["floods"] += flood
        row["caught"] += flood and alarm
        row["missed"] += flood and not alarm
        row["false_alarms"] += alarm and not flood

    months = [{"month": month.isoformat(), **by_month[month]} for month in sorted(by_month)]
    totals = {key: sum(row[key] for row in months) for key in keys}
    alarms = totals["caught"] + totals["false_alarms"]
    totals["recall"] = round(totals["caught"] / totals["floods"], 4) if totals["floods"] else None
    totals["precision"] = round(totals["caught"] / alarms, 4) if alarms else None

    return {
        "switched_on": switched_on,
        "threshold": threshold,
        "first_month": months[0]["month"] if months else None,
        "totals": totals,
        "by_month": months,
        "training": _training_rates(errors),
        "reports": [
            {
                "county": r.county.name,
                "published": r.published.isoformat(),
                "title": r.title,
                "url": r.url,
                "source": r.source,
            }
            for r in reports[:100]
        ],
    }
