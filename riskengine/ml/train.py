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
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.base import clone
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from riskengine.ml.calibration import METHODS, CalibratedModel, Calibrator
from riskengine.ml.features import FEATURE_NAMES, SHORT_RAINS_MONTHS

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_PATH = ARTIFACT_DIR / "flood_model.joblib"
METRICS_PATH = ARTIFACT_DIR / "metrics.json"

CV_FOLDS = 6
RANDOM_STATE = 0

# See the module docstring. Rows before this year are dropped.
FIRST_TRAINING_YEAR = 1996

# The inputs of the season-only baseline.
SEASON_FEATURES = ("month_sin", "month_cos")

# Upper bound of Low, Moderate and High as a flood probability; above the
# last is Very High. Recorded floods are rare (about 7% of county-months),
# so the bands sit around that base rate rather than at quarters of 0-1.
# `band_calibration` in metrics.json shows how often floods were actually
# recorded in each band on held-out years.
PROBABILITY_BANDS = (0.05, 0.10, 0.20)


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


def train(
    training_set: Path,
    oni: dict[tuple[int, int], float],
    dmi: dict[tuple[int, int], float] | None = None,
    first_year: int = FIRST_TRAINING_YEAR,
) -> tuple[dict, dict]:
    """Returns (artifact, metrics). Neither is written here."""
    data = load_training_set(training_set, first_year)
    y = data.y
    folds = list(GroupKFold(CV_FOLDS).split(data.X, y, groups=data.years))
    all_columns = list(range(len(data.features)))

    comparison = {}
    held_out = {}
    for name, pipeline in candidates().items():
        held_out[name] = held_out_probabilities(
            data, lambda pipeline=pipeline: clone(pipeline), all_columns, folds
        )
        comparison[name] = _scores(y, held_out[name])

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
    final_held_out = calibrated_held_out[calibration]

    # The deployed model: normals from every year kept, fitted on every row.
    all_rows = np.arange(len(y))
    normals = county_normals(data, all_rows)
    pipeline = candidates()[best].fit(with_normals(data, normals), y)
    model = pipeline
    if calibration != "none":
        model = CalibratedModel(pipeline, Calibrator(calibration).fit(held_out[best], y))

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
        "selected": best,
        "lift_over_base_rate": round(comparison[best]["average_precision"] / base_rate, 2),
        "lift_over_season_only": round(
            comparison[best]["average_precision"] / _scores(y, season)["average_precision"], 2
        ),
        "calibration": calibration,
        "calibration_comparison": calibration_scores,
        "probability_bands": list(PROBABILITY_BANDS),
        "band_calibration": _band_calibration(y, final_held_out),
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
