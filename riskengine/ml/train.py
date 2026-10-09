"""Fit and compare the three Chapter 3 models, and package the winner.

The target is `flood`: did the Dartmouth Flood Observatory record a large
flood touching the county in the 30-day window. The model output is the
probability of that.

Models are compared by cross-validation that holds out whole *years*. Rows
from the same month are near-copies of each other (one El Niño index, one
regional rain system, one flood event covering many counties), so a random
split would let a model recognise the month instead of learning what causes
a flood, and score far better than it deserves. The winner is the one with
the best average precision on years it never saw.

Three things keep those held-out scores honest:

* Only years from FIRST_TRAINING_YEAR on are used. The archive records
  almost no Kenyan floods before 1996, so a wet month before then is
  labelled "no flood" because nobody reported one, not because none
  happened. Training on those rows teaches the model that rain does not
  matter.
* `rain_30d_ratio` divides by the county's normal rainfall for the month.
  Inside cross-validation that normal is recomputed from the training
  folds only, so a held-out year never contributes to its own normal.
* A season-only model (month of the year and nothing else) is scored
  alongside the base rate. It is the bar the real models have to clear to
  show that rainfall, El Niño and terrain add anything beyond "it is the
  rainy season".

Each candidate's settings are searched over a small grid (`tune`), and the
grid's choice is used only where it scores higher than the fixed settings
under that same honest procedure.

`permutation_importances` and `_error_breakdown` say what the chosen model
leans on and where it is wrong: which inputs its held-out score depends on,
and which counties and months its missed floods and false alarms fall in.

Cross-validation still picks the winner and reports its score from the same
folds. `final_test` is the check on that: the years from FINAL_TEST_FROM on
are set aside, the choice is made again without them, and they are scored
once. The model that is saved is fitted on every year, because recent years
are the most useful ones to have learned from.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import platform
import time
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.base import clone
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import GridSearchCV, GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from riskengine.ml.calibration import METHODS, CalibratedModel, Calibrator
from riskengine.ml.features import FEATURE_NAMES, SHORT_RAINS_MONTHS

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_PATH = ARTIFACT_DIR / "flood_model.joblib"
METRICS_PATH = ARTIFACT_DIR / "metrics.json"

# How a Colab run, which is not a git checkout, is tied to its commit and to
# its folder of outputs. `build_colab_bundle` writes the file; the notebook
# sets the two variables.
COMMIT_FILE = "COMMIT"
COMMIT_ENV = "FLOODSCOUT_GIT_COMMIT"
RUN_ID_ENV = "FLOODSCOUT_RUN_ID"

CV_FOLDS = 6
RANDOM_STATE = 0

# See the module docstring. Rows before this year are dropped.
FIRST_TRAINING_YEAR = 1996

# Years from this one on are the final test: set aside before any model is
# compared, and scored once (see `final_test`). Fixed on 5 October 2026,
# before the result was seen. Do not move it to get a better number.
FINAL_TEST_FROM = 2017

# The inputs of the season-only baseline.
SEASON_FEATURES = ("month_sin", "month_cos")

# Upper bound of Low, Moderate and High as a flood probability; above the
# last is Very High. Recorded floods are rare (about 7% of county-months),
# so the bands sit around that base rate rather than at quarters of 0-1.
# `band_calibration` in metrics.json shows how often floods were actually
# recorded in each band on held-out years.
PROBABILITY_BANDS = (0.05, 0.10, 0.20)

# A probability above this is a warning in the error breakdown: the High
# and Very High bands.
WARNING_THRESHOLD = PROBABILITY_BANDS[1]

# The settings each candidate is searched over, by pipeline parameter. Kept
# small: six folds of whole years cannot tell fine settings apart. Every
# grid contains the fixed setting in `candidates()`, so the search can also
# conclude that nothing beats it. The number of trees is not searched,
# because more trees only make a forest less noisy.
PARAM_GRIDS = {
    "logreg": {"logisticregression__C": [0.01, 0.1, 1.0, 10.0]},
    "random-forest": {
        "randomforestclassifier__min_samples_leaf": [5, 20, 50],
        "randomforestclassifier__max_features": ["sqrt", 0.5],
    },
    "gradient-boosting": {
        "gradientboostingclassifier__max_depth": [2, 3],
        "gradientboostingclassifier__learning_rate": [0.02, 0.05, 0.1],
    },
}

# Times each input is shuffled per fold when measuring its importance.
PERMUTATION_REPEATS = 5

# Seeds the cross-validation is repeated with, to show how much the scores
# move for no reason but the random numbers. The saved model is RANDOM_STATE.
SEEDS = (0, 1, 2, 3, 4)
# Shares of each fold's training years the learning curve is drawn at.
LEARNING_CURVE_FRACTIONS = (0.25, 0.5, 0.75, 1.0)


def candidates() -> dict:
    return {
        "logreg": make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LogisticRegression(max_iter=1000),
        ),
        "random-forest": make_pipeline(
            SimpleImputer(strategy="median"),
            RandomForestClassifier(
                n_estimators=300, min_samples_leaf=20, n_jobs=-1, random_state=RANDOM_STATE
            ),
        ),
        "gradient-boosting": make_pipeline(
            SimpleImputer(strategy="median"),
            GradientBoostingClassifier(
                n_estimators=200,
                max_depth=3,
                learning_rate=0.05,
                subsample=0.8,
                random_state=RANDOM_STATE,
            ),
        ),
    }


def season_baseline():
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))


@dataclass
class TrainingSet:
    """The training CSV as arrays, one entry per county-month."""

    features: tuple[str, ...]  # the columns of X, in order
    X: np.ndarray
    y: np.ndarray
    years: np.ndarray
    months: np.ndarray
    counties: np.ndarray
    dates: list[str]
    meta: dict  # what build_training_set recorded about how it was built

    def column(self, name: str) -> int:
        return self.features.index(name)


def meta_path(training_set: Path) -> Path:
    """Where `build_training_set` records how the CSV was built."""
    return training_set.with_suffix(".meta.json")


def load_training_set(path: Path, first_year: int = FIRST_TRAINING_YEAR) -> TrainingSet:
    """Read the CSV `build_training_set` writes, keeping rows from `first_year` on.

    Uses every input in FEATURE_NAMES that the CSV has, so a training set
    built before an input was added still trains (without that input).
    """
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [row for row in reader if int(row["date"][:4]) >= first_year]
        columns = set(reader.fieldnames or ())
    if not rows:
        raise ValueError(f"No rows from {first_year} on in {path}")

    # Inputs that are a function of others are derived when an older CSV
    # lacks them, so it gets their benefit without a rebuild.
    for derived, source in (("oni_short_rains", "oni"), ("dmi_short_rains", "dmi")):
        if derived not in columns and source in columns:
            for row in rows:
                in_season = int(row["date"][5:7]) in SHORT_RAINS_MONTHS
                row[derived] = row[source] if in_season else "0"
            columns.add(derived)
    features = tuple(name for name in FEATURE_NAMES if name in columns)

    meta = {}
    if meta_path(path).exists():
        meta = json.loads(meta_path(path).read_text())

    return TrainingSet(
        features=features,
        X=np.array([[float(row[name]) for name in features] for row in rows]),
        y=np.array([int(row["flood"]) for row in rows]),
        years=np.array([int(row["date"][:4]) for row in rows]),
        months=np.array([int(row["date"][5:7]) for row in rows]),
        counties=np.array([row["county"] for row in rows]),
        dates=[row["date"] for row in rows],
        meta=meta,
    )


def county_normals(data: TrainingSet, rows: np.ndarray) -> dict[str, dict[int, float]]:
    """Mean `rain_30d` per county and calendar month, over `rows` only."""
    rain = data.X[:, data.column("rain_30d")]
    sums: dict[tuple[str, int], list[float]] = {}
    for index in rows:
        if rain[index] == rain[index]:  # not NaN
            sums.setdefault((data.counties[index], int(data.months[index])), []).append(rain[index])
    normals: dict[str, dict[int, float]] = {}
    for (county, month), values in sums.items():
        normals.setdefault(county, {})[month] = float(np.mean(values))
    return normals


def with_normals(data: TrainingSet, normals: dict[str, dict[int, float]]) -> np.ndarray:
    """X with `rain_30d_ratio` recomputed against `normals`."""
    X = data.X.copy()
    if "rain_30d_ratio" not in data.features:
        return X
    rain = X[:, data.column("rain_30d")]
    ratio = np.full(len(X), np.nan)
    for index, (county, month) in enumerate(zip(data.counties, data.months)):
        normal = normals.get(county, {}).get(int(month))
        if normal:
            ratio[index] = rain[index] / normal
    X[:, data.column("rain_30d_ratio")] = ratio
    return X


def held_out_probabilities(data: TrainingSet, make_model, columns, folds) -> np.ndarray:
    """Each row's flood probability from a model that never saw its year."""
    out = np.empty(len(data.y))
    for train_rows, test_rows in folds:
        X = with_normals(data, county_normals(data, train_rows))[:, columns]
        model = make_model().fit(X[train_rows], data.y[train_rows])
        out[test_rows] = model.predict_proba(X[test_rows])[:, 1]
    return out


