"""Run the rules, keep each region's alert up to date, and send the messages.

`run_alerts()` is what `issue_alerts` (and so the daily refresh) calls:

  - a region whose rules give a level and has no active alert gets one, and
    its subscribers are told;
  - an active alert whose rules now give a stronger level is upgraded (a
    new version), and its subscribers are told again;
  - an active alert whose rules give the same level is kept, its reasons
    and expiry refreshed, and nobody is told twice;
  - an active alert whose rules give a weaker level steps down to it (a new
    version) without a message, so the map shows what the rules say today
    (since 28 October 2026; until then it kept its highest level);
  - an active alert whose rules give nothing has ended: it is closed
    without a message. An alert also ends on its own at `expires`, so one
    that the daily run never gets back to does not stay up for ever.

Alerts an administrator issued by hand are left to the administrator.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from geodata.models import KmdWarning, Region
from riskengine.models import RiskScore
from riskengine.situation import newest_places

from . import channels
from .models import Alert, AlertRun, Delivery, Level, Subscriber
from .rules import DISCLAIMER, INSTRUCTIONS, decide, stronger, wording

# How long an alert stays up without a run confirming it.
ALERT_LIFETIME = dt.timedelta(hours=36)


@dataclass
class Sent:
    sent: int = 0
    failed: int = 0

    def add(self, other: "Sent") -> None:
        self.sent += other.sent
        self.failed += other.failed


def unsubscribe_link(subscriber: Subscriber) -> str:
    return f"{settings.EMAIL_API_URL.rstrip('/')}/alerts/unsubscribe/{subscriber.token}/"


def alert_link(alert: Alert) -> str:
    return f"{settings.EMAIL_DASHBOARD_URL.rstrip('/')}/#/alerts/{alert.id}"


def email_body(alert: Alert, subscriber: Subscriber, kind: str) -> tuple[str, str]:
    """(subject, text) of an alert, or of its cancellation."""
    if kind == "cancel":
        subject = f"Cancelled: {alert.headline}"
        lines = [
            f"The {alert.get_level_display().lower()} FloodScout issued for "
            f"{alert.region} has been cancelled by an administrator.",
        ]
    else:
        subject = f"Where you live: {alert.headline}" if subscriber.is_home else alert.headline
        lines = [
            alert.headline.upper(),
            "",
            alert.description,
            "",
            "What to do:",
            alert.instruction,
            "",
            f"See it on the map: {alert_link(alert)}",
        ]
    why = (
        f"You get this because {alert.region} is where you live, as set on your FloodScout "
        "profile. Change it or turn these off on your profile."
        if subscriber.is_home
        else f"You get this because you asked FloodScout for alerts for {alert.region}."
    )
    lines += ["", why, f"To stop them: {unsubscribe_link(subscriber)}"]
    return subject, "\n".join(lines)


def deliver(alert: Alert, subscriber: Subscriber, kind: str = "alert") -> Sent:
    """Email `alert` to `subscriber`, once per level and kind.

    Alerts go by email only. A phone on an older subscription is ignored.
    """
    tally = Sent()
    channel = Delivery.Channel.EMAIL
    if subscriber.email and not Delivery.objects.filter(
        alert=alert, subscriber=subscriber, channel=channel, level=alert.level, kind=kind
    ).exists():
        subject, body = email_body(alert, subscriber, kind)
        result = channels.send_email(subscriber.email, subject, body)
        Delivery.objects.create(
            alert=alert,
            subscriber=subscriber,
            channel=channel,
            level=alert.level,
            kind=kind,
            outcome=result.outcome,
            detail=result.detail,
        )
        if result.outcome == "sent":
            tally.sent += 1
        elif result.outcome == "failed":
            tally.failed += 1
    return tally


def notify(alert: Alert, kind: str = "alert") -> Sent:
    """Tell every confirmed subscriber to the alert's region who wants this level."""
    tally = Sent()
    subscribers = Subscriber.objects.filter(confirmed=True, regions=alert.region).distinct()
    for subscriber in subscribers:
        if kind == "alert" and not subscriber.wants(alert.level):
            continue
        if kind == "cancel" and not alert.deliveries.filter(subscriber=subscriber).exists():
            continue
        tally.add(deliver(alert, subscriber, kind))
    return tally


def expire_old(now=None) -> int:
    """Close the active alerts whose expiry has passed."""
    now = now or timezone.now()
    return Alert.objects.filter(status=Alert.Status.ACTIVE, expires__lt=now).update(
        status=Alert.Status.EXPIRED, ended_at=now
    )


def _local_day(when: dt.datetime) -> str:
    local = timezone.localtime(when)
    return f"{local:%A} {local.day} {local:%B}"


