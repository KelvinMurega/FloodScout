"""Run the risk scorer over every Region for one date and store the results.

Without this there is nothing for the Sprint 5 map to colour. The scoring
itself is `riskengine.scoring.score_regions`, which the administrator's
rescore endpoint shares; every row is stamped with the scorer's
MODEL_VERSION.
"""

from __future__ import annotations

import argparse
import datetime as dt

from django.core.management.base import BaseCommand, CommandError

from riskengine.ml.infer import HEURISTIC_VERSION, MODEL_VERSION
from riskengine.models import RiskScore
from riskengine.scoring import NoRegionsError, score_regions, start_of_day


def parse_date(raw: str) -> dt.date:
    try:
        return dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {raw!r}") from exc


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
        try:
            result = score_regions(options["date"])
        except NoRegionsError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Scored {result.regions} regions for {result.date.isoformat()}")
        self.stdout.write(f"Scorer: {result.model_version}")
        self._report(result.date, result.created, result.updated, result.categories)

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
        if MODEL_VERSION == HEURISTIC_VERSION:
            self.stdout.write(self.style.WARNING(
                f"These are placeholder scores from {MODEL_VERSION}, not model predictions."
            ))