def tune(data: TrainingSet, folds) -> dict[str, dict]:
    """The grid-search choice of settings for each candidate.

    Scored by average precision on the held-out years of `folds`.
    GridSearchCV cannot refit the county normals inside each fold, so here
    `rain_30d_ratio` uses normals from every row of `data`. That small leak
    touches only which settings are proposed: `select` scores the proposal
    with per-fold normals, like everything else, before using it.
    """
    X = with_normals(data, county_normals(data, np.arange(len(data.y))))
    chosen = {}
    for name, pipeline in candidates().items():
        search = GridSearchCV(
            pipeline, PARAM_GRIDS[name], scoring="average_precision", cv=folds, refit=False, n_jobs=-1
        )
        chosen[name] = search.fit(X, data.y).best_params_
    return chosen


def _short(params: dict) -> dict:
    """Pipeline parameters without the step prefix, for metrics.json."""
    return {name.split("__", 1)[-1]: value for name, value in params.items()}


def permutation_importances(data: TrainingSet, make_model, folds) -> dict[str, dict]:
    """How far held-out average precision falls when one input is shuffled.

    Measured fold by fold on the years the model did not see, so an input
    the model merely memorised scores nothing. Mean and standard deviation
    over the folds. Inputs that carry the same information share the credit:
    shuffling `rain_30d` leaves `rain_30d_ratio` intact.
    """
    drops = []
    for train_rows, test_rows in folds:
        X = with_normals(data, county_normals(data, train_rows))
        model = make_model().fit(X[train_rows], data.y[train_rows])
        result = permutation_importance(
            model,
            X[test_rows],
            data.y[test_rows],
            scoring="average_precision",
            n_repeats=PERMUTATION_REPEATS,
            random_state=RANDOM_STATE,
        )
        drops.append(result.importances_mean)
    drops = np.array(drops)
    return {
        name: {
            "mean": round(float(drops[:, column].mean()), 4),
            "sd": round(float(drops[:, column].std(ddof=1)), 4) if len(drops) > 1 else None,
        }
        for column, name in enumerate(data.features)
    }


