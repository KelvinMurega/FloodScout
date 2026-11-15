"""How the model is doing now: its daily scores against floods reported since.

The figures in metrics.json are fixed when the model is trained. This is
the record that keeps growing: each county-month since scoring began is
counted the way the training run counted them, so the two can be set side
by side.

* A county-month was **warned** when the county's score on the last day
  scored that month was High or Very High: the category the map showed,
  not the raw probability. The two differ when the window was dry. The dry
  gate (riskengine/dry.py) caps the category at Moderate and leaves the
  probability as it was, so a county shown as Moderate can carry a chance
  above the warning threshold. Counting the probability scored those
  months as warnings nobody was shown, and overcounted false alarms. The
  bands start High at the training run's warning threshold
  (`error_breakdown.threshold` in metrics.json), so without the gate the
  two counts agree. Only scores from the model in use count.
* It **flooded** when a public record of that month names the county: a
  ReliefWeb flood report, a news report of flood damage, or a GDACS flood
  event under way in the month (alerts come from rain, the records from
  people on the ground). The news and GDACS records are the ones the map's
  past months are checked against (riskengine/outcome.py), fetched every
  day by `refresh_scores`. ReliefWeb answers only approved app names, and
  with it alone a month whose floods it did not cover counted every
  warning as a false alarm. A record that names no county is left out.

The records are what make this coarse. A flood nobody wrote about counts as a
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
from riskengine.models import ObservedFlood, ReportedFlood, RiskScore
from riskengine.scoring import start_of_day

# Shown when the training run did not record its threshold.
DEFAULT_THRESHOLD = 0.1

# The categories the map shows as a warning, after the dry gate.
WARNING_CATEGORIES = {RiskScore.RiskCategory.HIGH, RiskScore.RiskCategory.VERY_HIGH}


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


def _observed_months(flood: ObservedFlood) -> list[dt.date]:
    """The months a news report or GDACS event counts in.

    A news report counts in the month it was published. A GDACS event counts
    in every month from its start to its end, as in riskengine/outcome.py.
    """
    first = _month(flood.date)
    last = _month(flood.ended) if flood.ended and flood.ended > flood.date else first
    months = [first]
    while months[-1] < last:
        month = months[-1]
        months.append(month.replace(year=month.year + month.month // 12, month=month.month % 12 + 1))
    return months


def live_record(errors: dict | None = None, today: dt.date | None = None) -> dict:
    """The scorecard for the analytics page's "Where it fails" tab."""
    today = today or timezone.localdate()
    errors = errors or {}
    threshold = errors.get("threshold") or DEFAULT_THRESHOLD
    observed = list(
        ObservedFlood.objects.exclude(county=None).select_related("county").order_by("-date", "county__name")
    )
    # Scored against something once either record is coming in.
    switched_on = bool(settings.RELIEFWEB_APPNAME) or bool(observed)
    this_month = _month(today)

    # The training rows are calendar months, and their main input is the
    # month's 30 days of rain, so the score that matches one is the county's
    # last of the month: its window is the month. The highest score in a
    # month reads a window straddling two and would warn nearly every time.
    # Only scores from the model in use are counted.
    month_end: dict[tuple[str, dt.date], str] = {}
    scores = (
        RiskScore.objects.filter(
            region__level=Region.Level.COUNTY,
            model_version=MODEL_VERSION,
            prediction_date__lt=start_of_day(this_month),
        )
        .order_by("prediction_date")
        .values_list("region__name", "prediction_date", "risk_category")
    )
    for county, when, category in scores:
        month_end[(county, _month(timezone.localtime(when).date()))] = category
    warned = {key: category in WARNING_CATEGORIES for key, category in month_end.items()}

    reports = list(
        ReportedFlood.objects.select_related("county").order_by("-published", "county__name")
    )
    flooded = {(r.county.name, _month(r.published)) for r in reports}
    for flood in observed:
        flooded.update((flood.county.name, month) for month in _observed_months(flood))

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
        "reliefweb": bool(settings.RELIEFWEB_APPNAME),
        "reports": _records(reports, observed)[:100],
    }



def _records(reports, observed) -> list[dict]:
    """Every record that named a county, newest first, for the page's list."""
    rows = [
        {
            "record": "reliefweb",
            "county": r.county.name,
            "published": r.published.isoformat(),
            "title": r.title,
            "url": r.url,
            "source": r.source,
        }
        for r in reports
    ] + [
        {
            "record": f.source,
            "county": f.county.name,
            "published": f.date.isoformat(),
            "title": f.title,
            "url": f.url,
            "source": f.publisher,
        }
        for f in observed
    ]
    rows.sort(key=lambda row: row["county"])
    rows.sort(key=lambda row: row["published"], reverse=True)
    return rows
