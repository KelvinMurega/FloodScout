"""More of the analytics page: how the system does in use, not on paper.

Everything here is worked out from what is already stored, each time the
page asks:

* `forecast_skill`  the 7-day forecasts against the score each day then got,
                    by how many days ahead they were made
* `kmd_agreement`   FloodScout's alerts against KMD's flood-related warnings,
                    county by county and day by day
* `category_flips`  how often a county's colour changes from one day to the next
* `rain_gaps`       regions missing days of rainfall in the last 30 days
* `rain_vs_normal`  the last 30 days of rain against what each county
                    normally gets in that month
* `api_usage`       Open-Meteo calls per day against the free daily limit
* `label_check`     the circle flood labels against flood water the Global
                    Flood Database mapped (the CSV `check_labels_gfd` wrote)

analytics.py holds the pipeline checks and the satellite check;
performance.py holds the model's evaluation.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import Counter, defaultdict
from pathlib import Path

from django.db.models import Count, Max, Q
from django.utils import timezone

from geodata.models import ApiUsage, EnvironmentalData, KmdWarning, Region
from geodata.services import open_meteo
from riskengine.advisory import CATEGORY_RANK
from riskengine.context import rain_totals
from riskengine.ml import infer as scorer
from riskengine.ml.infer import METRICS_PATH
from riskengine.models import RiskForecast, RiskScore
from riskengine.scoring import start_of_day

LABEL_CHECK_PATH = METRICS_PATH.with_name("gfd_label_check.csv")

# How far back the forecast check and the KMD comparison look.
WINDOW_DAYS = 90
# The other charts cover the last 30 days, like the trend chart.
RECENT_DAYS = 30
# A score at this category or above counts as a warning, as on the map.
WARN_FROM = "high"
# Shown in the lists of worst regions and jumpiest counties.
LIST_LENGTH = 12


def _local_day(moment: dt.datetime) -> dt.date:
    return timezone.localtime(moment).date()


def _warns(category: str) -> bool:
    return CATEGORY_RANK.get(category, 0) >= CATEGORY_RANK[WARN_FROM]


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _county_scores(since: dt.date, until: dt.date) -> dict[tuple[int, dt.date], tuple[float, str]]:
    """{(county id, day): (score, category)}, the day's last score where rescored."""
    rows = (
        RiskScore.objects.filter(
            region__level=Region.Level.COUNTY,
            prediction_date__gte=start_of_day(since),
            prediction_date__lt=start_of_day(until + dt.timedelta(days=1)),
        )
        .order_by("prediction_date", "id")
        .values_list("region_id", "prediction_date", "predicted_value", "risk_category")
    )
    return {(region, _local_day(when)): (value, category) for region, when, value, category in rows}


# --- Forecast skill ---------------------------------------------------------


def forecast_skill(today: dt.date | None = None, window: int = WINDOW_DAYS) -> dict:
    """How the county forecasts did, by days ahead, once the day came.

    Each forecast is set against the score the county got on the day it was
    for. "Same category" is the colour on the map matching; "within one"
    allows a neighbouring category. The warning columns treat High and above
    as a warning. Persistence is the plain alternative: assume the score on
    the day the forecast was made would stay the same all week.
    """
    today = today or timezone.localdate()
    since = today - dt.timedelta(days=window)
    actual = _county_scores(since, today)
    forecasts = RiskForecast.objects.filter(
        region__level=Region.Level.COUNTY, issued_on__gte=since, target_date__lte=today
    ).values_list("region_id", "issued_on", "target_date", "predicted_value", "risk_category")

    tally: dict[int, Counter] = defaultdict(Counter)
    errors: dict[int, float] = defaultdict(float)
    issues: set[dt.date] = set()
    for region, issued, target, value, category in forecasts:
        got = actual.get((region, target))
        if got is None:
            continue
        issues.add(issued)
        lead = (target - issued).days
        t = tally[lead]
        t["pairs"] += 1
        gap = abs(CATEGORY_RANK.get(category, 0) - CATEGORY_RANK.get(got[1], 0))
        t["same"] += gap == 0
        t["within_one"] += gap <= 1
        errors[lead] += abs(value - got[0])
        said, came = _warns(category), _warns(got[1])
        t["hits"] += said and came
        t["misses"] += came and not said
        t["false_alarms"] += said and not came
        before = actual.get((region, issued)) or actual.get((region, issued - dt.timedelta(days=1)))
        if before is not None:
            t["persistence_pairs"] += 1
            t["persistence_same"] += before[1] == got[1]

    leads = []
    for lead in sorted(tally):
        t = tally[lead]
        leads.append(
            {
                "lead_days": lead,
                "pairs": t["pairs"],
                "same_category": _ratio(t["same"], t["pairs"]),
                "within_one": _ratio(t["within_one"], t["pairs"]),
                "mean_abs_error": round(errors[lead] / t["pairs"], 4),
                "warnings_caught": _ratio(t["hits"], t["hits"] + t["misses"]),
                "warnings_that_came": _ratio(t["hits"], t["hits"] + t["false_alarms"]),
                "hits": t["hits"],
                "misses": t["misses"],
                "false_alarms": t["false_alarms"],
                "persistence_same": _ratio(t["persistence_same"], t["persistence_pairs"]),
            }
        )
    return {
        "since": since.isoformat(),
        "issues_checked": len(issues),
        "warn_from": WARN_FROM,
        "leads": leads,
    }