def _error_breakdown(data: TrainingSet, probabilities, threshold: float = WARNING_THRESHOLD) -> dict:
    """Missed floods and false alarms by calendar month and by county.

    A probability above `threshold` is a warning. Shows whether the errors
    sit in particular seasons or places.
    """
    warned = probabilities > threshold
    flooded = data.y == 1

    def count(inside) -> dict:
        return {
            "rows": int(inside.sum()),
            "floods": int((inside & flooded).sum()),
            "caught": int((inside & flooded & warned).sum()),
            "missed": int((inside & flooded & ~warned).sum()),
            "false_alarms": int((inside & ~flooded & warned).sum()),
        }

    return {
        "threshold": threshold,
        "by_month": [
            {"month": int(month), **count(data.months == month)} for month in sorted(set(data.months))
        ],
        "by_county": [
            {"county": str(county), **count(data.counties == county)}
            for county in sorted(set(data.counties))
        ],
    }


def cross_calibrated(y, probabilities, folds, method: str) -> np.ndarray:
    """Held-out probabilities, each calibrated by a map fitted on the other folds.

    The held-out probabilities are already honest; this keeps the
    calibration step honest too, by never fitting it on the fold it is
    scored on.
    """
    out = np.empty(len(y))
    for train_rows, test_rows in folds:
        calibrator = Calibrator(method).fit(probabilities[train_rows], y[train_rows])
        out[test_rows] = calibrator.predict(probabilities[test_rows])
    return out


def _scores(y, probabilities) -> dict:
    return {
        "roc_auc": round(float(roc_auc_score(y, probabilities)), 4),
        "average_precision": round(float(average_precision_score(y, probabilities)), 4),
        "brier": round(float(brier_score_loss(y, probabilities)), 4),
    }


