"""Check the app's explanations against SHAP, on the shipped model.

The dashboard explains a score with `riskengine/ml/explain.py`: the
tree-path method of Saabas (2014), which charges each split's change in the
leaf value to the input the split tested. It is exact (the parts add up to
the score) but it has a known weakness: credit among correlated inputs (the
four rain inputs, say) depends on which one each tree happened to split on
first, and inputs split near the root get more than their share.

TreeSHAP (Lundberg et al., 2020) computes Shapley values for the same trees.
Its parts also add up to the score, and it is consistent: an input the model
leans on more never gets less credit. The proposal named SHAP. This command
runs both on the same predictions and reports where they agree.

Nothing is retrained. The model is the shipped artifact, checked against the
SHA-256 in metrics.json, and the rows are the final-test years (2017 on) of
the training set that run recorded (its SHA-256 is checked too).

Compared, for every row and for the high-risk rows (probability at or above
the top probability band, the ones a reader is most likely to ask about):

  - whether both methods put the same factor group first;
  - the rank correlation of the seven factor-group contributions;
  - the mean absolute difference, in probability, per group;
  - the same three at the level of the 13 single inputs, where correlated
    inputs are expected to disagree more;
  - the global ranking of factor groups by mean |contribution|, beside the
    permutation importance recorded at training.

Writes riskengine/ml/artifacts/shap_check.json and shap_summary.png.

Needs `shap` and `matplotlib`, which the app does not; install them from
requirements-analysis.txt in a throwaway container:

    docker compose run --rm --no-deps web sh -c \
      "pip install -q -r requirements-analysis.txt && python manage.py shap_check"
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from django.core.management.base import BaseCommand, CommandError
from scipy.stats import spearmanr

from riskengine.ml import explain
from riskengine.ml.infer import ARTIFACT_PATH, load_model
from riskengine.ml.train import FINAL_TEST_FROM, FIRST_TRAINING_YEAR, load_training_set

TRAINING_SET = Path("data/processed/training_set_point_20261002.csv")
OUT_DIR = ARTIFACT_PATH.parent


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _group_matrix(per_input: np.ndarray, features: list[str]) -> tuple[list[str], np.ndarray]:
    """Sum per-input contributions (rows x inputs) into explain.FACTORS."""
    keys, columns = [], []
    for key, _label, inputs in explain.FACTORS:
        index = [features.index(name) for name in inputs if name in features]
        if index:
            keys.append(key)
            columns.append(per_input[:, index].sum(axis=1))
    return keys, np.column_stack(columns)


def _agreement(a: np.ndarray, b: np.ndarray, names: list[str]) -> dict:
    """How closely two (rows x parts) contribution matrices agree, row by row."""
    top_a = np.abs(a).argmax(axis=1)
    top_b = np.abs(b).argmax(axis=1)
    rho = []
    for row_a, row_b in zip(a, b):
        if np.ptp(row_a) == 0 or np.ptp(row_b) == 0:
            continue
        rho.append(spearmanr(row_a, row_b).statistic)
    rho = np.array(rho)
    return {
        "rows": int(len(a)),
        "same_top_part": round(float((top_a == top_b).mean()), 4),
        "rank_correlation_median": round(float(np.median(rho)), 4),
        "rank_correlation_p10": round(float(np.percentile(rho, 10)), 4),
        "mean_abs_difference": {
            name: round(float(np.abs(a[:, i] - b[:, i]).mean()), 5) for i, name in enumerate(names)
        },
        "mean_abs_difference_all": round(float(np.abs(a - b).mean()), 5),
        "mean_abs_contribution_shap": round(float(np.abs(a).mean()), 5),
    }


def _ranking(matrix: np.ndarray, names: list[str]) -> list[dict]:
    means = np.abs(matrix).mean(axis=0)
    order = np.argsort(-means)
    return [{"part": names[i], "mean_abs": round(float(means[i]), 5)} for i in order]


class Command(BaseCommand):
    help = "Compare the app's tree-path explanations with SHAP on the shipped model."

    def add_arguments(self, parser):
        parser.add_argument("--training-set", type=Path, default=TRAINING_SET)
        parser.add_argument("--out-dir", type=Path, default=OUT_DIR)

    def handle(self, *args, **options):
        try:
            import shap
        except ImportError as exc:
            raise CommandError(
                "Install requirements-analysis.txt first (see the module docstring)."
            ) from exc

        metrics = json.loads((ARTIFACT_PATH.parent / "metrics.json").read_text())
        model = load_model()  # refuses a file whose SHA-256 is not the recorded one
        if model is None:
            raise CommandError(f"No model at {ARTIFACT_PATH}")
        recorded = metrics["provenance"]["training_set_sha256"]
        path = options["training_set"]
        if _sha256(path) != recorded:
            raise CommandError(
                f"{path} is not the training set the model recorded ({recorded[:12]})."
            )

        data = load_training_set(path, FIRST_TRAINING_YEAR)
        features = list(model["features"])
        if list(data.features) != features:
            raise CommandError(
                f"Training set inputs {data.features} differ from the model's {features}."
            )
        test = data.years >= FINAL_TEST_FROM
        X = data.X[test]

        pipeline = model["pipeline"]
        prepare, forest = explain._parts(pipeline)
        Xt = prepare.transform(X) if prepare is not None else X
        positive = list(forest.classes_).index(1)
        probability = forest.predict_proba(Xt)[:, positive]

        self.stdout.write(f"{len(X)} final-test rows ({FINAL_TEST_FROM} on); tree-path ...")
        saabas = np.array(
            [
                [e.contributions[name] for name in features]
                for e in (explain.explain_row(model, row) for row in X)
            ]
        )

        self.stdout.write("TreeSHAP ...")
        explainer = shap.TreeExplainer(forest)
        values = explainer.shap_values(Xt, check_additivity=True)
        values = np.asarray(values)
        if values.ndim == 3:  # (rows, inputs, classes) in current shap
            values = values[:, :, positive]
        base = float(np.ravel(explainer.expected_value)[positive])

        # Both must add up to the model's own probability.
        saabas_bias = explain.explain_row(model, X[0]).bias
        additivity = {
            "tree_path_max_error": float(
                np.abs(saabas_bias + saabas.sum(axis=1) - probability).max()
            ),
            "shap_max_error": float(np.abs(base + values.sum(axis=1) - probability).max()),
        }

        groups, shap_groups = _group_matrix(values, features)
        _, saabas_groups = _group_matrix(saabas, features)
        top_band = float(model["probability_bands"][-1])
        high = probability >= top_band

        permutation = metrics["permutation_importance"]
        perm_groups = {}
        for key, _label, inputs in explain.FACTORS:
            present = [name for name in inputs if name in permutation]
            if present:
                perm_groups[key] = round(sum(permutation[name]["mean"] for name in present), 5)

        result = {
            "model_version": model["version"],
            "model_sha256": metrics["model_sha256"],
            "training_set": str(path),
            "training_set_sha256": recorded,
            "rows": "final test, years %d to %d"
            % (int(data.years[test].min()), int(data.years[test].max())),
            "shap_version": shap.__version__,
            "shap_method": "TreeExplainer, tree_path_dependent, output = probability of a flood month",
            "base_value": {"shap": round(base, 5), "tree_path": round(float(saabas_bias), 5)},
            "additivity": additivity,
            "high_risk_threshold": top_band,
            "groups": {
                "all": _agreement(shap_groups, saabas_groups, groups),
                "high_risk": _agreement(shap_groups[high], saabas_groups[high], groups),
            },
            "inputs": {
                "all": _agreement(values, saabas, features),
                "high_risk": _agreement(values[high], saabas[high], features),
            },
            "global_ranking": {
                "shap": _ranking(shap_groups, groups),
                "tree_path": _ranking(saabas_groups, groups),
                "permutation_importance_at_training": sorted(
                    ({"part": k, "auc_drop": v} for k, v in perm_groups.items()),
                    key=lambda item: -item["auc_drop"],
                ),
            },
        }
        out = options["out_dir"]
        (out / "shap_check.json").write_text(json.dumps(result, indent=2) + "\n")

        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        shap.summary_plot(values, X, feature_names=features, show=False, max_display=len(features))
        figure = plt.gcf()
        figure.suptitle("SHAP values, final-test rows (%s)" % model["version"], fontsize=10)
        figure.savefig(out / "shap_summary.png", dpi=150, bbox_inches="tight")
        plt.close(figure)

        self.stdout.write(
            json.dumps({k: result[k] for k in ("additivity", "groups", "global_ranking")}, indent=2)
        )
