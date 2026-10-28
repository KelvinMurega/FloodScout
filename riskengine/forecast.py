"""The flood risk for each of the next seven days, from forecast rain.

The model scores a region from the rain in the 60 days up to a date. For a
day next week, most of those 60 days have already happened and are stored;
the rest are taken from the weather forecast. So a forecast score is the
same trained model, run on observed rain joined to forecast rain:

    day +3:   [ ........ 57 days observed ........ | 3 days forecast ]

Nothing else about the model or its inputs changes. The ocean indices are
the ones published by the target day, which in a week are the ones
published today.

The forecast rain is never stored as EnvironmentalData. Only the scores,
and the forecast totals behind them, are kept, in RiskForecast.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from geodata.models import Region
from geodata.services.open_meteo import fetch_forecast_rainfall
from geodata.services.sampling import regional_rainfall
from riskengine import dry
from riskengine.ml import infer as scorer
from riskengine.models import RiskForecast

FORECAST_DAYS = 7


class ForecastError(Exception):
    """No trained model, or no regions, to forecast with."""


@dataclass
class ForecastResult:
    issued_on: dt.date
    model_version: str
    regions: int
    days: list[dt.date]
    # Risk category -> count, per forecast day.
    categories: dict[dt.date, Counter] = field(default_factory=dict)


def join_rain(
    observed: dict[dt.date, float], forecast: dict[dt.date, float], issued_on: dt.date
) -> dict[dt.date, float]:
    """Observed rain up to and including `issued_on`, forecast rain after it.

    Observed wins on any day both have: a forecast never replaces a
    measurement. Today's forecast is used only when today has no
    measurement yet.
    """
    joined = {day: mm for day, mm in forecast.items() if day >= issued_on}
    joined.update({day: mm for day, mm in observed.items() if day <= issued_on})
    return joined


def forecast_totals(
    forecast: dict[dt.date, float], issued_on: dt.date, target: dt.date
) -> tuple[float | None, float | None]:
    """(total, wettest day) of the forecast rain after `issued_on` up to `target`, mm."""
    days = [forecast[d] for d in sorted(forecast) if issued_on < d <= target]
    if not days:
        return None, None
    return round(sum(days), 1), round(max(days), 1)


def forecast_regions(
    issued_on: dt.date | None = None,
    days: int = FORECAST_DAYS,
    fetch: Callable[[list, int], list[dict[dt.date, float]]] = fetch_forecast_rainfall,
) -> ForecastResult:
    """Forecast every region for the `days` after `issued_on` (default today).

    `fetch` takes a list of Points and a number of days, like
    open_meteo.fetch_forecast_rainfall, which tests replace.
    """
    model = scorer._MODEL
    if model is None:
        raise ForecastError("Forecasts need the trained model; the placeholder rule has none.")
    issued_on = issued_on or timezone.localdate()
    regions = list(Region.objects.select_related("parent").order_by("county_name", "level", "name"))
    if not regions:
        raise ForecastError("No regions in the database — run `manage.py seed_regions` first.")

    targets = [issued_on + dt.timedelta(days=n) for n in range(1, days + 1)]
    forecasts = regional_rainfall(
        regions, lambda points: fetch(points, days), scorer.rain_points(model)
    )
    thresholds = scorer.model_thresholds(model)

    rows, keys = [], []
    for region, forecast in zip(regions, forecasts):
        terrain = region.geospatial_features.order_by("-pk").first()
        # The latest target needs rain back to HISTORY_DAYS before it.
        observed = scorer.region_rain(region, issued_on)
        rain = join_rain(observed, forecast, issued_on)
        for target in targets:
            rows.append(scorer.region_row(model, region, rain, target, terrain))
            keys.append((region, target, forecast))
    probabilities = scorer.flood_probabilities(model, rows)

    result = ForecastResult(
        issued_on=issued_on,
        model_version=scorer.MODEL_VERSION,
        regions=len(regions),
        days=targets,
        categories={target: Counter() for target in targets},
    )
    names = scorer.model_features(model)
    records = []
    for (region, target, forecast), row, probability in zip(keys, rows, probabilities):
        category = dry.gated(scorer.classify(probability, thresholds), row, names)
        total, wettest = forecast_totals(forecast, issued_on, target)
        result.categories[target][category] += 1
        records.append(
            RiskForecast(
                region=region,
                issued_on=issued_on,
                target_date=target,
                predicted_value=round(probability, 4),
                risk_category=category,
                rain_forecast_mm=total,
                rain_forecast_max_24h=wettest,
                model_version=scorer.MODEL_VERSION,
            )
        )
    with transaction.atomic():
        RiskForecast.objects.filter(issued_on=issued_on).delete()
        RiskForecast.objects.bulk_create(records)
    return result


def latest_issue() -> dt.date | None:
    """The day the newest forecast was issued."""
    return RiskForecast.objects.order_by("-issued_on").values_list("issued_on", flat=True).first()
