"""Score a change to the model's inputs before it is adopted.

One controlled change at a time: the current inputs against the current
inputs plus the ones under trial, on the same folds of held-out years, with
the same model and the same seeds. Nothing here writes a model. The shipped
one in artifacts/ changes only when a kept input is moved into
FEATURE_NAMES and the model is retrained on Colab.

The inputs under trial (CANDIDATE_FEATURES) are worked out from the cached
daily rainfall `build_training_set` downloaded, so the training set itself
is not rebuilt for a trial. Another measurement of rainfall (the mean over
the county in place of one point) is tried the same way, by working the
rainfall inputs out again and leaving every other column and the labels as
they are.

The Random Forest is the model compared, because it is the one selected.
It draws random numbers, so every figure is a mean over SEEDS: a difference
smaller than the spread between seeds is not a difference.

The final test years play no part. `manage.py compare_inputs` prints the
result, and it is recorded in the experiments log in riskengine/ml/README.md
whether the change is kept or not.
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
from riskengine.ml.features import CANDIDATE_FEATURES, rainfall_features

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
    data: train.TrainingSet, rainfall: dict[str, dict[dt.date, float]], names
) -> train.TrainingSet:
    """`data` with the trial inputs `names` added as columns, from `rainfall`.

    Raises when the cached rainfall does not reproduce the `rain_30d` the
    training set already holds, since the trial columns would then describe
    different rain from the rest of the row.
    """
    unknown = [name for name in names if name not in CANDIDATE_FEATURES]
    if unknown:
        raise ValueError(f"Not inputs under trial: {', '.join(unknown)}")

    computed = [
        rainfall_features(rainfall[county], dt.date.fromisoformat(date))
        for county, date in zip(data.counties, data.dates)
    ]
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


def evaluate(data: train.TrainingSet, seeds=train.SEEDS) -> dict:
    """The selected model's held-out scores on `data`, averaged over `seeds`.

    The same folds as `train.select` makes, so two calls on training sets
    that differ only in their columns are compared row for row.
    """
    folds = list(GroupKFold(train.CV_FOLDS).split(data.X, data.y, groups=data.years))
    columns = list(range(len(data.features)))
    pipeline = train.candidates()[MODEL]
    seed_steps = train._random_steps(pipeline)
    flooded = data.y == 1
    june = data.months == JUNE
    intense = np.isin(data.counties, INTENSE_RAIN_COUNTIES)

    runs = []
    for seed in seeds:
        seeded = clone(pipeline).set_params(**{step: seed for step in seed_steps})
        held_out = train.held_out_probabilities(data, lambda: clone(seeded), columns, folds)
        warned = held_out > train.WARNING_THRESHOLD
        fold_precision = [
            average_precision_score(data.y[test_rows], held_out[test_rows])
            for _train_rows, test_rows in folds
        ]
        runs.append(
            {
                **train._scores(data.y, held_out),
                "average_precision_fold_mean": float(np.mean(fold_precision)),
                "floods_missed": _missed(flooded, warned, np.ones(len(data.y), dtype=bool))[0],
                "false_alarms": int((~flooded & warned).sum()),
                "june_missed": _missed(flooded, warned, june)[0],
                "intense_rain_missed": _missed(flooded, warned, intense)[0],
            }
        )

    out = {
        "features": list(data.features),
        "seeds": [int(seed) for seed in seeds],
        "floods": int(flooded.sum()),
        "june_floods": int((flooded & june).sum()),
        "intense_rain_floods": int((flooded & intense).sum()),
    }
    for measure in runs[0]:
        values = [run[measure] for run in runs]
        out[measure] = {
            "mean": round(float(np.mean(values)), 4),
            "sd": round(float(np.std(values, ddof=1)), 4) if len(values) > 1 else None,
        }
    return out


def compare(
    data: train.TrainingSet, rainfall, names=(), seeds=train.SEEDS, other_rainfall=None
) -> dict:
    """The current model against one with `names` added, on `other_rainfall` if given.

    `rainfall` is the rainfall `data` was built on. With `other_rainfall`,
    the changed model's rainfall inputs, the trial inputs included, are
    worked out from it instead.
    """
    before = evaluate(data, seeds)
    changed = data
    if other_rainfall is not None:
        changed = with_rainfall(changed, other_rainfall)
    if names:
        changed = with_candidates(changed, other_rainfall or rainfall, names)
    after = evaluate(changed, seeds)
    change = round(after["average_precision"]["mean"] - before["average_precision"]["mean"], 4)
    return {
        "added": list(names),
        "before": before,
        "after": after,
        "average_precision_change": change,
        "beyond_noise": abs(change) > NOISE,
    }
