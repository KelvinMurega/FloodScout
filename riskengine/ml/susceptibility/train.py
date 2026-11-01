"""Train and select the susceptibility model: SUSCEPTIBILITY_PROTOCOL.md, sections 3 to 7.

Runs on Colab through the pipeline. Nothing here reads the UNOSAT test.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
from sklearn.base import clone
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import partial_dependence
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold, LeaveOneGroupOut
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import evaluate

# Partial dependence must move this way (section 7, rule 3).
EXPECTED_SIGNS = {"hand": -1, "dist_river": -1, "twi": 1}
MAX_LOGREG_ITERATIONS = 2000
PERMUTATION_REPEATS = 5


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    settings: tuple


def candidates(grids: dict) -> list[Candidate]:
    """The eight configurations, in a fixed order."""
    found = []
    for family, grid in grids.items():
        keys = list(grid)
        for values in itertools.product(*(grid[key] for key in keys)):
            settings = tuple(zip(keys, values, strict=True))
            label = ",".join(f"{key.split('__')[-1]}={value}" for key, value in settings)
            found.append(Candidate(f"{family}[{label}]", family, settings))
    return found


def build(candidate: Candidate, seed: int):
    settings = dict(candidate.settings)
    if candidate.family == "logreg":
        pipeline = make_pipeline(
            StandardScaler(), LogisticRegression(max_iter=MAX_LOGREG_ITERATIONS)
        )
        return pipeline.set_params(**settings)
    return HistGradientBoostingClassifier(random_state=seed, **settings)


def matrix(data: dict, features: list[str]) -> np.ndarray:
    return np.column_stack([data[name] for name in features])


def _fit(model, inputs, labels, weight):
    if hasattr(model, "steps"):
        return model.fit(inputs, labels, logisticregression__sample_weight=weight)
    return model.fit(inputs, labels, sample_weight=weight)


def block_ids(data: dict, degrees: float) -> np.ndarray:
    """Which square of `degrees` each pixel falls in."""
    columns = np.floor(data["lon"] / degrees).astype(int)
    rows = np.floor(data["lat"] / degrees).astype(int)
    return np.char.add(columns.astype(str), np.char.add("_", rows.astype(str)))


def out_of_fold(model, data, features, splits) -> np.ndarray:
    """A prediction for every pixel from a model that never saw its fold."""
    inputs = matrix(data, features)
    predicted = np.full(len(inputs), np.nan)
    for train_rows, test_rows in splits:
        fitted = _fit(
            clone(model),
            inputs[train_rows],
            data["flooded"][train_rows],
            data["weight"][train_rows],
        )
        predicted[test_rows] = fitted.predict_proba(inputs[test_rows])[:, 1]
    return predicted


def seeds_for(candidate: Candidate, seeds: list[int]) -> list[int]:
    # Logistic regression gives the same fit from any seed.
    return seeds[:1] if candidate.family == "logreg" else seeds


def event_cv(data, features, candidate: Candidate, seeds: list[int]) -> tuple[dict, np.ndarray]:
    """Leave one event out, once per seed. Returns (scores, the first seed's predictions)."""
    inputs = matrix(data, features)
    splits = list(LeaveOneGroupOut().split(inputs, groups=data["event"]))
    predictions = {
        seed: out_of_fold(build(candidate, seed), data, features, splits)
        for seed in seeds_for(candidate, seeds)
    }
    scores = {
        str(seed): evaluate.summary(data, predicted)["within_county_roc_auc"]
        for seed, predicted in predictions.items()
    }
    values = list(scores.values())
    outcome = {
        "scores_by_seed": scores,
        "mean": round(float(np.mean(values)), 4),
        "sd": round(float(np.std(values)), 4),
    }
    return outcome, next(iter(predictions.values()))


def block_cv(data, features, model, block_degrees: float, folds: int) -> np.ndarray:
    inputs = matrix(data, features)
    splits = list(GroupKFold(n_splits=folds).split(inputs, groups=block_ids(data, block_degrees)))
    return out_of_fold(model, data, features, splits)


def permutation_importance(model, data, features, seed: int) -> dict:
    """The fall in the primary metric when one feature is shuffled."""
    generator = np.random.default_rng(seed)
    inputs = matrix(data, features)
    start = evaluate.summary(data, model.predict_proba(inputs)[:, 1])["within_county_roc_auc"]
    falls = {}
    for column, feature in enumerate(features):
        drops = []
        for _ in range(PERMUTATION_REPEATS):
            shuffled = inputs.copy()
            shuffled[:, column] = generator.permutation(shuffled[:, column])
            score = evaluate.summary(data, model.predict_proba(shuffled)[:, 1])
            drops.append(start - score["within_county_roc_auc"])
        falls[feature] = {
            "mean": round(float(np.mean(drops)), 4),
            "sd": round(float(np.std(drops)), 4),
        }
    return falls


def sign_check(model, data, features) -> dict:
    """Does partial dependence move the way physics says it should?"""
    inputs = matrix(data, features)
    result = {}
    for feature, expected in EXPECTED_SIGNS.items():
        curve = partial_dependence(
            model, inputs, [features.index(feature)], kind="average", percentiles=(0.05, 0.95)
        )
        values = curve["average"][0]
        moved = float(values[-1] - values[0])
        result[feature] = {
            "grid": [round(float(value), 4) for value in curve["grid_values"][0]],
            "average": [round(float(value), 6) for value in values],
            "moved": round(moved, 6),
            "as_expected": bool(np.sign(moved) == expected),
        }
    return result


def fit_baseline_layer(data: dict, name: str):
    """Section 7, rule 2: a one-variable logistic fit, which changes the scale only."""
    score = np.asarray(evaluate.BASELINES[name](data), dtype=float).reshape(-1, 1)
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=MAX_LOGREG_ITERATIONS))
    return _fit(model, score, data["flooded"], data["weight"])


