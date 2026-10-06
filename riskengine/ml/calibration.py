"""Probability calibration on top of a fitted model.

A model can rank county-months well and still say 31% where 21% flooded,
which is what the first model did in its Very High band. Calibration is a
one-dimensional map from the model's probability to the rate actually
observed at that probability, learned from predictions on held-out years.

Two maps are offered and `train.py` keeps whichever lowers the held-out
Brier score, or neither:

  sigmoid   Platt scaling: a logistic regression on the log-odds. Smooth,
            two parameters, cannot overfit much.
  isotonic  Any non-decreasing step function. Fixes curves the sigmoid
            cannot, but needs more data and can overfit sparse bands.

Both only ever reshape the probabilities. Neither can change which county
ranks above which, so ROC AUC is unaffected and average precision almost so.
"""

from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

# Keeps a calibrated probability off exactly 0 and 1. A region is never
# certain to flood or certain not to, and log-odds need finite inputs.
EPSILON = 1e-3

METHODS = ("sigmoid", "isotonic")


def _logit(probabilities) -> np.ndarray:
    p = np.clip(np.asarray(probabilities, dtype=float), EPSILON, 1 - EPSILON)
    return np.log(p / (1 - p)).reshape(-1, 1)


class Calibrator:
    """Maps raw probabilities to calibrated ones."""

    def __init__(self, method: str):
        if method not in METHODS:
            raise ValueError(f"Unknown calibration method {method!r}")
        self.method = method
        self._map = None

    def fit(self, probabilities, y) -> "Calibrator":
        if self.method == "sigmoid":
            self._map = LogisticRegression().fit(_logit(probabilities), y)
        else:
            self._map = IsotonicRegression(
                y_min=EPSILON, y_max=1 - EPSILON, out_of_bounds="clip"
            ).fit(np.asarray(probabilities, dtype=float), y)
        return self

    def predict(self, probabilities) -> np.ndarray:
        if self.method == "sigmoid":
            out = self._map.predict_proba(_logit(probabilities))[:, 1]
        else:
            out = self._map.predict(np.asarray(probabilities, dtype=float))
        return np.clip(out, EPSILON, 1 - EPSILON)


class CalibratedModel:
    """A fitted pipeline with a calibrator applied to its output.

    Exposes `predict_proba` like any scikit-learn classifier, so `infer()`
    and the outlook use it without knowing it is calibrated. Indexing passes
    through to the pipeline, so `model[-1]` is still the estimator.
    """

    def __init__(self, pipeline, calibrator: Calibrator):
        self.pipeline = pipeline
        self.calibrator = calibrator

    def predict_proba(self, X) -> np.ndarray:
        calibrated = self.calibrator.predict(self.pipeline.predict_proba(X)[:, 1])
        return np.column_stack([1 - calibrated, calibrated])

    def __getitem__(self, index):
        return self.pipeline[index]
