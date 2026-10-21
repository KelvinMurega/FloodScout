"""Flood alerts, the people who asked for them, and every message sent.

The answer to "how are you going to warn people": the risk engine's daily
scores and 7-day forecast are read by fixed rules (alerts/rules.py) that
issue, upgrade and end an alert per region. Each new or stronger alert is
sent to everyone who subscribed to that region, by email and SMS, and is
published as an open Common Alerting Protocol (CAP 1.2) feed for any other
system to pick up.

Levels follow the three-step practice of most national warning services
(advisory, watch, warning), mapped to CAP severity Minor, Moderate and
Severe. FloodScout is a research system, not a warning authority, so every
message says so and its CAP status is "Exercise" unless set otherwise.
"""

from __future__ import annotations

import secrets
import uuid

from django.conf import settings
from django.db import models

from geodata.models import Region


def new_token() -> str:
    return secrets.token_urlsafe(24)


def new_code() -> str:
    """A six-digit confirmation code."""
    return f"{secrets.randbelow(1_000_000):06d}"


class Level(models.TextChoices):
    ADVISORY = "advisory", "Flood advisory"
    WATCH = "watch", "Flood watch"
    WARNING = "warning", "Flood warning"


# Least to most severe, for "at least this level" comparisons.
LEVEL_RANK = {Level.ADVISORY: 1, Level.WATCH: 2, Level.WARNING: 3}


class Alert(models.Model):
    """One alert for one region, from issue to end.

    A stronger condition later raises the level of the same alert (CAP
    msgType Update) rather than opening a second one, so a region has at
    most one active alert. `version` counts the issue and its upgrades.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Ended: the conditions no longer hold"
        CANCELLED = "cancelled", "Cancelled by an administrator"

    class Basis(models.TextChoices):
        OBSERVED = "observed", "Today's score, from rain that has fallen"
        FORECAST = "forecast", "The 7-day forecast"
        MANUAL = "manual", "Issued by an administrator"

    identifier = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="alerts")
    level = models.CharField(max_length=10, choices=Level.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    basis = models.CharField(max_length=10, choices=Basis.choices)
    headline = models.CharField(max_length=200)
    description = models.TextField()
    instruction = models.TextField()
    # The facts behind the alert: [{"kind": ..., "text": ...}].
    reasons = models.JSONField(default=list)
    times_usual = models.FloatField(null=True, blank=True)
    onset = models.DateField(help_text="The day the danger is expected from")
    expires = models.DateTimeField()
    version = models.PositiveIntegerField(default=1)
    issued_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alerts_issued",
        help_text="Empty when the rules issued it",
    )

    class Meta:
        ordering = ["-issued_at"]
        indexes = [models.Index(fields=["status", "level"])]
        constraints = [
            models.UniqueConstraint(
                fields=["region"],
                condition=models.Q(status="active"),
                name="one_active_alert_per_region",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_level_display()}: {self.region} ({self.status})"


class Subscriber(models.Model):
    """Someone who asked to be told about alerts for some regions.

    Nothing is sent until the six-digit code sent to them has been entered
    back (double opt-in), so no one can sign up someone else's number.
    Unsubscribing deletes the row: nothing is kept about a person who has
    left.
    """

    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=16, blank=True, help_text="+2547XXXXXXXX")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="alert_subscriptions",
    )
    regions = models.ManyToManyField(Region, related_name="subscribers")
    min_level = models.CharField(max_length=10, choices=Level.choices, default=Level.WATCH)
    confirmed = models.BooleanField(default=False)
    confirm_code = models.CharField(max_length=6, default=new_code)
    confirm_attempts = models.PositiveSmallIntegerField(default=0)
    token = models.CharField(max_length=40, unique=True, default=new_token)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # The automatic subscription for where a signed-up user lives, kept in
    # step with their profile by accounts/home.py. At most one per user.
    is_home = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        who = self.email or self.phone or f"subscriber {self.pk}"
        return f"{who} ({'confirmed' if self.confirmed else 'pending'})"

    def wants(self, level: str) -> bool:
        return LEVEL_RANK[level] >= LEVEL_RANK[self.min_level]


class Delivery(models.Model):
    """One message to one subscriber on one channel about one alert level."""

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"

    class Outcome(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    alert = models.ForeignKey(Alert, on_delete=models.CASCADE, related_name="deliveries")
    subscriber = models.ForeignKey(Subscriber, on_delete=models.CASCADE, related_name="deliveries")
    channel = models.CharField(max_length=5, choices=Channel.choices)
    level = models.CharField(max_length=10, choices=Level.choices)
    kind = models.CharField(max_length=10, default="alert", help_text="alert or cancel")
    outcome = models.CharField(max_length=7, choices=Outcome.choices)
    detail = models.CharField(max_length=300, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["alert", "subscriber", "channel", "level", "kind"],
                name="one_delivery_per_alert_level_channel",
            )
        ]

    def __str__(self) -> str:
        return f"{self.channel} to {self.subscriber} for {self.alert}: {self.outcome}"


class AlertRun(models.Model):
    """One pass of the rules over the latest scores, for the administrator."""

    started_at = models.DateTimeField(auto_now_add=True)
    scores_for = models.DateField(null=True, blank=True)
    issued = models.PositiveIntegerField(default=0)
    upgraded = models.PositiveIntegerField(default=0)
    ended = models.PositiveIntegerField(default=0)
    messages_sent = models.PositiveIntegerField(default=0)
    messages_failed = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"Alert run {self.started_at:%Y-%m-%d %H:%M}"