def sensitivity(data: dict, event_predictions, block_predictions) -> dict:
    """The primary metric away from large lakes. It never enters the decision."""
    away, from_events = evaluate.without_lakes(data, event_predictions)
    _, from_blocks = evaluate.without_lakes(data, block_predictions)
    scores = {"model, event CV": from_events, "model, block CV": from_blocks}
    scores |= {name: score(away) for name, score in evaluate.BASELINES.items()}
    return {
        "rows": len(away["flooded"]),
        "within_county_roc_auc": {
            name: evaluate.summary(away, score)["within_county_roc_auc"]
            for name, score in scores.items()
        },
    }


def run(data: dict, settings: dict, log=print) -> tuple[object, dict]:
    """The whole protocol. Returns (the layer to ship, its metrics)."""
    features = settings["data"]["features"]
    seeds = settings["selection"]["seeds"]
    cv = settings["cv"]
    baselines = evaluate.score_baselines(data)
    best = evaluate.best_baseline(baselines)
    log(f"Best baseline: {best}, {baselines[best]['within_county_roc_auc']}")

    options = candidates(settings["selection"]["grids"])
    results, predictions = {}, {}
    for candidate in options:
        results[candidate.name], predictions[candidate.name] = event_cv(
            data, features, candidate, seeds
        )
        log(f"{candidate.name}: {results[candidate.name]['mean']}")

    selected = max(options, key=lambda candidate: results[candidate.name]["mean"])
    chosen = build(selected, seeds[0])
    in_blocks = block_cv(data, features, chosen, cv["block_degrees"], cv["block_folds"])
    model_event = evaluate.summary(data, predictions[selected.name])
    model_block = evaluate.summary(data, in_blocks)
    # A baseline has nothing fitted, so its score is the same under either split.
    decision = evaluate.decide(
        model_event["within_county_roc_auc"],
        model_block["within_county_roc_auc"],
        baselines[best]["within_county_roc_auc"],
        baselines[best]["within_county_roc_auc"],
        settings["promotion"]["margin"],
    )

    final = _fit(clone(chosen), matrix(data, features), data["flooded"], data["weight"])
    signs = sign_check(final, data, features)
    metrics = {
        "rows": len(data["flooded"]),
        "events": len(set(data["event"].tolist())),
        "baselines": baselines,
        "best_baseline": best,
        "candidates": results,
        "selected": selected.name,
        "event_cv": model_event,
        "block_cv": model_block,
        "calibration": evaluate.calibration(data, predictions[selected.name]),
        "permutation_importance": permutation_importance(final, data, features, seeds[0]),
        "partial_dependence": signs,
        "signs_as_expected": all(sign["as_expected"] for sign in signs.values()),
        "sensitivity_without_lakes": sensitivity(data, predictions[selected.name], in_blocks),
        "decision": decision,
    }
    shipped = final if decision["ship"] == "model" else fit_baseline_layer(data, best)
    return shipped, metrics