# --- Agreement with KMD -----------------------------------------------------


def _days(start: dt.date, end: dt.date):
    while start <= end:
        yield start
        start += dt.timedelta(days=1)


def kmd_agreement(today: dt.date | None = None, window: int = WINDOW_DAYS) -> dict:
    """County-days with a FloodScout alert, a KMD flood-related warning, or both.

    A FloodScout alert on a sub-county counts for its county. A KMD warning
    counts for every county its area reaches, from its onset to its end (or
    KmdWarning.OPEN_ENDED after onset when it gives none). Withdrawn
    warnings are left out, as on the map. KMD keeps no history, so the
    record starts on the first warning FloodScout stored.
    """
    from alerts.models import Alert  # alerts imports riskengine; import here

    today = today or timezone.localdate()
    since = today - dt.timedelta(days=window)

    ours: set[tuple[str, dt.date]] = set()
    # Names only: a region's boundary is heavy and not needed here.
    alerts = (
        Alert.objects.filter(issued_at__lt=start_of_day(today + dt.timedelta(days=1)))
        .filter(Q(ended_at__isnull=True) | Q(ended_at__gte=start_of_day(since)))
        .values_list("region__county_name", "issued_at", "ended_at", "expires", "status")
    )
    for county, issued, ended, expires, status in alerts:
        if ended:
            end = _local_day(ended)
        elif status == Alert.Status.ACTIVE:
            end = today
        else:
            end = _local_day(expires)
        for d in _days(max(_local_day(issued), since), min(end, today)):
            ours.add((county, d))

    theirs: set[tuple[str, dt.date]] = set()
    warnings = []
    flood = KmdWarning.objects.filter(flood_related=True, cancelled=False)
    first = KmdWarning.objects.order_by("sent").values_list("sent", flat=True).first()
    reached: dict[int, set[str]] = defaultdict(set)
    for warning_id, county in KmdWarning.regions.through.objects.filter(
        kmdwarning__in=flood
    ).values_list("kmdwarning_id", "region__county_name"):
        reached[warning_id].add(county)
    for warning in flood.filter(onset__lt=start_of_day(today + dt.timedelta(days=1))).defer("geom"):
        start = _local_day(warning.onset)
        end = _local_day(warning.expires) if warning.expires else start + KmdWarning.OPEN_ENDED
        if end < since:
            continue
        counties = sorted(reached[warning.pk])
        covered = list(_days(max(start, since), min(end, today)))
        for county in counties:
            for d in covered:
                theirs.add((county, d))
        matched = sorted({c for c in counties for d in covered if (c, d) in ours})
        warnings.append(
            {
                "event": warning.event,
                "headline": warning.headline[:200],
                "severity": warning.severity,
                "onset": start.isoformat(),
                "ends": end.isoformat(),
                "counties": len(counties),
                "counties_with_our_alert": len(matched),
                "web": warning.web or warning.url,
            }
        )
    warnings.sort(key=lambda w: w["onset"], reverse=True)

    both = ours & theirs
    daily = []
    ours_by_day: dict[dt.date, set[str]] = defaultdict(set)
    theirs_by_day: dict[dt.date, set[str]] = defaultdict(set)
    for county, d in ours:
        ours_by_day[d].add(county)
    for county, d in theirs:
        theirs_by_day[d].add(county)
    for d in _days(since, today):
        o, t = ours_by_day[d], theirs_by_day[d]
        daily.append({"date": d.isoformat(), "both": len(o & t), "ours_only": len(o - t), "kmd_only": len(t - o)})
    either = len(ours | theirs)
    return {
        "since": since.isoformat(),
        "kmd_first_stored": _local_day(first).isoformat() if first else None,
        "both": len(both),
        "ours_only": len(ours - theirs),
        "kmd_only": len(theirs - ours),
        "agreement": _ratio(len(both), either),
        "daily": daily,
        "warnings": warnings[:LIST_LENGTH * 2],
    }


