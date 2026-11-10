"""Why the model gave one region its score: an exact split of the prediction.

A Random Forest's probability is the mean of its trees' leaf values. Each
tree reaches its leaf by a path of splits, and every split moves the value
from the parent node's to the child's. Charging each move to the input the
split tested gives, for one prediction,

    probability = bias + sum of one contribution per input

exactly, where `bias` is the forest's value before any split (close to the
share of flood months in the training data). This is the tree-path method
of Saabas (2014), the basis of the `treeinterpreter` package; Palczewska et
al. (2014) describe the same decomposition as "feature contributions". It
needs nothing beyond the fitted trees, so no new dependency.

What it is and is not:

  - It is exact for this model and this prediction: the parts add up to the
    score, which `test_contributions_add_up_to_the_prediction` checks.
  - It says how the model *used* each input here, not that the input caused
    a flood risk in the world. A model that learned that big counties record
    more floods charges part of a big county's score to its size.
  - Contributions of correlated inputs (the four rain inputs, say) are
    shared out by the order the trees happened to split on them, so the
    groups below are more reliable than any single input in them.

Inputs are reported in groups a reader recognises, each the sum of the
inputs in it (FACTORS).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Reader-facing groups of model inputs, in the order a forecaster would talk
# about them. An input the model was not trained on is skipped.
FACTORS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("rain", "Rain in the last 30 days", ("rain_30d", "rain_7d_max", "rain_30d_ratio")),
    ("ground", "Rain in the month before (how wet the ground was)", ("rain_prev_30d",)),
    ("enso", "El Niño / La Niña (Pacific Ocean)", ("oni", "oni_short_rains")),
    ("iod", "Indian Ocean Dipole", ("dmi", "dmi_short_rains")),
    ("season", "Time of year", ("month_sin", "month_cos")),
    ("terrain", "Elevation and slope", ("elevation", "slope")),
    ("area", "County size (how floods were recorded)", ("log_area",)),
)


@dataclass
class Explanation:
    bias: float
    probability: float
    # Input name -> contribution, in probability.
    contributions: dict[str, float]


def _parts(pipeline):
    """(the steps before the forest or None, the forest)."""
    forest = pipeline.steps[-1][1]
    if not hasattr(forest, "estimators_"):
        raise TypeError(f"Expected a fitted tree ensemble, got {type(forest).__name__}")
    return (pipeline[:-1] if len(pipeline.steps) > 1 else None), forest


def _tree_path(tree, x: np.ndarray, positive: int) -> tuple[float, np.ndarray]:
    """(root value, contribution per input) of one tree for one row."""
    t = tree.tree_
    values = t.value[:, 0, :]
    # Normalised per node, so it holds whether the tree stores counts or
    # fractions (scikit-learn changed this in 1.4).
    share = values[:, positive] / values.sum(axis=1)
    contributions = np.zeros(t.n_features)
    node = 0
    while t.children_left[node] != -1:
        feature = t.feature[node]
        child = t.children_left[node] if x[feature] <= t.threshold[node] else t.children_right[node]
        contributions[feature] += share[child] - share[node]
        node = child
    return float(share[0]), contributions


def explain_row(model: dict, row: list[float]) -> Explanation:
    """Split `model`'s flood probability for one input row into its parts."""
    pipeline = model["pipeline"]
    prepare, forest = _parts(pipeline)
    x = np.array([row], dtype=float)
    x = (prepare.transform(x) if prepare is not None else x)[0]
    positive = list(forest.classes_).index(1)

    bias = 0.0
    total = np.zeros(len(x))
    for tree in forest.estimators_:
        root, contributions = _tree_path(tree, x, positive)
        bias += root
        total += contributions
    n = len(forest.estimators_)
    names = list(model.get("features") or [f"x{i}" for i in range(len(x))])
    contributions = {name: float(value / n) for name, value in zip(names, total)}
    return Explanation(
        bias=bias / n,
        probability=bias / n + sum(contributions.values()),
        contributions=contributions,
    )


def grouped(explanation: Explanation) -> list[dict]:
    """The contributions summed into FACTORS, largest effect first."""
    factors = []
    for key, label, inputs in FACTORS:
        present = [name for name in inputs if name in explanation.contributions]
        if not present:
            continue
        factors.append(
            {
                "key": key,
                "label": label,
                "contribution": sum(explanation.contributions[name] for name in present),
                "inputs": {name: explanation.contributions[name] for name in present},
            }
        )
    factors.sort(key=lambda factor: abs(factor["contribution"]), reverse=True)
    return factors
