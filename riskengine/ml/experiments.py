"""Score a change to the model's inputs before it is adopted.

One controlled change at a time: the current inputs against the current
inputs plus the ones under trial, on the same folds of held-out years, with
the same model and the same seeds. Nothing here writes a model. The shipped
one in artifacts/ changes only when a kept input is moved into
FEATURE_NAMES and the model is retrained on Colab.

The inputs under trial (CANDIDATE_FEATURES) are worked out from cached daily
series: the rainfall `build_training_set` downloaded, the river discharge
`build_discharge` downloaded and the soil moisture read from Earth Engine.
The training set itself is not rebuilt for a trial. Another measurement of rainfall (the mean over
the county in place of one point) is tried the same way, by working the
rainfall inputs out again and leaving every other column and the labels as
they are.

The Random Forest is the model compared, because it is the one selected.
It draws random numbers, so every figure is a mean over SEEDS: a difference
smaller than the spread between seeds is not a difference.

A change to the labels (`compare_labels`) cannot be judged by one number,
because it changes what is being predicted. Each set of labels is scored
against itself, with its own base rate and season-only model beside it, and
the model trained on the current labels is also scored against the new
ones, which shows whether learning from the new labels helps predict them.

The final test years play no part. `manage.py compare_inputs` and
`manage.py compare_labels` print the result, and it is recorded in the
experiments log in riskengine/ml/README.md whether the change is kept or
not.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
from sklearn.base import clone
from sklearn.metrics import average_precision_score
from sklearn.model_selection import GroupKFold

from riskengine.ml import train
from riskengine.ml.features import (
    CANDIDATE_FEATURES,
    DISCHARGE_CANDIDATES,
    RAIN_CANDIDATES,
    SOIL_CANDIDATES,
    discharge_features,
    rainfall_features,
    soil_features,
)

MODEL = "random-forest"

# The two places the trials are aimed at, by what `error_breakdown` showed:
# June, and the counties that flood from short intense rain.
JUNE = 6
INTENSE_RAIN_COUNTIES = ("Nairobi", "Kilifi", "Lamu", "Kwale")

# A change in pooled average precision smaller than this is treated as no
# change: about twice the standard deviation across seeds (0.0027).
NOISE = 0.005

# The inputs `with_rainfall` works out again. `rain_30d_ratio` follows,
# because training recomputes it from `rain_30d` in every fold.
RAIN_INPUTS = ("rain_30d", "rain_7d_max", "rain_prev_30d")


def cached_rainfall(cache_dir: Path, counties) -> dict[str, dict[dt.date, float]]:
    """Each county's longest cached daily series, as `open_meteo_rainfall` wrote it.

    The files are named <county>_<start>_<end>.json. The longest one is
    taken so a 90-day window has rain behind the first training month.
    """
    rainfall = {}
    for county in counties:
        longest, span = None, dt.timedelta(0)
        for path in cache_dir.glob(f"{county}_*_*.json"):
            name, start, end = path.stem.rsplit("_", 2)
            if name != county:
                continue  # another county whose name starts the same way
            length = dt.date.fromisoformat(end) - dt.date.fromisoformat(start)
            if length > span:
                longest, span = path, length
        if longest is None:
            raise FileNotFoundError(f"No cached rainfall for {county} in {cache_dir}")
        rainfall[county] = {
            dt.date.fromisoformat(day): mm for day, mm in json.loads(longest.read_text()).items()
        }
    return rainfall


def with_candidates(
    data: train.TrainingSet,
    rainfall: dict[str, dict[dt.date, float]],
    names,
    discharge: dict[str, dict[dt.date, float]] | None = None,
    soil: dict[str, dict[dt.date, float]] | None = None,
) -> train.TrainingSet:
    """`data` with the trial inputs `names` added as columns.

    Rain inputs are worked out from `rainfall`, river inputs from
    `discharge` and soil inputs from `soil`, each {county: {date: value}}.
    Raises when the cached rainfall does not reproduce the `rain_30d` the
    training set already holds, since the trial columns would then describe
    different rain from the rest of the row.
    """
    unknown = [name for name in names if name not in CANDIDATE_FEATURES]
    if unknown:
        raise ValueError(f"Not inputs under trial: {', '.join(unknown)}")
    sources = (
        (RAIN_CANDIDATES, rainfall, rainfall_features, "rainfall"),
        (DISCHARGE_CANDIDATES, discharge, discharge_features, "river discharge"),
        (SOIL_CANDIDATES, soil, soil_features, "soil moisture"),
    )
    for group, series, _, what in sources:
        if series is None and set(names) & set(group):
            raise ValueError(f"No {what} given for {', '.join(n for n in names if n in group)}.")

    keys = list(zip(data.counties, (dt.date.fromisoformat(date) for date in data.dates)))
    computed = [{} for _ in keys]
    for group, series, features, _ in sources:
        if not set(names) & set(group):
            continue
        for row, (county, end) in zip(computed, keys):
            row.update(features(series.get(county, {}), end))

    if set(names) & set(RAIN_CANDIDATES):
        stored = data.X[:, data.column("rain_30d")]
        recomputed = np.array([row["rain_30d"] for row in computed])
        known = ~np.isnan(stored) & ~np.isnan(recomputed)
        if not np.allclose(stored[known], recomputed[known], atol=0.01):
            raise ValueError("The cached rainfall does not match the training set's rain_30d.")

    extra = np.array([[row[name] for name in names] for row in computed], dtype=float)
    return train.TrainingSet(
        features=(*data.features, *names),
        X=np.hstack([data.X, extra]),
        y=data.y,
        years=data.years,
        months=data.months,
        counties=data.counties,
        dates=data.dates,
        meta=data.meta,
    )


def with_rainfall(
    data: train.TrainingSet, rainfall: dict[str, dict[dt.date, float]]
) -> train.TrainingSet:
    """`data` with its rainfall inputs worked out again from `rainfall`."""
    X = data.X.copy()
    for index, (county, date) in enumerate(zip(data.counties, data.dates)):
        rain = rainfall_features(rainfall[county], dt.date.fromisoformat(date))
        for name in RAIN_INPUTS:
            if name in data.features:
                X[index, data.column(name)] = rain[name]
    return train.TrainingSet(
        features=data.features,
        X=X,
        y=data.y,
        years=data.years,
        months=data.months,
        counties=data.counties,
        dates=data.dates,
        meta=data.meta,
    )


def _missed(flooded, warned, inside) -> tuple[int, int]:
    """(floods with no warning, floods) among the rows `inside`."""
    return int((inside & flooded & ~warned).sum()), int((inside & flooded).sum())


def with_labels(data: train.TrainingSet, labels: np.ndarray) -> train.TrainingSet:
    """`data` with `labels` as its target and everything else unchanged."""
    return train.TrainingSet(
        features=data.features,
        X=data.X,
        y=np.asarray(labels, dtype=int),
        years=data.years,
        months=data.months,
        counties=data.counties,
        dates=data.dates,
        meta=data.meta,
    )


def evaluate(
    data: train.TrainingSet,
    seeds=train.SEEDS,
    truth: np.ndarray | None = None,
    weights: np.ndarray | None = None,
    score_rows: np.ndarray | None = None,
) -> dict:
    """The selected model's held-out scores on `data`, averaged over `seeds`.

    The same folds as `train.select` makes, so two calls on training sets
    that differ only in their columns or labels are compared row for row.
    The model always learns from `data.y`, each row counted by `weights` if
    given. With `truth`, its held-out probabilities are scored against those
    labels instead. With `score_rows`, a boolean mask, only those rows are
    scored; the model still learns from every row.
    """
    folds = list(GroupKFold(train.CV_FOLDS).split(data.X, data.y, groups=data.years))
    columns = list(range(len(data.features)))
    pipeline = train.candidates()[MODEL]
    seed_steps = train._random_steps(pipeline)
    truth = data.y if truth is None else np.asarray(truth, dtype=int)
    scored = np.ones(len(truth), dtype=bool) if score_rows is None else np.asarray(score_rows, dtype=bool)
    flooded = truth == 1
    june = data.months == JUNE
    intense = np.isin(data.counties, INTENSE_RAIN_COUNTIES)

    runs = []
    for seed in seeds:
        seeded = clone(pipeline).set_params(**{step: seed for step in seed_steps})
        held_out = train.held_out_probabilities(data, lambda: clone(seeded), columns, folds, weights)
        warned = held_out > train.WARNING_THRESHOLD
        fold_precision = [
            average_precision_score(truth[rows], held_out[rows])
            for _train_rows, test_rows in folds
            for rows in [test_rows[scored[test_rows]]]
            if 0 < truth[rows].sum() < len(rows)
        ]
        runs.append(
            {
                **train._scores(truth[scored], held_out[scored]),
                "average_precision_fold_mean": float(np.mean(fold_precision)),
                "floods_missed": _missed(flooded, warned, scored)[0],
                "false_alarms": int((~flooded & warned & scored).sum()),
                "june_missed": _missed(flooded, warned, june & scored)[0],
                "intense_rain_missed": _missed(flooded, warned, intense & scored)[0],
            }
        )

    # The season-only model learns from the same labels as the forest and is
    # scored against the same truth. It has nothing random in it.
    season_columns = [data.column(name) for name in train.SEASON_FEATURES if name in data.features]
    season_precision = None
    if season_columns:
        season = train.held_out_probabilities(data, train.season_baseline, season_columns, folds, weights)
        season_precision = round(float(average_precision_score(truth[scored], season[scored])), 4)
    out = {
        "features": list(data.features),
        "seeds": [int(seed) for seed in seeds],
        "rows": int(scored.sum()),
        "base_rate": round(float(flooded[scored].mean()), 4),
        "season_only_average_precision": season_precision,
        "floods": int((flooded & scored).sum()),
        "june_floods": int((flooded & june & scored).sum()),
        "intense_rain_floods": int((flooded & intense & scored).sum()),
    }
    for measure in runs[0]:
        values = [run[measure] for run in runs]
        out[measure] = {
            "mean": round(float(np.mean(values)), 4),
            "sd": round(float(np.std(values, ddof=1)), 4) if len(values) > 1 else None,
        }
    return out


def compare(
    data: train.TrainingSet,
    rainfall,
    names=(),
    seeds=train.SEEDS,
    other_rainfall=None,
    discharge=None,
    soil=None,
) -> dict:
    """The current model against one with `names` added, on `other_rainfall` if given.

    `rainfall` is the rainfall `data` was built on. With `other_rainfall`,
    the changed model's rainfall inputs, the trial inputs included, are
    worked out from it instead. River and soil inputs are worked out from
    `discharge` and `soil`.
    """
    before = evaluate(data, seeds)
    changed = data
    if other_rainfall is not None:
        changed = with_rainfall(changed, other_rainfall)
    if names:
        changed = with_candidates(changed, other_rainfall or rainfall, names, discharge, soil)
    after = evaluate(changed, seeds)
    change = round(after["average_precision"]["mean"] - before["average_precision"]["mean"], 4)
    return {
        "added": list(names),
        "before": before,
        "after": after,
        "average_precision_change": change,
        "beyond_noise": abs(change) > NOISE,
    }


def compare_labels(
    data: train.TrainingSet,
    labels: np.ndarray,
    seeds=train.SEEDS,
    weights: np.ndarray | None = None,
    sure_rows: np.ndarray | None = None,
) -> dict:
    """The current labels against `labels`, for the same rows and inputs.

    `current` and `new` are each trained and scored on their own labels.
    `current_on_new` is trained on the current labels and scored against
    the new ones. With `weights`, `new_weighted` is trained on the new
    labels with those row weights.

    With `sure_rows`, a boolean mask, every model is also scored on the new
    labels of those rows alone (the `*_on_sure` entries): the rows where the
    new labels are best evidence of where the water was. A model trained on
    the new labels has to beat the current one there to be worth adopting.
    """
    new = with_labels(data, labels)
    out = {
        "current": evaluate(data, seeds),
        "new": evaluate(new, seeds),
        "current_on_new": evaluate(data, seeds, truth=labels),
    }
    if weights is not None:
        out["new_weighted"] = evaluate(new, seeds, weights=weights)
    if sure_rows is not None:
        out["current_on_sure"] = evaluate(data, seeds, truth=labels, score_rows=sure_rows)
        out["new_on_sure"] = evaluate(new, seeds, score_rows=sure_rows)
        if weights is not None:
            out["new_weighted_on_sure"] = evaluate(new, seeds, weights=weights, score_rows=sure_rows)
    return out
