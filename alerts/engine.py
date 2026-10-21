"""Run the rules, keep each region's alert up to date, and send the messages.

`run_alerts()` is what `issue_alerts` (and so the daily refresh) calls:

  - a region whose rules give a level and has no active alert gets one, and
    its subscribers are told;
  - an active alert whose rules now give a stronger level is upgraded (a
    new version), and its subscribers are told again;
  - an active alert whose rules give the same or a weaker level is kept,
    its wording and expiry refreshed, and nobody is told twice;
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

from geodata.models import Region
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
    return f"{settings.PUBLIC_API_URL.rstrip('/')}/alerts/unsubscribe/{subscriber.token}/"


def alert_link(alert: Alert) -> str:
    return f"{settings.DASHBOARD_URL.rstrip('/')}/#/alerts/{alert.id}"


def email_body(alert: Alert, subscriber: Subscriber, kind: str) -> tuple[str, str]:
    """(subject, text) of an alert, or of its cancellation."""
    if kind == "cancel":
        subject = f"Cancelled: {alert.headline}"
        lines = [
            f"The {alert.get_level_display().lower()} FloodScout issued for "
            f"{alert.region} has been cancelled by an administrator.",
        ]
    else:
        subject = alert.headline
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
    lines += [
        "",
        f"You get this because you asked FloodScout for alerts for {alert.region}.",
        f"To stop them: {unsubscribe_link(subscriber)}",
    ]
    return subject, "\n".join(lines)


def deliver(alert: Alert, subscriber: Subscriber, kind: str = "alert") -> Sent:
    """Send `alert` to `subscriber` on each channel they gave, once per level and kind."""
    tally = Sent()
    targets = []
    if subscriber.email:
        targets.append(Delivery.Channel.EMAIL)
    if subscriber.phone:
        targets.append(Delivery.Channel.SMS)
    for channel in targets:
        if Delivery.objects.filter(
            alert=alert, subscriber=subscriber, channel=channel, level=alert.level, kind=kind
        ).exists():
            continue
        if channel == Delivery.Channel.EMAIL:
            subject, body = email_body(alert, subscriber, kind)
            result = channels.send_email(subscriber.email, subject, body)
        else:
            reason = (
                "cancelled by an administrator"
                if kind == "cancel"
                else alert.reasons[0]["text"]
                if alert.reasons
                else alert.headline
            )
            text = channels.sms_text(
                ("Cancelled " if kind == "cancel" else "") + alert.get_level_display(),
                str(alert.region),
                reason,
                unsubscribe_link(subscriber),
            )
            result = channels.send_sms(subscriber.phone, text)
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
    to_notify = []
    with transaction.atomic():
        for place in places:
            decision = decide(place, day)
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
            else:
                # Same or weaker: keep the level people were told, refresh the rest.
                current.expires = fields["expires"]
                current.reasons = decision.reasons
                current.times_usual = place.times_usual
                current.save(update_fields=["expires", "reasons", "times_usual", "updated_at"])

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
