"""Bring every region's flood alert up to date and send the new ones.

    python manage.py issue_alerts
    python manage.py issue_alerts --dry-run   # what the rules decide today, nothing issued or sent

Reads the newest scores and 7-day forecast, applies the rules in
alerts/rules.py, issues, upgrades and ends alerts, and sends each new or
stronger one to the people who subscribed to its region (alerts/engine.py).
`refresh_scores` runs it after the forecast. Running it twice sends nothing
twice.
"""

from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand

from alerts.engine import run_alerts
from alerts.models import Alert, Level
from alerts.rules import decide
from riskengine.models import RiskScore
from riskengine.situation import newest_places


class Command(BaseCommand):
    help = "Issue, upgrade and end flood alerts from the newest scores, and send them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the level the rules give each region today; change and send nothing.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            return self._dry_run()
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

    def _dry_run(self):
        day, places, _issued = newest_places(RiskScore.objects.select_related("region"))
        if day is None:
            self.stdout.write("No scores to read.")
            return
        levels = [(place, decide(place, day).level) for place in places]
        counts = Counter(level for _place, level in levels if level)
        self.stdout.write(f"Dry run on scores of {day}, {len(places)} regions; nothing issued or sent")
        for level in reversed(Level.values):
            self.stdout.write(f"  {Level(level).label:<16} {counts[level]}")
        for level in reversed(Level.values):
            names = sorted(p.name for p, got in levels if got == level and p.level == "county")
            if names:
                self.stdout.write(f"  {Level(level).label} counties: {', '.join(names)}")