def _band_calibration(y, probabilities) -> list[dict]:
    """Per risk band: how many held-out rows fell in it and how many flooded."""
    edges = (0.0, *PROBABILITY_BANDS, 1.0 + 1e-9)
    bands = []
    for name, low, high in zip(("low", "moderate", "high", "very_high"), edges, edges[1:]):
        inside = (probabilities > low) & (probabilities <= high) if low else probabilities <= high
        bands.append(
            {
                "band": name,
                "rows": int(inside.sum()),
                "mean_predicted": round(float(probabilities[inside].mean()), 4) if inside.any() else None,
                "observed_flood_rate": round(float(y[inside].mean()), 4) if inside.any() else None,
            }
        )
    return bands


def _fold_scores(y, years, folds, held_out: dict[str, np.ndarray]) -> list[dict]:
    """Each fold's held-out years and every model's scores on them.

    The pooled scores hide how much a result depends on which years were
    held out. A score is None when a fold holds one class only.
    """
    out = []
    for _train_rows, test_rows in folds:
        both_classes = 0 < y[test_rows].sum() < len(test_rows)
        scores = {}
        for name, probabilities in held_out.items():
            scores[name] = {
                "average_precision": round(
                    float(average_precision_score(y[test_rows], probabilities[test_rows])), 4
                ) if both_classes else None,
                "roc_auc": round(
                    float(roc_auc_score(y[test_rows], probabilities[test_rows])), 4
                ) if both_classes else None,
            }
        out.append(
            {
                "years": sorted({int(year) for year in years[test_rows]}),
                "rows": len(test_rows),
                "positives": int(y[test_rows].sum()),
                "scores": scores,
            }
        )
    return out


def _fold_summary(fold_scores: list[dict]) -> dict:
    """Mean and sample standard deviation of each model's per-fold scores."""
    summary = {}
    for name in fold_scores[0]["scores"]:
        summary[name] = {}
        for metric in ("average_precision", "roc_auc"):
            values = [
                fold["scores"][name][metric]
                for fold in fold_scores
                if fold["scores"][name][metric] is not None
            ]
            summary[name][f"{metric}_mean"] = round(float(np.mean(values)), 4) if values else None
            summary[name][f"{metric}_sd"] = (
                round(float(np.std(values, ddof=1)), 4) if len(values) > 1 else None
            )
    return summary


def _threshold_errors(y, probabilities) -> list[dict]:
    """The confusion matrix when a probability above each band edge is a warning.

    Above 0.10 is "High or Very High", above 0.20 is "Very High" alone. A
    false negative is a recorded flood with no warning; a false positive is
    a warning with no recorded flood.
    """
    out = []
    for threshold in PROBABILITY_BANDS:
        warned = probabilities > threshold
        tp = int((warned & (y == 1)).sum())
        fp = int((warned & (y == 0)).sum())
        fn = int((~warned & (y == 1)).sum())
        tn = int((~warned & (y == 0)).sum())
        out.append(
            {
                "threshold": threshold,
                "true_positives": tp,
                "false_positives": fp,
                "false_negatives": fn,
                "true_negatives": tn,
                "precision": round(tp / (tp + fp), 4) if tp + fp else None,
                "recall": round(tp / (tp + fn), 4) if tp + fn else None,
                "specificity": round(tn / (tn + fp), 4) if tn + fp else None,
                "accuracy": round((tp + tn) / len(y), 4),
                "f1": round(2 * tp / (2 * tp + fp + fn), 4) if tp + fp + fn else None,
                # The mean of recall and specificity, which a rare class
                # cannot inflate the way it inflates accuracy.
                "balanced_accuracy": (
                    round((tp / (tp + fn) + tn / (tn + fp)) / 2, 4) if tp + fn and tn + fp else None
                ),
                "support_positive": tp + fn,
                "support_negative": tn + fp,
            }
        )
    return out


def _random_steps(pipeline) -> dict:
    """The pipeline parameters that seed a random number generator.

    A step whose seed is unset draws no random numbers at its settings here
    (Logistic Regression with the default solver), so it is left out.
    """
    return {
        name: value
        for name, value in pipeline.get_params().items()
        if name.endswith("__random_state") and value is not None
    }


def _mean_sd(values: list[float]) -> dict:
    return {
        "mean": round(float(np.mean(values)), 4),
        "sd": round(float(np.std(values, ddof=1)), 4) if len(values) > 1 else None,
        "values": [round(float(value), 4) for value in values],
    }


