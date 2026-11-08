"""The alert service in use, for the analytics page's Alerts tab.

Counts only: nothing here names a subscriber. Worked out from the alert
runs, the alerts, the deliveries, the subscriptions and the masked records
alerts/stopping.py keeps of those who stopped.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from django.db.models import Count, Sum
from django.db.models.functions import TruncWeek
from django.utils import timezone

from .models import Alert, AlertRun, Delivery, Level, Subscriber, Unsubscription

WEEKS = 12
# An alert that ends within this long of being issued counts as short-lived:
# raised and then dropped again, the kind that wears out people's trust.
SHORT_LIVED = dt.timedelta(days=2)
# How long alerts lasted, in days, as the chart's buckets: [low, high).
DURATION_BUCKETS = [(0, 1), (1, 2), (2, 3), (3, 7), (7, 14), (14, None)]


def _week(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def _weeks(first: dt.date, last: dt.date) -> list[dt.date]:
    out, week = [], _week(first)
    while week <= last:
        out.append(week)
        week += dt.timedelta(days=7)
    return out


def _as_date(value) -> dt.date:
    return timezone.localtime(value).date() if isinstance(value, dt.datetime) else value


def _bucket_label(low: int, high: int | None) -> str:
    if high is None:
        return f"{low}+ days"
    if low == 0:
        return f"Under {high} day" + ("" if high == 1 else "s")
    return f"{low}–{high} days"


def alert_activity(today: dt.date | None = None, weeks: int = WEEKS) -> dict:
    today = today or timezone.localdate()
    weeks_shown = _weeks(today - dt.timedelta(weeks=weeks - 1), today)
    since = weeks_shown[0]
    since_moment = timezone.make_aware(dt.datetime.combine(since, dt.time.min))

    # Runs: what the rules did each week.
    runs = {
        _as_date(row["week"]): row
        for row in AlertRun.objects.filter(started_at__gte=since_moment)
        .annotate(week=TruncWeek("started_at"))
        .values("week")
        .annotate(
            runs=Count("id"),
            issued=Sum("issued"),
            upgraded=Sum("upgraded"),
            ended=Sum("ended"),
        )
    }

    # Deliveries: email outcomes each week.
    delivered: dict[dt.date, Counter] = defaultdict(Counter)
    for row in (
        Delivery.objects.filter(sent_at__gte=since_moment)
        .annotate(week=TruncWeek("sent_at"))
        .values("week", "outcome")
        .annotate(n=Count("id"))
    ):
        delivered[_as_date(row["week"])][row["outcome"]] += row["n"]

    # Subscribers: new confirmations and stops each week, and how many were
    # signed up at each week's end. A stopped subscription is deleted, so
    # those still counted at a week's end are the ones still here plus the
    # ones that stopped later.
    confirmed = Counter(
        _week(_as_date(when))
        for when in Subscriber.objects.filter(confirmed=True, confirmed_at__gte=since_moment).values_list(
            "confirmed_at", flat=True
        )
    )
    stopped_rows = list(Unsubscription.objects.values_list("stopped_at", "subscribed_at", "how"))
    stopped = Counter(_week(_as_date(when)) for when, _, _ in stopped_rows if _as_date(when) >= since)
    still_here = list(
        Subscriber.objects.filter(confirmed=True).values_list("confirmed_at", "created_at")
    )

    def signed_up_at(end: dt.date) -> int:
        here = sum(1 for conf, made in still_here if _as_date(conf or made) <= end)
        left = sum(
            1
            for when, began, _ in stopped_rows
            if began is not None and _as_date(began) <= end < _as_date(when)
        )
        return here + left

    weekly = []
    for week in weeks_shown:
        run = runs.get(week, {})
        d = delivered.get(week, Counter())
        sent, failed = d[Delivery.Outcome.SENT], d[Delivery.Outcome.FAILED]
        weekly.append(
            {
                "week": week.isoformat(),
                "runs": run.get("runs") or 0,
                "issued": run.get("issued") or 0,
                "upgraded": run.get("upgraded") or 0,
                "ended": run.get("ended") or 0,
                "sent": sent,
                "failed": failed,
                "skipped": d[Delivery.Outcome.SKIPPED],
                "delivery_rate": round(sent / (sent + failed), 4) if sent + failed else None,
                "new_subscribers": confirmed.get(week, 0),
                "stopped": stopped.get(week, 0),
                "signed_up": signed_up_at(min(week + dt.timedelta(days=6), today)),
            }
        )

    # Alerts that have ended: how long they lasted, by level.
    ended = list(
        Alert.objects.filter(ended_at__isnull=False, issued_at__gte=since_moment).values_list(
            "level", "status", "issued_at", "ended_at", "version"
        )
    )
    buckets = [
        {"label": _bucket_label(low, high), **dict.fromkeys(Level.values, 0)}
        for low, high in DURATION_BUCKETS
    ]
    lengths = []
    short = 0
    for level, status, issued, ended_at, _ in ended:
        length = ended_at - issued
        days = length.total_seconds() / 86400
        lengths.append(days)
        if status == Alert.Status.EXPIRED and length < SHORT_LIVED:
            short += 1
        for bucket, (low, high) in zip(buckets, DURATION_BUCKETS):
            if days >= low and (high is None or days < high):
                bucket[level] += 1
                break
    lengths.sort()
    median = lengths[len(lengths) // 2] if lengths else None

    issued_in_window = Alert.objects.filter(issued_at__gte=since_moment)
    how_labels = dict(Unsubscription.How.choices)
    by_how = Counter(how for _, _, how in stopped_rows)

    total_sent = sum(row["sent"] for row in weekly)
    total_failed = sum(row["failed"] for row in weekly)
    return {
        "since": since.isoformat(),
        "weekly": weekly,
        "alerts": {
            "issued": issued_in_window.count(),
            "upgraded": issued_in_window.filter(version__gt=1).count(),
            "cancelled": issued_in_window.filter(status=Alert.Status.CANCELLED).count(),
            "active": Alert.objects.filter(status=Alert.Status.ACTIVE).count(),
            "ended": len(ended),
            "short_lived": short,
            "short_lived_days": SHORT_LIVED.days,
            "median_days": round(median, 1) if median is not None else None,
            "durations": buckets,
            "by_level": dict(
                issued_in_window.values_list("level").annotate(n=Count("id")).values_list("level", "n")
            ),
        },
        "delivery": {
            "sent": total_sent,
            "failed": total_failed,
            "rate": round(total_sent / (total_sent + total_failed), 4) if total_sent + total_failed else None,
        },
        "subscribers": {
            "now": Subscriber.objects.filter(confirmed=True).count(),
            "pending": Subscriber.objects.filter(confirmed=False).count(),
            "stopped": len(stopped_rows),
            "stops_by_how": [
                {"how": how, "label": how_labels.get(how, how), "count": n}
                for how, n in by_how.most_common()
            ],
        },
    }
