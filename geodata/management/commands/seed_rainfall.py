"""Populate geodata.EnvironmentalData with real recent daily rainfall.

One row per county per day, in millimetres, sampled at the same
representative point `seed_terrain` uses. Only dates that have already
happened are stored — nothing here is a forecast.

Re-running overwrites the days it covers rather than appending duplicates,
so it is safe on a cron or before a demo.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from geodata.models import EnvironmentalData, Region
from geodata.services.open_meteo import OpenMeteoError, fetch_daily_rainfall

from .seed_terrain import representative_point

# The placeholder scorer looks at a 7-day window; 14 gives it a full window
# plus history to look back over without a second fetch.
DEFAULT_PAST_DAYS = 14


class Command(BaseCommand):
    help = "Ingest real daily rainfall per Region from Open-Meteo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--past-days",
            type=int,
            default=DEFAULT_PAST_DAYS,
            help=f"How many days back to ingest (default {DEFAULT_PAST_DAYS}).",
        )
        parser.add_argument(
            "--county",
            type=str,
            default=None,
            help="Only this county and its sub-counties (case-insensitive). Defaults to all.",
        )
        parser.add_argument(
            "--level",
            choices=[level.value for level in Region.Level],
            default=None,
            help="Only counties or only sub-counties. Defaults to both.",
        )

    def handle(self, *args, **options):
        past_days = options["past_days"]
        if past_days < 1:
            raise CommandError("--past-days must be at least 1.")

        regions = Region.objects.order_by("county_name", "level", "name")
        if options["level"]:
            regions = regions.filter(level=options["level"])
        regions = list(regions)
        if options["county"]:
            wanted = options["county"].strip().casefold()
            regions = [r for r in regions if r.county_name.casefold() == wanted]
            if not regions:
                raise CommandError(f"No county named {options['county']!r}.")
        if not regions:
            raise CommandError("No regions in the database — run `seed_regions` first.")

        self.stdout.write(
            f"Fetching {past_days} days of rainfall for {len(regions)} regions"
        )

        points = [representative_point(region) for region in regions]
        try:
            series = fetch_daily_rainfall(points, past_days=past_days)
        except OpenMeteoError as exc:
            raise CommandError(str(exc)) from exc

        created_count = 0
        updated_count = 0
        totals: list[tuple[str, float]] = []

        with transaction.atomic():
            for region, by_date in zip(regions, series):
                for record_date, millimetres in by_date.items():
                    _row, created = EnvironmentalData.objects.update_or_create(
                        region=region,
                        record_date=record_date,
                        defaults={"rainfall_intensity": millimetres},
                    )
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
                totals.append((str(region), sum(by_date.values())))

        self._report(past_days, created_count, updated_count, totals)

    def _report(self, past_days, created_count, updated_count, totals):
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Created: {created_count}"))
        self.stdout.write(self.style.SUCCESS(f"Updated: {updated_count}"))
        self.stdout.write("")

        if totals:
            wettest = max(totals, key=lambda t: t[1])
            driest = min(totals, key=lambda t: t[1])
            self.stdout.write(f"Wettest over {past_days} days: {wettest[0]} at {wettest[1]:.1f} mm")
            self.stdout.write(f"Driest  over {past_days} days: {driest[0]} at {driest[1]:.1f} mm")
        self.stdout.write(f"EnvironmentalData rows now: {EnvironmentalData.objects.count()}")
