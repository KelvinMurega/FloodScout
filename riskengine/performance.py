"""How well the model in use did when it was evaluated, for the dashboard.

The administrator's analytics page shows these. Everything is read from
`metrics.json`, the file the training run wrote beside the model, so the
page cannot say anything the evaluation did not. Nothing is worked out
again here except the order of the rows.

The figures are the model's record on years it was not trained on. They are
not a measure of any score on the map: riskengine/ml/README.md says how to
read them and where the model is weak.
"""

from __future__ import annotations

import json
from pathlib import Path

from riskengine.ml.infer import METRICS_PATH

MODEL_LABELS = {
    "random-forest": "Random Forest",
    "logreg": "Logistic Regression",
    "gradient-boosting": "Gradient Boosting",
    "season-only": "Season only",
    "base-rate": "Always the usual chance",
}

# The band a warning starts at, for each threshold in turn: a probability
# above the first band edge is Moderate or higher, and so on.
WARNING_FROM = ("moderate", "high", "very_high")


def _comparison(candidates: dict, baselines: dict, selected: str) -> list[dict]:
    """One row per model, best average precision first, then the baselines."""
    rows = [
        {"key": key, "label": MODEL_LABELS.get(key, key), "baseline": False, **scores}
        for key, scores in candidates.items()
    ]
    rows.sort(key=lambda row: -row["average_precision"])
    rows += sorted(
        (
            {"key": key, "label": MODEL_LABELS.get(key, key), "baseline": True, **scores}
            for key, scores in baselines.items()
        ),
        key=lambda row: -row["average_precision"],
    )
    for row in rows:
        row["selected"] = row["key"] == selected
    return rows


def _warnings(threshold_errors: list[dict]) -> list[dict]:
    return [
        {
            "threshold": row["threshold"],
            "from_band": band,
            "caught": row["true_positives"],
            "missed": row["false_negatives"],
            "false_alarms": row["false_positives"],
            "recall": row["recall"],
            "precision": row["precision"],
            "f1": row["f1"],
            "accuracy": row["accuracy"],
            "balanced_accuracy": row["balanced_accuracy"],
        }
        for row, band in zip(threshold_errors, WARNING_FROM)
    ]


def _final_test(test: dict) -> dict:
    """The years set aside: the model the earlier years chose, and the others."""
    selected = test["selected"]
    candidates = {selected: test["scores"], **test["other_candidates"]}
    return {
        "years": test["test_years"],
        "chosen_on": test["development_years"],
        "rows": test["rows"],
        "floods": test["positives"],
        "base_rate": test["base_rate"],
        "selected": selected,
        "selected_label": MODEL_LABELS.get(selected, selected),
        "comparison": _comparison(candidates, test["baselines"], selected),
        "lift_over_base_rate": test["lift_over_base_rate"],
        "lift_over_season_only": test["lift_over_season_only"],
        "bands": test.get("band_calibration", []),
        "calibration": test.get("calibration"),
    }


def _folds(metrics: dict) -> dict:
    """Each model's score on each held-out group of years, and the spread."""
    folds = metrics.get("folds", [])
    keys = list(metrics["comparison"]) + [
        key for key in (folds[0]["scores"] if folds else {}) if key not in metrics["comparison"]
    ]
    return {
        "models": [{"key": key, "label": MODEL_LABELS.get(key, key)} for key in keys],
        "folds": [
            {
                "years": fold["years"],
                "rows": fold["rows"],
                "floods": fold["positives"],
                "scores": fold["scores"],
            }
            for fold in folds
        ],
        "summary": metrics.get("fold_summary", {}),
    }


def _importance(metrics: dict) -> list[dict]:
    """Permutation importance, the input that mattered most first.

    A negative mean means shuffling the input helped: the model is no worse
    off without it.
    """
    rows = [
        {"feature": feature, "mean": value["mean"], "sd": value["sd"]}
        for feature, value in metrics.get("permutation_importance", {}).items()
    ]
    rows.sort(key=lambda row: -row["mean"])
    return rows


def _seed_variation(metrics: dict) -> list[dict]:
    """How much a model's score moves when only its random seed changes."""
    variation = metrics.get("seed_variation", {})
    return [
        {
            "key": key,
            "label": MODEL_LABELS.get(key, key),
            "average_precision": value["average_precision"],
            "roc_auc": value["roc_auc"],
        }
        for key, value in variation.items()
        if isinstance(value, dict) and "average_precision" in value
    ]


def _tuning(metrics: dict) -> list[dict]:
    return [
        {
            "key": key,
            "label": MODEL_LABELS.get(key, key),
            "grid": value["grid"],
            "fixed_settings": value["fixed_settings"],
            "searched_settings": value["searched_settings"],
            "fixed": value["fixed"],
            "searched": value["searched"],
            "adopted": value["adopted"],
        }
        for key, value in metrics.get("tuning", {}).items()
    ]


def _calibration_comparison(metrics: dict) -> list[dict]:
    return [
        {"method": method, **scores, "selected": method == metrics.get("calibration")}
        for method, scores in metrics.get("calibration_comparison", {}).items()
    ]


def model_performance(metrics: dict) -> dict:
    """`metrics.json` as the panel shows it."""
    selected = metrics["selected"]
    return {
        "model_version": metrics["version"],
        "selected": selected,
        "selected_label": MODEL_LABELS.get(selected, selected),
        "target": metrics["target"],
        "validation": metrics["validation"],
        "training": {
            "rows": metrics["rows"],
            "floods": metrics["positives"],
            "base_rate": metrics["base_rate"],
            "period": metrics["period"],
            "inputs": len(metrics["features"]),
        },
        "comparison": _comparison(metrics["comparison"], metrics["baselines"], selected),
        "lift_over_base_rate": metrics["lift_over_base_rate"],
        "lift_over_season_only": metrics["lift_over_season_only"],
        "warnings": _warnings(metrics["threshold_errors"]),
        "bands": metrics["band_calibration"],
        "final_test": _final_test(metrics["final_test"]),
        "run_id": metrics.get("provenance", {}).get("run_id"),
        "features": metrics["features"],
        "rain_source": metrics.get("rain_source"),
        "calibration": metrics.get("calibration"),
        "calibration_comparison": _calibration_comparison(metrics),
        "thresholds": metrics.get("threshold_errors", []),
        "cross_validation": _folds(metrics),
        "seed_variation": _seed_variation(metrics),
        "tuning": _tuning(metrics),
        "importance": _importance(metrics),
        "errors": metrics.get("error_breakdown", {}),
        "learning_curve": metrics.get("learning_curve", []),
        "provenance": metrics.get("provenance", {}),
        "compute": metrics.get("compute", {}),
    }


def load_performance(path: Path = METRICS_PATH) -> dict | None:
    """The panel's figures, or None when no model has been trained yet."""
    if not path.exists():
        return None
    return model_performance(json.loads(path.read_text()))
