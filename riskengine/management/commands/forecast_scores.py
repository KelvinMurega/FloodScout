"""Forecast every region's flood risk for the next seven days.

    python manage.py forecast_scores
    python manage.py forecast_scores --days 5

Fetches the rainfall forecast for every region and scores each of the days
ahead with the trained model, from the rain stored so far joined to the
rain forecast (riskengine/forecast.py). `refresh_scores` runs this after
the day's scores. Re-running on the same day replaces that day's issue.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from geodata.services.open_meteo import OpenMeteoError
from riskengine.forecast import FORECAST_DAYS, ForecastError, forecast_regions
from riskengine.models import RiskScore


class Command(BaseCommand):
    help = "Forecast every region's flood risk for the coming days from forecast rain."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=FORECAST_DAYS,
            help=f"How many days ahead (default {FORECAST_DAYS}, at most 15).",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if not 1 <= days <= 15:
            raise CommandError("--days must be between 1 and 15.")
        try:
            result = forecast_regions(days=days)
        except (ForecastError, OpenMeteoError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            f"Forecast {result.regions} regions for {days} days, issued {result.issued_on}"
        )
        self.stdout.write(f"Scorer: {result.model_version}")
        order = list(RiskScore.RiskCategory)
        self.stdout.write("  Day          " + "".join(f"{c.label:>11}" for c in order))
        for day in result.days:
            counts = result.categories[day]
            self.stdout.write(
                f"  {day.isoformat()}   " + "".join(f"{counts.get(c.value, 0):>11}" for c in order)
            )
