"""Score every grid cell for a date (default: today) and write GridRiskScore rows.

    python manage.py score_grid
    python manage.py score_grid --date 2026-10-17 --date 2026-10-18

The scoring itself is riskengine/grid.py. `refresh_scores` runs this after
the region scores whenever there are grid cells, so the grid stays as
current as the counties.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from geodata.services.open_meteo import OpenMeteoError
from riskengine.grid import GridError, score_grid
from riskengine.management.commands.score_regions import parse_date
from riskengine.models import RiskScore


class Command(BaseCommand):
    help = "Score every grid cell for one or more dates and write GridRiskScore rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=parse_date,
            action="append",
            default=None,
            help="Date to score, as YYYY-MM-DD; repeat for several. Defaults to today.",
        )

    def handle(self, *args, **options):
        dates = options["date"] or [timezone.localdate()]
        try:
            result = score_grid(dates)
        except (GridError, OpenMeteoError) as exc:
            raise CommandError(str(exc)) from exc

        for warning in result.warnings:
            self.stdout.write(self.style.WARNING(warning))
        days = ", ".join(date.isoformat() for date in result.dates)
        self.stdout.write(f"Scored {result.cells} grid cells for {days}")
        self.stdout.write(f"Scorer: {result.model_version}")
        for category in RiskScore.RiskCategory:
            self.stdout.write(f"  {category.label:<10} {result.categories.get(category.value, 0)}")
