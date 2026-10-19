"""Bring the scores up to date: fetch the latest rainfall, then score every region.

This is the one command a scheduler needs. Both steps overwrite what they
wrote before for the same days, so running it twice does no harm.

    python manage.py refresh_scores                    # once
    python manage.py refresh_scores --every-hours 24   # now, then daily, until stopped

The `scheduler` service in docker-compose.yml runs the second form, so the
dashboard stays current for as long as the stack is up.

Days the stack was down are caught up on: a run scores every day in the
last MAX_CATCH_UP_DAYS that has no scores, not only today. So a computer
that was off over a weekend has Saturday and Sunday on the map once it is
back on, scored from the rain that actually fell on them.

When `seed_grid` has laid an area grid, its cells are scored too, with the
same catching up.

The seasonal outlook is not refreshed here. The forecast behind it is issued
monthly and takes much longer to fetch; run `flood_outlook` when a new one
is out.
"""

from __future__ import annotations

import datetime as dt
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db.models.functions import TruncDate
from django.utils import timezone

from geodata.management.commands.seed_rainfall import DEFAULT_PAST_DAYS
from geodata.models import GridCell
from riskengine.models import GridRiskScore, RiskScore

# The most days back Open-Meteo's forecast endpoint serves. Scoring a day
# reads the DEFAULT_PAST_DAYS before it, so this caps how far back a run can
# catch up.
MAX_PAST_DAYS = 92
MAX_CATCH_UP_DAYS = MAX_PAST_DAYS - DEFAULT_PAST_DAYS

# How often a scheduled run wakes to check whether the next refresh is due.
# The wait is measured on the wall clock, not by one long sleep, so a laptop
# that sleeps through the night still refreshes as soon as it wakes.
CHECK_SECONDS = 600

# How soon a scheduled run that failed is tried again. Open-Meteo's free
# tier answers 429 once a day's or an hour's allowance is spent, and the
# allowance comes back long before the next daily run.
RETRY_SECONDS = 3600

# Indirection so tests can skip the wait and move the clock.
_sleep = time.sleep
_now = time.time


def days_to_score(today: dt.date, scored_days) -> list[dt.date]:
    """The days in the last MAX_CATCH_UP_DAYS with no scores, then `today`.

    `scored_days` are the days that already have scores. Gaps are filled
    wherever they are, not only after the last scored day. Nothing before
    the first scored day is filled in, so a new database starts with just
    today. Today is always included, so a second run on the same day
    rescores it with the rain that has fallen since.
    """
    scored = set(scored_days)
    if not scored:
        return [today]
    start = max(min(scored), today - dt.timedelta(days=MAX_CATCH_UP_DAYS))
    days = [start + dt.timedelta(days=n) for n in range((today - start).days)]
    return [day for day in days if day not in scored] + [today]


def region_days(today: dt.date) -> list[dt.date]:
    """The days the regions need scoring for."""
    scored = (
        RiskScore.objects.annotate(day=TruncDate("prediction_date"))
        .values_list("day", flat=True)
        .distinct()
    )
    return days_to_score(today, scored)


def grid_days(today: dt.date) -> list[dt.date]:
    """The days the area grid needs scoring for."""
    return days_to_score(today, GridRiskScore.objects.values_list("date", flat=True).distinct())


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

        last_run = None
        while True:
            if last_run is None or _now() - last_run >= every * 3600:
                last_run = _now()
                try:
                    self.refresh()
                except CommandError as exc:
                    if every is None:
                        raise
                    # A failed fetch must not end the schedule: yesterday's
                    # scores stay on the dashboard and a run tries again in
                    # RETRY_SECONDS, not a whole interval later.
                    retry = min(RETRY_SECONDS, every * 3600)
                    last_run -= every * 3600 - retry
                    self.stderr.write(
                        self.style.ERROR(
                            f"Refresh failed, will retry in {retry / 60:.0f} minutes: {exc}"
                        )
                    )
            if every is None:
                return
            _sleep(min(CHECK_SECONDS, every * 3600))

    def refresh(self):
        today = timezone.localdate()
        days = region_days(today)
        self.stdout.write(f"Refresh started {timezone.localtime():%Y-%m-%d %H:%M}")
        if len(days) > 1:
            self.stdout.write(f"Catching up on {len(days)} days from {days[0].isoformat()}")

        past_days = DEFAULT_PAST_DAYS + (today - days[0]).days
        call_command(
            "seed_rainfall", "--past-days", str(past_days), stdout=self.stdout, stderr=self.stderr
        )
        for day in days:
            call_command(
                "score_regions", "--date", day.isoformat(), stdout=self.stdout, stderr=self.stderr
            )

        # The area grid, once `seed_grid` has laid one. It fetches its own
        # rainfall, at the cells' centres.
        if GridCell.objects.exists():
            dates = [arg for day in grid_days(today) for arg in ("--date", day.isoformat())]
            call_command("score_grid", *dates, stdout=self.stdout, stderr=self.stderr)