def kmd_reason(warning: KmdWarning, now: dt.datetime) -> dict:
    """ "KMD heavy rainfall advisory in force until Friday 30 October"."""
    kind = "warning" if warning.severity in ("Severe", "Extreme") else "advisory"
    when = f"from {_local_day(warning.onset)}" if warning.onset > now else "in force"
    if warning.expires:
        when += f" until {_local_day(warning.expires)}"
    text = f"KMD {warning.event.lower()} {kind} {when}"
    return {"kind": "kmd", "text": text, "url": warning.web or warning.url}


def kmd_by_region(now: dt.datetime) -> dict[int, KmdWarning]:
    """The flood-related KMD warning for each region it covers: the one that
    lasts longest where two overlap."""
    found: dict[int, KmdWarning] = {}
    warnings = (
        KmdWarning.in_force(now).filter(flood_related=True).prefetch_related("regions")
    )
    for warning in warnings:
        for region_id in (r.pk for r in warning.regions.all()):
            current = found.get(region_id)
            if current is None or (warning.expires or now) > (current.expires or now):
                found[region_id] = warning
    return found


def run_alerts(now=None) -> AlertRun:
    """One pass of the rules over the newest scores of every region."""
    now = now or timezone.now()
    run = AlertRun.objects.create()
    tally = Sent()
    ended = expire_old(now)

    day, places, _issued = newest_places(RiskScore.objects.select_related("region"))
    run.scores_for = day
    if day is None:
        run.note = "No scores to read."
        run.ended = ended
        run.save()
        return run

    active = {
        alert.region_id: alert
        for alert in Alert.objects.filter(status=Alert.Status.ACTIVE).select_related("region")
    }
    kmd = kmd_by_region(now)
    to_notify = []
    with transaction.atomic():
        for place in places:
            decision = decide(place, day)
            # KMD's warning is named, never counted: it does not change the
            # level, as there is no record of past KMD warnings to test that on.
            if decision.level and place.region in kmd:
                decision.reasons.append(kmd_reason(kmd[place.region], now))
            current = active.get(place.region)
            if current and current.basis == Alert.Basis.MANUAL:
                continue
            if decision.level is None:
                if current:
                    current.status = Alert.Status.EXPIRED
                    current.ended_at = now
                    current.save(update_fields=["status", "ended_at", "updated_at"])
                    ended += 1
                continue

            words = wording(decision.level, place, decision)
            fields = {
                **words,
                "basis": decision.basis,
                "reasons": decision.reasons,
                "times_usual": place.times_usual,
                "onset": decision.onset,
                "expires": now + ALERT_LIFETIME,
            }
            if current is None:
                alert = Alert.objects.create(region_id=place.region, level=decision.level, **fields)
                run.issued += 1
                to_notify.append(alert)
            elif stronger(decision.level, current.level):
                for name, value in fields.items():
                    setattr(current, name, value)
                current.level = decision.level
                current.version += 1
                current.save()
                run.upgraded += 1
                to_notify.append(current)
            elif stronger(current.level, decision.level):
                # Weaker: the alert says what the rules say today, but nobody is
                # messaged to say so. Climbing back later messages only those not
                # yet told at that level (deliver() sends once per level).
                for name, value in fields.items():
                    setattr(current, name, value)
                current.level = decision.level
                current.version += 1
                current.save()
            else:
                # The same level: refresh what it rests on, and the words with
                # it, so the text a later subscriber is sent does not still
                # name a forecast day that has passed or an old figure.
                # Nobody is told again and the version stays.
                for name, value in fields.items():
                    setattr(current, name, value)
                current.save(update_fields=[*fields, "updated_at"])

    for alert in to_notify:
        tally.add(notify(alert))
    run.ended = ended
    run.messages_sent = tally.sent
    run.messages_failed = tally.failed
    run.save()
    return run


def issue_manual(
    region: Region, level: str, description: str, user, instruction: str = ""
) -> Alert:
    """An administrator's own alert, which the rules will not touch."""
    current = Alert.objects.filter(region=region, status=Alert.Status.ACTIVE).first()
    if current:
        current.status = Alert.Status.CANCELLED
        current.ended_at = timezone.now()
        current.save(update_fields=["status", "ended_at", "updated_at"])
    where = (
        f"{region.name} ({region.county_name} County)"
        if region.level == Region.Level.SUB_COUNTY
        else f"{region.name} County"
    )
    alert = Alert.objects.create(
        region=region,
        level=level,
        basis=Alert.Basis.MANUAL,
        headline=f"{Level(level).label} for {where}",
        description=f"{description.strip()} {DISCLAIMER}",
        instruction=instruction.strip() or INSTRUCTIONS[level],
        reasons=[{"kind": "manual", "text": description.strip()[:200]}],
        onset=timezone.localdate(),
        expires=timezone.now() + ALERT_LIFETIME,
        issued_by=user,
    )
    notify(alert)
    return alert


def cancel(alert: Alert) -> Sent:
    """End an alert early and tell the people who were told about it."""
    alert.status = Alert.Status.CANCELLED
    alert.ended_at = timezone.now()
    alert.save(update_fields=["status", "ended_at", "updated_at"])
    return notify(alert, kind="cancel")
