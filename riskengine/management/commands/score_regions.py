"""Run the risk scorer over every Region for one date and store the results.

Without this there is nothing for the Sprint 5 map to colour. It is the only
writer of RiskScore rows today, and it stamps every one of them with the
scorer's MODEL_VERSION.
"""

from __future__ import annotations

import argparse
import datetime as dt
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from geodata.models import Region
from riskengine.ml.infer import MODEL_VERSION, infer
from riskengine.models import RiskScore


def parse_date(raw: str) -> dt.date:
    try:
        return dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {raw!r}") from exc


def start_of_day(date: dt.date) -> dt.datetime:
    """Midnight local time on `date`.

    RiskScore.prediction_date carries the date a score is *for*, so pinning
    it to the start of the day is what makes a re-run for the same date
    update its rows instead of writing a second set of 47.
    """
    naive = dt.datetime.combine(date, dt.time.min)
    return timezone.make_aware(naive) if settings.USE_TZ else naive


class Command(BaseCommand):
    help = "Score every Region for a date (default: today) and write RiskScore rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=parse_date,
            default=None,
            help="Date to score, as YYYY-MM-DD. Defaults to today.",
        )

    def handle(self, *args, **options):
        target_date = options["date"] or timezone.localdate()
        prediction_dt = start_of_day(target_date)

        regions = Region.objects.order_by("county_name", "level", "name")
        if not regions.exists():
            raise CommandError(
                "No regions in the database — run `manage.py seed_regions` first."
            )

        self.stdout.write(f"Scoring {regions.count()} regions for {target_date.isoformat()}")
        self.stdout.write(f"Scorer: {MODEL_VERSION}")

        created_count = 0
        updated_count = 0
        categories: Counter[str] = Counter()

        with transaction.atomic():
            for region in regions:
                predicted_value, risk_category = infer(region, target_date)
                _score, created = RiskScore.objects.update_or_create(
                    region=region,
                    prediction_date=prediction_dt,
                    defaults={
                        "predicted_value": predicted_value,
                        "risk_category": risk_category,
                        "model_version": MODEL_VERSION,
                    },
                )
                categories[risk_category] += 1
                if created:
                    created_count += 1
                else:
                    updated_count += 1

        self._report(target_date, created_count, updated_count, categories)

    def _report(self, target_date, created_count, updated_count, categories):
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Created: {created_count}"))
        self.stdout.write(self.style.SUCCESS(f"Updated: {updated_count}"))
        self.stdout.write("")
        self.stdout.write("Category breakdown:")
        for category in RiskScore.RiskCategory:
            self.stdout.write(f"  {category.label:<10} {categories.get(category.value, 0)}")
        self.stdout.write("")
        self.stdout.write(
            f"RiskScore rows for {target_date.isoformat()}: "
            f"{RiskScore.objects.filter(prediction_date=start_of_day(target_date)).count()}"
        )
        self.stdout.write(self.style.WARNING(
            f"These are placeholder scores from {MODEL_VERSION}, not model predictions."
        ))
