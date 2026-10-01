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
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from riskengine.ml.features import FEATURE_NAMES

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_PATH = ARTIFACT_DIR / "flood_model.joblib"
METRICS_PATH = ARTIFACT_DIR / "metrics.json"

CV_FOLDS = 6
RANDOM_STATE = 0

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


def load_training_set(path: Path):
    """(rows as dicts, X, y, year per row) from the CSV `build_training_set` writes."""
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    X = np.array([[float(row[name]) for name in FEATURE_NAMES] for row in rows])
    y = np.array([int(row["flood"]) for row in rows])
    years = np.array([int(row["date"][:4]) for row in rows])
    return rows, X, y, years


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


def train(training_set: Path, oni: dict[tuple[int, int], float]) -> tuple[dict, dict]:
    """Returns (artifact, metrics). Neither is written here."""
    rows, X, y, years = load_training_set(training_set)

    comparison = {}
    held_out = {}
    for name, pipeline in candidates().items():
        held_out[name] = cross_val_predict(
            pipeline, X, y, groups=years, cv=GroupKFold(CV_FOLDS), method="predict_proba"
        )[:, 1]
        comparison[name] = _scores(y, held_out[name])

    best = max(comparison, key=lambda name: comparison[name]["average_precision"])
    pipeline = candidates()[best].fit(X, y)

    # What each county normally gets in each calendar month, so a row for
    # any later date can be put on the same scale as the training rows.
    normals: dict[str, dict[int, float]] = {}
    for row in rows:
        normals.setdefault(row["county"], {})[int(row["date"][5:7])] = float(row["normal_30d"])

    base_rate = float(y.mean())
    metrics = {
        "target": "DFO large flood recorded in the county within the 30-day window",
        "rows": len(rows),
        "positives": int(y.sum()),
        "base_rate": round(base_rate, 4),
        "period": [rows[0]["date"], max(row["date"] for row in rows)],
        "validation": f"{CV_FOLDS}-fold cross-validation grouped by year",
        "baseline_brier": round(float(brier_score_loss(y, np.full(len(y), base_rate))), 4),
        "comparison": comparison,
        "selected": best,
        "probability_bands": list(PROBABILITY_BANDS),
        "band_calibration": _band_calibration(y, held_out[best]),
    }
    if best == "logreg":
        metrics["standardised_coefficients"] = {
            name: round(float(value), 3) for name, value in zip(FEATURE_NAMES, pipeline[-1].coef_[0])
        }

    artifact = {
        "version": f"flood-{best}-{dt.date.today():%Y%m%d}",
        "features": list(FEATURE_NAMES),
        "pipeline": pipeline,
        "probability_bands": list(PROBABILITY_BANDS),
        "normals": normals,
        # ONI as of training, so scoring needs no network. A date past the
        # end of the table gets the last value carried forward.
        "oni": oni,
    }
    return artifact, metrics


def save(artifact: dict, metrics: dict) -> None:
    """Write the model and its metrics where `infer()` and the docs expect them."""
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, ARTIFACT_PATH)
    METRICS_PATH.write_text(json.dumps({"version": artifact["version"], **metrics}, indent=2) + "\n")
