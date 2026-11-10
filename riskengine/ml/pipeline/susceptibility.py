"""A susceptibility run: SUSCEPTIBILITY_PROTOCOL.md, from the sample to a decision.

The hazard run wraps train.py. This one reads everything from its config.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tomllib
from pathlib import Path

import joblib
import sklearn

from riskengine.ml import train as hazard_train
from riskengine.ml.susceptibility import evaluate, train

from . import hazard, runs
from .config import ConfigError, RunConfig

MODEL_FILE = "susceptibility_model.joblib"


def check(config: RunConfig, root: Path | None = None) -> Path:
    """Everything that can be checked before training. Returns the sample's path."""
    if config.model != "susceptibility":
        raise ConfigError(
            f"{config.path.name} is a {config.model} config, not a susceptibility one."
        )
    return hazard.check_training_set(config, root)


def run(
    config: RunConfig, run_id: str, log=print, checkpoints: Path | None = None
) -> tuple[object, dict]:
    path = check(config)
    with open(config.path, "rb") as handle:
        settings = tomllib.load(handle)
    layer, metrics = train.run(evaluate.load_sample(path), settings, log, checkpoints)
    metrics["provenance"] = {
        "git_commit": hazard_train.git_commit(),
        "run_id": run_id,
        "training_set_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "protocol_version": settings["protocol_version"],
        "scikit_learn": sklearn.__version__,
    }
    return layer, metrics


def summary(run_id: str, config: RunConfig, metrics: dict) -> str:
    best = metrics["best_baseline"]
    return "\n".join(
        [
            f"# Run {run_id}",
            "",
            f"- Config: `{config.path.name}`",
            f"- Commit: `{metrics['provenance']['git_commit']}`",
            f"- Sample SHA-256: `{metrics['provenance']['training_set_sha256']}`",
            f"- Rows: {metrics['rows']}, events: {metrics['events']}",
            f"- Selected: {metrics['selected']}",
            f"- Within-county ROC AUC, leave one event out: "
            f"{metrics['event_cv']['within_county_roc_auc']}",
            f"- The same, spatial blocks: {metrics['block_cv']['within_county_roc_auc']}",
            f"- Best baseline, {best}: {metrics['baselines'][best]['within_county_roc_auc']}",
            f"- Signs as expected: {metrics['signs_as_expected']}",
            f"- Decision: ship the {metrics['decision']['ship']}",
            "",
            "The UNOSAT test was not scored in this run.",
            "",
        ]
    )


def write(
    run_id: str, config: RunConfig, layer, metrics: dict, runs_dir: Path = runs.RUNS_DIR
) -> Path:
    folder = runs_dir / run_id
    folder.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {"model": layer, "ship": metrics["decision"]["ship"], "baseline": metrics["best_baseline"]},
        folder / MODEL_FILE,
    )
    (folder / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(config.path, folder / "config.toml")
    (folder / "summary.md").write_text(summary(run_id, config, metrics), encoding="utf-8")
    return folder


def ledger_line(run_id: str, config: RunConfig, metrics: dict, day: str) -> str:
    score = metrics["event_cv"]["within_county_roc_auc"]
    return (
        f"| {run_id} | {day} | susceptibility | `{config.path.name}` | {metrics['selected']} | "
        f"within_county_roc_auc {score} | ship {metrics['decision']['ship']} |"
    )
