"""Bring the scores up to date: fetch the latest rainfall, then score every region.

This is the one command a scheduler needs. Both steps overwrite what they
wrote before for the same days, so running it twice does no harm.

    python manage.py refresh_scores                    # once
    python manage.py refresh_scores --every-hours 24   # now, then daily, until stopped

The `scheduler` service in docker-compose.yml runs the second form, so the
dashboard stays current for as long as the stack is up.

The seasonal outlook is not refreshed here. The forecast behind it is issued
monthly and takes much longer to fetch; run `flood_outlook` when a new one
is out.
"""

from __future__ import annotations

import time

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

# Indirection so tests can skip the wait.
_sleep = time.sleep


class Command(BaseCommand):
    help = "Fetch the latest rainfall and rescore every region, once or on a repeat."

    def add_arguments(self, parser):
        parser.add_argument(
            "--every-hours",
            type=float,
            default=None,
            metavar="HOURS",
            help="Keep running, refreshing again after this many hours. Default: run once.",
        )

    def handle(self, *args, **options):
        every = options["every_hours"]
        if every is not None and every <= 0:
            raise CommandError("--every-hours must be positive.")

        while True:
            self.stdout.write(f"Refresh started {timezone.localtime():%Y-%m-%d %H:%M}")
            try:
                call_command("seed_rainfall", stdout=self.stdout, stderr=self.stderr)
                call_command("score_regions", stdout=self.stdout, stderr=self.stderr)
            except CommandError as exc:
                if every is None:
                    raise
                # A failed fetch must not end the schedule: yesterday's scores
                # stay on the dashboard and the next run tries again.
                self.stderr.write(self.style.ERROR(f"Refresh failed, will retry next run: {exc}"))
            if every is None:
                return
            _sleep(every * 3600)