# --- How jumpy the map is ---------------------------------------------------


def category_flips(last_day: dt.date | None, days: int = RECENT_DAYS) -> dict:
    """Counties whose category changed from the day before, per day.

    A model change re-scores every county at once, so those days are flagged
    rather than counted as the weather.
    """
    if last_day is None:
        return {"daily": [], "jumpiest": [], "counties": 0}
    since = last_day - dt.timedelta(days=days)
    scores = _county_scores(since, last_day)
    versions: dict[dt.date, set] = defaultdict(set)
    for when, version in (
        RiskScore.objects.filter(
            region__level=Region.Level.COUNTY,
            prediction_date__gte=start_of_day(since),
            prediction_date__lt=start_of_day(last_day + dt.timedelta(days=1)),
        )
        .values_list("prediction_date", "model_version")
        .distinct()
    ):
        versions[_local_day(when)].add(version)
    names = dict(Region.objects.filter(level=Region.Level.COUNTY).values_list("id", "name"))

    by_day: dict[dt.date, dict[int, str]] = defaultdict(dict)
    for (region, d), (_, category) in scores.items():
        by_day[d][region] = category
    per_county: Counter = Counter()
    daily = []
    ordered = sorted(by_day)
    for previous, current in zip(ordered, ordered[1:]):
        up = down = 0
        for region, category in by_day[current].items():
            before = by_day[previous].get(region)
            if before is None or before == category:
                continue
            per_county[region] += 1
            if CATEGORY_RANK.get(category, 0) > CATEGORY_RANK.get(before, 0):
                up += 1
            else:
                down += 1
        daily.append(
            {
                "date": current.isoformat(),
                "up": up,
                "down": down,
                "scored": len(by_day[current]),
                "model_changed": bool(versions[previous]) and versions[previous] != versions[current],
            }
        )
    days_compared = len(daily)
    return {
        "counties": len(names),
        "days_compared": days_compared,
        "daily": daily,
        "jumpiest": [
            {"county": names.get(region, str(region)), "changes": n}
            for region, n in per_county.most_common(LIST_LENGTH)
        ],
        "never_changed": len(names) - len(per_county),
    }


# --- Rainfall: gaps and against normal ---------------------------------------


def rain_gaps(days: int = RECENT_DAYS) -> dict:
    """Regions missing days of rainfall in the `days` up to the newest stored day."""
    last = EnvironmentalData.objects.aggregate(last=Max("record_date"))["last"]
    regions = list(Region.objects.values_list("id", "name", "level", "county_name"))
    if last is None:
        return {"last": None, "days": days, "regions": len(regions), "complete": 0, "daily": [], "worst": []}
    since = last - dt.timedelta(days=days - 1)
    window = EnvironmentalData.objects.filter(record_date__gte=since, record_date__lte=last)
    stored = dict(window.values_list("region_id").annotate(n=Count("id")).values_list("region_id", "n"))
    per_day = dict(
        window.values_list("record_date").annotate(n=Count("region_id", distinct=True)).values_list("record_date", "n")
    )
    missing = []
    for region, name, level, county in regions:
        gap = days - min(days, stored.get(region, 0))
        if gap:
            missing.append({"region": name, "level": level, "county": county, "missing_days": gap})
    missing.sort(key=lambda row: (-row["missing_days"], row["region"]))
    return {
        "last": last.isoformat(),
        "days": days,
        "regions": len(regions),
        "complete": len(regions) - len(missing),
        "with_gaps": len(missing),
        "missing_days": sum(row["missing_days"] for row in missing),
        "daily": [{"date": d.isoformat(), "regions": per_day.get(d, 0)} for d in _days(since, last)],
        "worst": missing[:LIST_LENGTH],
    }


