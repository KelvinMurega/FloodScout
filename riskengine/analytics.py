"""The administrator's analytics page, apart from the model's evaluation.

Two things live here:

* `system_health` — is the pipeline keeping the map current? Worked out from
  the database each time it is asked: when rainfall and scores were last
  stored, whether every region was scored, which model wrote the scores, and
  how the categories have moved over the last weeks.
* `satellite_check` — the scores the model gives on days when UNOSAT mapped
  floodwater from satellite images, set against which counties were wet.
  Read from the CSV `validate_unosat` wrote beside the model.

performance.py covers the model's record on the years it was evaluated on.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path

from django.db.models import Count, Max, Min
from django.db.models.functions import TruncDate
from django.utils import timezone

from geodata.models import EnvironmentalData, Region
from riskengine.ml import infer as scorer
from riskengine.ml.infer import HEURISTIC_VERSION, METRICS_PATH
from riskengine.models import RiskOutlook, RiskScore
from riskengine.scoring import start_of_day

UNOSAT_PATH = METRICS_PATH.with_name("unosat_validation.csv")

# How many days of scores the trend chart covers.
TREND_DAYS = 30

# The order statuses are ranked in; the page shows the worst one overall.
STATUS_RANK = {"ok": 0, "warning": 1, "degraded": 2}

# Days since the last stored day before a feed counts as late. Scores are
# written for today by each daily run, so before that day's run the newest
# are a day old. Rainfall is fetched only for days that have ended
# (forecast_days=0), so its newest day is always one further back:
# RAINFALL_LAG is added to its limits. One more than that means a scheduled
# refresh did not run.
FRESH_DAYS = 1
STALE_DAYS = 3
RAINFALL_LAG = 1

CATEGORIES = [choice for choice, _ in RiskScore.RiskCategory.choices]


def _freshness(
    name: str, last: dt.date | None, today: dt.date, what: str, lag: int = 0
) -> dict:
    if last is None:
        return {"name": name, "status": "degraded", "detail": f"No {what} stored yet."}
    age = (today - last).days
    if age <= FRESH_DAYS + lag:
        status = "ok"
    elif age <= STALE_DAYS + lag:
        status = "warning"
    else:
        status = "degraded"
    when = "today" if age == 0 else "yesterday" if age == 1 else f"{age} days ago"
    return {"name": name, "status": status, "detail": f"Latest {what} is for {last:%d %b %Y}, {when}."}


def _score_day(score_date) -> dt.date:
    return timezone.localtime(score_date).date()


def system_health(today: dt.date | None = None) -> dict:
    today = today or timezone.localdate()

    regions = dict(
        Region.objects.values_list("level").annotate(n=Count("id")).values_list("level", "n")
    )
    total_regions = sum(regions.values())

    newest = RiskScore.objects.aggregate(newest=Max("prediction_date"))["newest"]
    last_score_day = _score_day(newest) if newest else None
    latest = (
        RiskScore.objects.filter(
            prediction_date__gte=start_of_day(last_score_day),
            prediction_date__lt=start_of_day(last_score_day + dt.timedelta(days=1)),
        )
        if last_score_day
        else RiskScore.objects.none()
    )
    scored = dict(
        latest.values_list("region__level").annotate(n=Count("id")).values_list("region__level", "n")
    )
    latest_categories = defaultdict(lambda: dict.fromkeys(CATEGORIES, 0))
    for level, category, n in (
        latest.values_list("region__level", "risk_category")
        .annotate(n=Count("id"))
        .values_list("region__level", "risk_category", "n")
    ):
        latest_categories[level][category] = n
    versions = list(latest.order_by().values_list("model_version", flat=True).distinct())

    rain = EnvironmentalData.objects.aggregate(
        first=Min("record_date"), last=Max("record_date"), rows=Count("id")
    )
    rain_regions = (
        EnvironmentalData.objects.filter(record_date=rain["last"]).count() if rain["last"] else 0
    )

    checks = [
        _freshness("Rainfall feed", rain["last"], today, "rainfall", lag=RAINFALL_LAG),
        _freshness("Risk scores", last_score_day, today, "set of scores"),
    ]

    scored_total = sum(scored.values())
    if total_regions and scored_total == total_regions:
        coverage = {"status": "ok", "detail": f"All {total_regions} regions have a score."}
    elif total_regions and scored_total >= 0.9 * total_regions:
        coverage = {
            "status": "warning",
            "detail": f"{scored_total} of {total_regions} regions have a score.",
        }
    else:
        coverage = {
            "status": "degraded",
            "detail": f"{scored_total} of {total_regions} regions have a score.",
        }
    checks.append({"name": "Coverage", **coverage})

    loaded = scorer.MODEL_VERSION if scorer._MODEL else None
    if loaded is None:
        model_check = {
            "status": "degraded",
            "detail": f"No trained model is loaded; scores come from {HEURISTIC_VERSION}.",
        }
    elif versions and versions != [loaded]:
        model_check = {
            "status": "warning",
            "detail": f"The latest scores were written by {', '.join(versions)}, "
            f"but {loaded} is loaded now. Rescore to bring them in line.",
        }
    else:
        model_check = {"status": "ok", "detail": f"{loaded} is loaded and wrote the latest scores."}
    checks.append({"name": "Model", **model_check})

    overall = max((check["status"] for check in checks), key=STATUS_RANK.__getitem__)

    return {
        "today": today.isoformat(),
        "status": overall,
        "checks": checks,
        "rules": (
            f"Scores are ok up to {FRESH_DAYS} day old, a warning up to {STALE_DAYS} "
            f"days and degraded after. Rainfall stops at yesterday, so it is allowed "
            f"{RAINFALL_LAG} day more on each. Coverage is a warning below every "
            "region and degraded below 90%. The model is degraded when no trained model "
            "is loaded and a warning when the latest scores came from another version."
        ),
        "regions": {"total": total_regions, "by_level": regions},
        "latest_scores": {
            "date": last_score_day.isoformat() if last_score_day else None,
            "scored": scored,
            "categories": dict(latest_categories),
            "model_versions": versions,
        },
        "loaded_model": loaded,
        "rainfall": {
            "first": rain["first"].isoformat() if rain["first"] else None,
            "last": rain["last"].isoformat() if rain["last"] else None,
            "rows": rain["rows"],
            "regions_on_last_day": rain_regions,
        },
        "outlook": {
            "months": [
                month.isoformat()
                for month in RiskOutlook.objects.order_by("month")
                .values_list("month", flat=True)
                .distinct()
            ],
            "issued_on": (
                issued.isoformat()
                if (issued := RiskOutlook.objects.aggregate(i=Max("issued_on"))["i"])
                else None
            ),
        },
        "trend": _trend(last_score_day),
    }


def _trend(last_day: dt.date | None) -> list[dict]:
    """County categories per day for the last TREND_DAYS days with scores."""
    if last_day is None:
        return []
    since = start_of_day(last_day - dt.timedelta(days=TREND_DAYS - 1))
    rows = (
        RiskScore.objects.filter(
            region__level=Region.Level.COUNTY, prediction_date__gte=since
        )
        .annotate(day=TruncDate("prediction_date"))
        .values_list("day", "risk_category", "model_version")
        .annotate(n=Count("id"))
        .order_by("day")
    )
    by_day: dict[dt.date, dict] = {}
    versions: dict[dt.date, set] = defaultdict(set)
    for day, category, version, n in rows:
        by_day.setdefault(day, dict.fromkeys(CATEGORIES, 0))[category] += n
        versions[day].add(version)
    return [
        {"date": day.isoformat(), **counts, "model_versions": sorted(versions[day])}
        for day, counts in sorted(by_day.items())
    ]


def _rank_auc(wet: list[float], dry: list[float]) -> float | None:
    """The chance a flooded county got a higher score than a dry one (ROC AUC).

    Counted pair by pair, ties as half; fine for the tens of counties here.
    """
    if not wet or not dry:
        return None
    wins = sum((w > d) + 0.5 * (w == d) for w in wet for d in dry)
    return round(wins / (len(wet) * len(dry)), 4)


def satellite_check(path: Path = UNOSAT_PATH) -> list[dict] | None:
    """Per UNOSAT event: did the counties with mapped floodwater score higher?"""
    if not path.exists():
        return None
    events: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            event = events.setdefault(
                row["event"], {"event": row["event"], "date": row["date"], "counties": []}
            )
            event["counties"].append(
                {
                    "county": row["county"],
                    "flooded_km2": float(row["flooded_km2"]),
                    "flooded": row["flooded"] == "1",
                    "probability": float(row["probability"]),
                    "risk_category": row["risk_category"],
                }
            )
    result = []
    for event in events.values():
        wet = [c["probability"] for c in event["counties"] if c["flooded"]]
        dry = [c["probability"] for c in event["counties"] if not c["flooded"]]
        high = {"high", "very_high"}
        event["counties"].sort(key=lambda c: -c["probability"])
        result.append(
            {
                **event,
                "flooded_counties": len(wet),
                "dry_counties": len(dry),
                "mean_probability_flooded": round(sum(wet) / len(wet), 4) if wet else None,
                "mean_probability_dry": round(sum(dry) / len(dry), 4) if dry else None,
                "roc_auc": _rank_auc(wet, dry),
                "flooded_at_high_or_above": sum(
                    1 for c in event["counties"] if c["flooded"] and c["risk_category"] in high
                ),
                "dry_at_high_or_above": sum(
                    1
                    for c in event["counties"]
                    if not c["flooded"] and c["risk_category"] in high
                ),
            }
        )
    return result