def seed_variation(data: TrainingSet, pipelines: dict, folds, seeds=SEEDS) -> dict:
    """How far the held-out scores move when only the random seed changes.

    Each candidate is cross-validated once per seed on the same folds, at
    the settings that were kept. Mean and sample standard deviation over the
    seeds, with the score for each seed in `values`, in the order of
    `seeds`. A candidate with nothing random in it is fitted once and
    marked deterministic. Cross-validation only: the final test plays no
    part.
    """
    columns = list(range(len(data.features)))
    out = {"seeds": [int(seed) for seed in seeds]}
    for name, pipeline in pipelines.items():
        random_steps = _random_steps(pipeline)
        if not random_steps:
            out[name] = {"note": "deterministic"}
            continue
        scores = []
        for seed in seeds:
            seeded = clone(pipeline).set_params(**{step: seed for step in random_steps})
            held_out = held_out_probabilities(data, lambda: clone(seeded), columns, folds)
            scores.append(_scores(data.y, held_out))
        out[name] = {
            metric: _mean_sd([score[metric] for score in scores])
            for metric in ("average_precision", "roc_auc")
        }
    return out


def sample_years(years: np.ndarray, fraction: float, order: np.ndarray) -> np.ndarray:
    """The first `fraction` of the years in `order`, at least one.

    `order` is a shuffle of the distinct years, so a larger fraction always
    contains the smaller one. Whole years are taken, never single rows: rows
    of one year are near-copies of each other, as with the folds.
    """
    kept = order[: max(1, int(np.ceil(fraction * len(order))))]
    return np.isin(years, kept)


def learning_curve(
    data: TrainingSet, make_model, folds, fractions=LEARNING_CURVE_FRACTIONS
) -> list[dict]:
    """Held-out average precision against how many training years the model saw.

    In each fold the model is fitted on a share of the training years,
    with the county normals from those years only, and scored on the same
    held-out years every time. Mean and sample standard deviation over the
    folds. Still rising at 1.0 means more recorded years would help; flat
    means the inputs or the labels are the limit.
    """
    rng = np.random.default_rng(RANDOM_STATE)
    orders = [rng.permutation(np.unique(data.years[train_rows])) for train_rows, _ in folds]
    curve = []
    for fraction in fractions:
        scores, year_counts, row_counts = [], [], []
        for (train_rows, test_rows), order in zip(folds, orders):
            rows = train_rows[sample_years(data.years[train_rows], fraction, order)]
            y_test = data.y[test_rows]
            if not (0 < data.y[rows].sum() < len(rows) and 0 < y_test.sum() < len(y_test)):
                continue  # one class only: nothing to fit or to rank
            X = with_normals(data, county_normals(data, rows))
            model = make_model().fit(X[rows], data.y[rows])
            scores.append(average_precision_score(y_test, model.predict_proba(X[test_rows])[:, 1]))
            year_counts.append(len(np.unique(data.years[rows])))
            row_counts.append(len(rows))
        curve.append(
            {
                "fraction": fraction,
                "folds": len(scores),
                "training_years_mean": round(float(np.mean(year_counts)), 1) if scores else None,
                "training_rows_mean": round(float(np.mean(row_counts))) if scores else None,
                "average_precision_mean": round(float(np.mean(scores)), 4) if scores else None,
                "average_precision_sd": (
                    round(float(np.std(scores, ddof=1)), 4) if len(scores) > 1 else None
                ),
            }
        )
    return curve


def _processor() -> str:
    """The processor's name where the system gives one, else its architecture."""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def _commit_from_git_dir(root: Path) -> str | None:
    """The commit checked out in `root`, read from .git without needing git."""
    git_dir = root / ".git"
    head = git_dir / "HEAD"
    if not head.is_file():
        return None
    content = head.read_text().strip()
    if not content.startswith("ref: "):
        return content
    ref = content[5:]
    if (git_dir / ref).is_file():
        return (git_dir / ref).read_text().strip()
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text().splitlines():
            if line.endswith(" " + ref):
                return line.split(" ", 1)[0]
    return None


def git_commit(root: Path = REPO_ROOT) -> str | None:
    """The commit the code came from.

    Order: a real .git checkout, then the FLOODSCOUT_GIT_COMMIT environment
    variable, then the COMMIT file `build_colab_bundle` writes into the
    bundle, which is not a checkout.
    """
    found = _commit_from_git_dir(root)
    if found:
        return found
    from_env = os.environ.get(COMMIT_ENV, "").strip()
    if from_env:
        return from_env
    commit_file = root / COMMIT_FILE
    if commit_file.is_file():
        return commit_file.read_text().strip() or None
    return None


