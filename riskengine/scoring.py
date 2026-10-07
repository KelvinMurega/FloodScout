"""Score every Region for one date and store the results.

The one writer of RiskScore rows. The `score_regions` command and the
administrator's `risk-scores/rescore/` endpoint both call `score_regions()`
here, so a rescore from the dashboard does exactly what the command does.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from geodata.models import Region
from riskengine.ml.infer import MODEL_VERSION, infer
from riskengine.models import RiskScore


class NoRegionsError(Exception):
    """There is nothing to score: the Region table is empty."""


@dataclass
class ScoringResult:
    date: dt.date
    model_version: str
    regions: int
    created: int = 0
    updated: int = 0
    # Risk category value -> number of regions given it.
    categories: Counter = field(default_factory=Counter)


def start_of_day(date: dt.date) -> dt.datetime:
    """Midnight local time on `date`.

    RiskScore.prediction_date carries the date a score is *for*, so pinning
    it to the start of the day is what makes a re-run for the same date
    update its rows instead of writing a second set of 47.
    """
    naive = dt.datetime.combine(date, dt.time.min)
    return timezone.make_aware(naive) if settings.USE_TZ else naive


def score_regions(target_date: dt.date | None = None) -> ScoringResult:
    """Score every Region for `target_date` (default: today), all or nothing."""
    target_date = target_date or timezone.localdate()
    prediction_dt = start_of_day(target_date)

    regions = Region.objects.order_by("county_name", "level", "name")
    result = ScoringResult(date=target_date, model_version=MODEL_VERSION, regions=regions.count())
    if result.regions == 0:
        raise NoRegionsError("No regions in the database — run `manage.py seed_regions` first.")

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
            result.categories[risk_category] += 1
            if created:
                result.created += 1
            else:
                result.updated += 1
    return result