def rain_vs_normal() -> dict | None:
    """Each county's last 30 days of rain against its normal for the month.

    The normals are the ones the model was trained with, so this is the
    model's main input (rain_30d_ratio) in terms anyone can read. Null while
    no trained model is loaded.
    """
    model = scorer._MODEL
    last = EnvironmentalData.objects.aggregate(last=Max("record_date"))["last"]
    if not model or last is None:
        return None
    counties = list(Region.objects.filter(level=Region.Level.COUNTY).values_list("id", "name"))
    totals = rain_totals([pk for pk, _ in counties], last)
    rows = []
    for pk, name in counties:
        rain = totals.get(pk)
        normal = model["normals"].get(name, {}).get(last.month)
        if rain is None or not normal:
            continue
        rows.append(
            {"county": name, "rain_30d": round(rain, 1), "normal": round(normal, 1), "ratio": round(rain / normal, 2)}
        )
    rows.sort(key=lambda row: -row["ratio"])
    return {
        "date": last.isoformat(),
        "counties": rows,
        "above_normal": sum(1 for row in rows if row["ratio"] > 1.2),
        "below_normal": sum(1 for row in rows if row["ratio"] < 0.8),
    }


# --- Open-Meteo quota --------------------------------------------------------


def api_usage(today: dt.date | None = None, days: int = RECENT_DAYS) -> dict:
    """Open-Meteo calls per day, as counted by geodata/services/open_meteo.py."""
    today = today or timezone.localdate()
    since = today - dt.timedelta(days=days - 1)
    rows = {
        row.day: row
        for row in ApiUsage.objects.filter(service=open_meteo.SERVICE, day__gte=since, day__lte=today)
    }
    limit = open_meteo.FREE_CALLS_PER_DAY
    daily = [
        {
            "date": d.isoformat(),
            "calls": round(rows[d].calls) if d in rows else 0,
            "requests": rows[d].requests if d in rows else 0,
            "rate_limited": rows[d].rate_limited if d in rows else 0,
            "failed": rows[d].failed if d in rows else 0,
        }
        for d in _days(since, today)
    ]
    first = (
        ApiUsage.objects.filter(service=open_meteo.SERVICE).order_by("day").values_list("day", flat=True).first()
    )
    return {
        "service": "Open-Meteo",
        "limit_per_day": limit,
        "limit_per_hour": open_meteo.FREE_CALLS_PER_HOUR,
        "limit_per_minute": open_meteo.FREE_CALLS_PER_MINUTE,
        "counting_since": first.isoformat() if first else None,
        "daily": daily,
        "today": daily[-1]["calls"],
        "peak": max((row["calls"] for row in daily), default=0),
        "days_over_80_percent": sum(1 for row in daily if row["calls"] >= 0.8 * limit),
        "rate_limited": sum(row["rate_limited"] for row in daily),
        "failed": sum(row["failed"] for row in daily),
    }


# --- Label check -------------------------------------------------------------


def label_check(path: Path = LABEL_CHECK_PATH) -> dict | None:
    """The circle labels against Global Flood Database water, per event.

    "Labelled" is a county the event's circle touches, which training calls
    flooded. "Mapped wet" is a county where the database mapped flood water.
    Labelled but dry may be a false label (or water the satellite missed);
    wet but unlabelled is a flood the labels left out.
    """
    if not path.exists():
        return None
    events: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            event = events.setdefault(
                row["event"],
                {"event": row["event"], "began": row["began"], "labelled": 0, "wet": 0, "both": 0},
            )
            labelled, wet = row["under_circle"] == "1", row["mapped_wet"] == "1"
            event["labelled"] += labelled
            event["wet"] += wet
            event["both"] += labelled and wet
    rows = sorted(events.values(), key=lambda e: e["began"])
    labelled = sum(e["labelled"] for e in rows)
    wet = sum(e["wet"] for e in rows)
    both = sum(e["both"] for e in rows)
    return {
        "events": rows,
        "labelled": labelled,
        "wet": wet,
        "both": both,
        "labels_with_water": _ratio(both, labelled),
        "water_labelled": _ratio(both, wet),
    }
