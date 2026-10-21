"""Bring every region's flood alert up to date and send the new ones.

    python manage.py issue_alerts

Reads the newest scores and 7-day forecast, applies the rules in
alerts/rules.py, issues, upgrades and ends alerts, and sends each new or
stronger one to the people who subscribed to its region (alerts/engine.py).
`refresh_scores` runs it after the forecast. Running it twice sends nothing
twice.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from alerts.engine import run_alerts
from alerts.models import Alert, Level


class Command(BaseCommand):
    help = "Issue, upgrade and end flood alerts from the newest scores, and send them."

    def handle(self, *args, **options):
        run = run_alerts()
        if run.note:
            self.stdout.write(run.note)
        self.stdout.write(
            f"Alerts for scores of {run.scores_for}: {run.issued} issued, {run.upgraded} upgraded, "
            f"{run.ended} ended; {run.messages_sent} messages sent, {run.messages_failed} failed"
        )
        active = Alert.objects.filter(status=Alert.Status.ACTIVE)
        for level in reversed(Level.values):
            self.stdout.write(f"  {Level(level).label:<16} {active.filter(level=level).count()}")