def provenance(training_set: Path) -> dict:
    """What a result has to be traced back to: the code, the run, the data and the seed."""
    return {
        "git_commit": git_commit(),
        "run_id": os.environ.get(RUN_ID_ENV, "").strip() or None,
        "training_set_sha256": hashlib.sha256(training_set.read_bytes()).hexdigest(),
        "random_state": RANDOM_STATE,
        "scikit_learn": sklearn.__version__,
    }


def subset(data: TrainingSet, rows: np.ndarray) -> TrainingSet:
    """`data` restricted to `rows` (a boolean mask or an index array)."""
    index = np.flatnonzero(rows) if rows.dtype == bool else rows
    return TrainingSet(
        features=data.features,
        X=data.X[index],
        y=data.y[index],
        years=data.years[index],
        months=data.months[index],
        counties=data.counties[index],
        dates=[data.dates[i] for i in index],
        meta=data.meta,
    )


@dataclass
class Selection:
    """What cross-validation on one set of years decided, and the evidence."""

    folds: list
    pipelines: dict  # per candidate, unfitted, with the settings that were kept
    tuning: dict  # per candidate, the fixed and searched settings and their scores
    held_out: dict[str, np.ndarray]  # per candidate, each row scored unseen
    season: np.ndarray  # the same for the season-only baseline
    comparison: dict
    best: str
    calibration_scores: dict
    calibration: str
    final_held_out: np.ndarray  # the winner's held-out probabilities, calibrated
    seconds: float = 0.0  # how long the comparison took, settings search included


def select(data: TrainingSet, tune_models: bool = True) -> Selection:
    """Compare the candidates on held-out years and pick a model and a calibration.

    With `tune_models`, each candidate's settings are first searched over
    PARAM_GRIDS. The searched settings replace the fixed ones only where
    they score a higher average precision on the same held-out years.
    """
    started = time.perf_counter()
    y = data.y
    folds = list(GroupKFold(CV_FOLDS).split(data.X, y, groups=data.years))
    all_columns = list(range(len(data.features)))
    searched = tune(data, folds) if tune_models else {}

    def unseen(pipeline):
        return held_out_probabilities(data, lambda: clone(pipeline), all_columns, folds)

    pipelines = {}
    tuning = {}
    comparison = {}
    held_out = {}
    for name, pipeline in candidates().items():
        pipelines[name] = pipeline
        held_out[name] = unseen(pipeline)
        comparison[name] = _scores(y, held_out[name])
        if name not in searched:
            continue
        current = pipeline.get_params()
        tuning[name] = {
            "grid": _short(PARAM_GRIDS[name]),
            "fixed_settings": _short({key: current[key] for key in searched[name]}),
            "searched_settings": _short(searched[name]),
            "fixed": comparison[name],
            "searched": comparison[name],
            "adopted": False,
        }
        if all(current[key] == value for key, value in searched[name].items()):
            continue  # the search chose the fixed settings
        tuned = clone(pipeline).set_params(**searched[name])
        tuned_held_out = unseen(tuned)
        tuning[name]["searched"] = _scores(y, tuned_held_out)
        if tuning[name]["searched"]["average_precision"] > comparison[name]["average_precision"]:
            tuning[name]["adopted"] = True
            pipelines[name] = tuned
            held_out[name] = tuned_held_out
            comparison[name] = tuning[name]["searched"]

    season_columns = [data.column(name) for name in SEASON_FEATURES if name in data.features]
    season = held_out_probabilities(data, season_baseline, season_columns, folds)

    best = max(comparison, key=lambda name: comparison[name]["average_precision"])

    # Calibrate only if it lowers the held-out Brier score.
    calibration_scores = {"none": comparison[best]}
    calibrated_held_out = {"none": held_out[best]}
    for method in METHODS:
        calibrated_held_out[method] = cross_calibrated(y, held_out[best], folds, method)
        calibration_scores[method] = _scores(y, calibrated_held_out[method])
    calibration = min(calibration_scores, key=lambda m: calibration_scores[m]["brier"])

    return Selection(
        folds=folds,
        pipelines=pipelines,
        tuning=tuning,
        held_out=held_out,
        season=season,
        comparison=comparison,
        best=best,
        calibration_scores=calibration_scores,
        calibration=calibration,
        final_held_out=calibrated_held_out[calibration],
        seconds=time.perf_counter() - started,
    )


