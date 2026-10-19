"""Score every grid cell for one or more days and store the results.

The region scores give each county one value, so the whole county takes one
colour. Here the same trained model is run on each geodata.GridCell, from
rainfall measured at the cell's centre, so the map can show where inside a
county the rain that drives the risk fell.

The model was trained on rainfall measured at one point per county
(`rain_points` 1 in the artifact), so a cell's point rainfall is on the scale
the model learned. What does not change is what the model was taught to
predict: a large flood recorded somewhere in a county. A cell's value is
therefore the chance for a county that had received that cell's rain, and
the county's normal rainfall and area are borrowed to say so. It ranks the
cells by how dangerous their rain was; it is not a flood extent.

The cells' rainfall is fetched fresh on each run and not stored: only the
scores, and the 30-day rain behind them for the map's tooltip, are kept.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from geodata.management.commands.seed_rainfall import DEFAULT_PAST_DAYS
from geodata.models import GridCell
from geodata.services.open_meteo import Point, fetch_daily_rainfall
from riskengine.ml import infer as scorer
from riskengine.ml.features import rainfall_features
from riskengine.models import GridRiskScore

# The most days back Open-Meteo's forecast endpoint serves.
MAX_PAST_DAYS = 92


class GridError(Exception):
    """The grid cannot be scored: no trained model, no cells, or a day too old."""


@dataclass
class GridResult:
    dates: list[dt.date]
    model_version: str
    cells: int
    # Risk category value -> number of cells given it, over all the dates.
    categories: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)


def _number(value: float) -> float | None:
    return None if value is None or math.isnan(value) else round(value, 2)


def score_grid(dates: list[dt.date], fetch=fetch_daily_rainfall) -> GridResult:
    """Score every GridCell for each of `dates`, replacing earlier scores for them.

    `fetch` takes a list of Points and a number of past days, like
    open_meteo.fetch_daily_rainfall, which tests replace.
    """
    model = scorer._MODEL
    if model is None:
        raise GridError(
            "The grid is scored only by a trained model; the placeholder heuristic "
            "has nothing to say about where inside a county."
        )
    cells = list(GridCell.objects.select_related("county").order_by("pk"))
    if not cells:
        raise GridError("No grid cells in the database — run `manage.py seed_grid` first.")

    dates = sorted(set(dates))
    past_days = DEFAULT_PAST_DAYS + (timezone.localdate() - dates[0]).days
    if past_days > MAX_PAST_DAYS:
        raise GridError(
            f"{dates[0].isoformat()} is too far back: the rainfall source keeps "
            f"{MAX_PAST_DAYS} days, and scoring a day reads the {DEFAULT_PAST_DAYS} before it."
        )

    result = GridResult(dates=dates, model_version=scorer.MODEL_VERSION, cells=len(cells))
    if scorer.rain_points(model) > 1:
        result.warnings.append(
            "The model was trained on rainfall averaged over each county; a cell's "
            "rainfall is read at one point, so its 7-day peak runs higher than the "
            "model is used to."
        )

    series = fetch([Point(lon=cell.lon, lat=cell.lat) for cell in cells], past_days)
    thresholds = scorer.model_thresholds(model)

    with transaction.atomic():
        for date in dates:
            rows = [
                scorer.region_row(model, cell.county, rain, date, terrain=cell)
                for cell, rain in zip(cells, series)
            ]
            probabilities = scorer.flood_probabilities(model, rows)
            scores = []
            for cell, rain, probability in zip(cells, series, probabilities):
                rain_30d = rainfall_features(rain, date)["rain_30d"]
                normal = model["normals"].get(cell.county.county_name, {}).get(date.month)
                category = scorer.classify(probability, thresholds)
                result.categories[category] += 1
                scores.append(
                    GridRiskScore(
                        cell=cell,
                        date=date,
                        predicted_value=round(probability, 4),
                        risk_category=category,
                        rain_30d=_number(rain_30d),
                        rain_30d_ratio=_number(rain_30d / normal) if normal else None,
                        model_version=scorer.MODEL_VERSION,
                    )
                )
            GridRiskScore.objects.filter(date=date).delete()
            GridRiskScore.objects.bulk_create(scores)
    return result