def fit(data: TrainingSet, chosen: Selection):
    """The chosen model fitted on every row of `data`.

    Returns (model, pipeline, normals). `model` is `pipeline` with the chosen
    calibration applied, or `pipeline` itself when none was kept.
    """
    normals = county_normals(data, np.arange(len(data.y)))
    pipeline = clone(chosen.pipelines[chosen.best]).fit(with_normals(data, normals), data.y)
    model = pipeline
    if chosen.calibration != "none":
        calibrator = Calibrator(chosen.calibration).fit(chosen.held_out[chosen.best], data.y)
        model = CalibratedModel(pipeline, calibrator)
    return model, pipeline, normals


def final_test(data: TrainingSet, test_from: int, tune_models: bool = True) -> dict | None:
    """Score years from `test_from` on with a model that never saw them.

    The cross-validation in `train` both picks the model and reports its
    score, so that score is a little flattering. Here the later years are
    set aside first. The model and its calibration are chosen again on the
    earlier years alone (settings search included), fitted on them, and then
    scored once on the later years. Nothing about the later years (their floods, or their rainfall
    in the county normals) reaches the model.

    None when either side of `test_from` has no rows or one class only.
    """
    is_test = data.years >= test_from
    y_test = data.y[is_test]
    if not is_test.any() or is_test.all() or not 0 < y_test.sum() < len(y_test):
        return None
    development = subset(data, ~is_test)
    if not 0 < development.y.sum() < len(development.y):
        return None

    chosen = select(development, tune_models)
    model, _pipeline, normals = fit(development, chosen)

    # Every row measured against the development years' normals.
    X = with_normals(data, normals)
    X_development, X_test = X[~is_test], X[is_test]
    probabilities = model.predict_proba(X_test)[:, 1]

    # The candidates that were not chosen, for the record. They play no part
    # in the choice above.
    others = {}
    for name, pipeline in chosen.pipelines.items():
        if name != chosen.best:
            fitted = clone(pipeline).fit(X_development, development.y)
            others[name] = _scores(y_test, fitted.predict_proba(X_test)[:, 1])

    season_columns = [data.column(name) for name in SEASON_FEATURES if name in data.features]
    season = season_baseline().fit(X_development[:, season_columns], development.y)
    development_rate = float(development.y.mean())
    test_rate = float(y_test.mean())

    scores = _scores(y_test, probabilities)
    season_scores = _scores(y_test, season.predict_proba(X_test[:, season_columns])[:, 1])
    return {
        "test_from": test_from,
        "development_years": [int(development.years.min()), int(development.years.max())],
        "test_years": [int(data.years[is_test].min()), int(data.years[is_test].max())],
        "development_rows": len(development.y),
        "rows": int(is_test.sum()),
        "positives": int(y_test.sum()),
        "base_rate": round(test_rate, 4),
        "development_comparison": chosen.comparison,
        "tuning": chosen.tuning,
        "selected": chosen.best,
        "calibration": chosen.calibration,
        "scores": scores,
        "other_candidates": others,
        "baselines": {
            "base-rate": {
                "roc_auc": 0.5,
                "average_precision": round(test_rate, 4),
                # The rate known before the test years, not their own.
                "brier": round(
                    float(brier_score_loss(y_test, np.full(len(y_test), development_rate))), 4
                ),
            },
            "season-only": season_scores,
        },
        "lift_over_base_rate": round(scores["average_precision"] / test_rate, 2),
        "lift_over_season_only": round(
            scores["average_precision"] / season_scores["average_precision"], 2
        ),
        "band_calibration": _band_calibration(y_test, probabilities),
        "threshold_errors": _threshold_errors(y_test, probabilities),
    }


def train(
    training_set: Path,
    oni: dict[tuple[int, int], float],
    dmi: dict[tuple[int, int], float] | None = None,
    first_year: int = FIRST_TRAINING_YEAR,
    test_from: int | None = FINAL_TEST_FROM,
    tune_models: bool = True,
    seeds=SEEDS,
) -> tuple[dict, dict]:
    """Returns (artifact, metrics). Neither is written here."""
    data = load_training_set(training_set, first_year)
    chosen = select(data, tune_models)
    return build(data, chosen, training_set, oni, dmi, first_year, test_from, tune_models, seeds)


def build(
    data: TrainingSet,
    chosen: Selection,
    training_set: Path,
    oni: dict[tuple[int, int], float],
    dmi: dict[tuple[int, int], float] | None = None,
    first_year: int = FIRST_TRAINING_YEAR,
    test_from: int | None = FINAL_TEST_FROM,
    tune_models: bool = True,
    seeds=SEEDS,
) -> tuple[dict, dict]:
    """The artifact and metrics for a selection `select` already made on `data`.

    Split from `train` so a caller that wants the held-out probabilities
    (the notebook, for its curves) can keep `chosen` without selecting twice.
    `seeds` are the seeds the cross-validation is repeated with; empty skips
    the repeat.
    """
    y = data.y
    best, calibration = chosen.best, chosen.calibration
    comparison, season = chosen.comparison, chosen.season
    fold_scores = _fold_scores(
        y, data.years, chosen.folds, {**chosen.held_out, "season-only": season}
    )

    # The deployed model: normals from every year kept, fitted on every row.
    started = time.perf_counter()
    model, pipeline, normals = fit(data, chosen)
    seconds = {"cross_validation": chosen.seconds, "final_fit": time.perf_counter() - started}

    def timed(stage: str, work):
        started = time.perf_counter()
        result = work()
        seconds[stage] = time.perf_counter() - started
        return result

    base_rate = float(y.mean())
    metrics = {
        "target": "DFO large flood recorded in the county within the 30-day window",
        "rows": len(y),
        "positives": int(y.sum()),
        "base_rate": round(base_rate, 4),
        "period": [min(data.dates), max(data.dates)],
        "first_training_year": first_year,
        "features": list(data.features),
        "rain_source": data.meta.get("rain_source", "open-meteo-point"),
        "validation": f"{CV_FOLDS}-fold cross-validation grouped by year, normals refit per fold",
        "baseline_brier": round(float(brier_score_loss(y, np.full(len(y), base_rate))), 4),
        "baselines": {
            "base-rate": {
                "roc_auc": 0.5,
                "average_precision": round(base_rate, 4),
                "brier": round(float(brier_score_loss(y, np.full(len(y), base_rate))), 4),
            },
            "season-only": _scores(y, season),
        },
        "comparison": comparison,
        "tuning": chosen.tuning,
        "selected": best,
        "lift_over_base_rate": round(comparison[best]["average_precision"] / base_rate, 2),
        "lift_over_season_only": round(
            comparison[best]["average_precision"] / _scores(y, season)["average_precision"], 2
        ),
        "calibration": calibration,
        "calibration_comparison": chosen.calibration_scores,
        "probability_bands": list(PROBABILITY_BANDS),
        "band_calibration": _band_calibration(y, chosen.final_held_out),
        "folds": fold_scores,
        "fold_summary": _fold_summary(fold_scores),
        "threshold_errors": _threshold_errors(y, chosen.final_held_out),
        "error_breakdown": _error_breakdown(data, chosen.final_held_out),
        "permutation_importance": timed(
            "permutation_importance",
            lambda: permutation_importances(
                data, lambda: clone(chosen.pipelines[best]), chosen.folds
            ),
        ),
        "seed_variation": timed(
            "seed_variation",
            lambda: seed_variation(data, chosen.pipelines, chosen.folds, seeds) if seeds else None,
        ),
        "learning_curve": timed(
            "learning_curve",
            lambda: learning_curve(data, lambda: clone(chosen.pipelines[best]), chosen.folds),
        ),
        "final_test": timed(
            "final_test", lambda: final_test(data, test_from, tune_models) if test_from else None
        ),
        "provenance": provenance(training_set),
    }
    # What the run cost. Wall-clock seconds, so they differ from run to run.
    metrics["compute"] = {
        **{f"seconds_{stage}": round(spent, 1) for stage, spent in seconds.items()},
        "cpu": _processor(),
        "cores": os.cpu_count(),
    }
    if best == "logreg":
        metrics["standardised_coefficients"] = {
            name: round(float(value), 3)
            for name, value in zip(data.features, pipeline[-1].coef_[0])
        }

    artifact = {
        "version": f"flood-{best}-{dt.date.today():%Y%m%d}",
        "features": list(data.features),
        "pipeline": model,
        "probability_bands": list(PROBABILITY_BANDS),
        "normals": normals,
        # Indices as of training, so scoring needs no network. A date past
        # the end of a table gets its last value carried forward.
        "oni": oni,
        "dmi": dmi or {},
        # How rainfall was measured for training. Scoring has to measure it
        # the same way: one point, or an average over several (see
        # geodata/services/sampling.py).
        "rain_source": metrics["rain_source"],
        "rain_points": int(data.meta.get("rain_points", 1)),
    }
    return artifact, metrics


def save(artifact: dict, metrics: dict) -> None:
    """Write the model and its metrics where `infer()` and the docs expect them."""
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, ARTIFACT_PATH)
    METRICS_PATH.write_text(json.dumps({"version": artifact["version"], **metrics}, indent=2) + "\n")
