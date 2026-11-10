"""The hazard model through the pipeline: train.py's procedure, run from a config.

The procedure itself (folds of whole years, the settings search, the
calibration choice) stays in train.py. This module checks that the config
describes that procedure exactly, checks the training set against its
SHA-256, and runs it with the final test closed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from riskengine.ml import train

from .config import ConfigError, RunConfig

# The only value [cv] final_test may take. The 2017 to 2021 test was closed
# on 29 October 2026 after about seven looks (riskengine/ml/experiments.md).
CLOSED = "closed"


@dataclass
class HazardRun:
    data: train.TrainingSet
    chosen: train.Selection
    artifact: dict
    metrics: dict
    training_set: Path


def differences(config: RunConfig) -> list[str]:
    """Where the config and the code in train.py disagree. Empty when they match."""
    found = []

    def compare(label, in_config, in_code):
        if in_config != in_code:
            found.append(f"{label}: config has {in_config!r}, train.py has {in_code!r}")

    compare("[cv] folds", config.value("cv", "folds"), train.CV_FOLDS)
    compare(
        "[selection] random_state", config.value("selection", "random_state"), train.RANDOM_STATE
    )
    compare(
        "[selection] probability_bands",
        tuple(config.value("selection", "probability_bands")),
        train.PROBABILITY_BANDS,
    )
    compare("[selection.grids]", config.value("selection", "grids"), train.PARAM_GRIDS)
    if config.value("cv", "final_test") != CLOSED:
        found.append(
            f'[cv] final_test must be "{CLOSED}": the 2017 to 2021 test is closed and is '
            "never scored again"
        )
    return found


def training_set_path(config: RunConfig, root: Path | None = None) -> Path:
    """The config's training set, relative to `root` (the repository by default)."""
    return (root or train.REPO_ROOT) / config.value("data", "training_set")


def check(config: RunConfig, root: Path | None = None) -> Path:
    """Everything that can be checked before training. Returns the training set's path."""
    if config.model != "hazard":
        raise ConfigError(f"{config.path.name} is a {config.model} config, not a hazard one.")
    problems = differences(config)
    if problems:
        raise ConfigError(
            f"{config.path.name} does not describe what train.py does:\n  " + "\n  ".join(problems)
        )
    return check_training_set(config, root)


def check_training_set(config: RunConfig, root: Path | None = None) -> Path:
    """The config's training set, once its file and SHA-256 are confirmed."""
    path = training_set_path(config, root)
    if not path.is_file():
        raise ConfigError(f"Training set {path} not found.")
    expected = config.value("data", "sha256")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ConfigError(f"{path.name} has SHA-256 {actual}, but the config records {expected}.")
    return path


def run(
    config: RunConfig,
    oni: dict[tuple[int, int], float],
    dmi: dict[tuple[int, int], float] | None,
    root: Path | None = None,
) -> HazardRun:
    """Train as the config says. Nothing is written here; see runs.py.

    `oni` and `dmi` are only the lookup tables saved with the model for
    scoring. The training set already holds the indices it trains on.
    """
    path = check(config, root)
    first_year = config.value("data", "first_year")
    data = train.load_training_set(path, first_year)
    features = tuple(config.value("data", "features"))
    if data.features != features:
        raise ConfigError(
            f"{path.name} gives the inputs {list(data.features)}, but the config lists "
            f"{list(features)}."
        )
    tune = config.value("selection", "tune")
    chosen = train.select(data, tune)
    artifact, metrics = train.build(
        data,
        chosen,
        path,
        oni,
        dmi,
        first_year=first_year,
        test_from=None,
        tune_models=tune,
        seeds=tuple(config.value("selection", "seeds")),
    )
    metrics["config"] = config.name
    return HazardRun(
        data=data, chosen=chosen, artifact=artifact, metrics=metrics, training_set=path
    )


def primary_score(config: RunConfig, metrics: dict) -> float:
    """The selected model's primary metric on held-out years."""
    return metrics["comparison"][metrics["selected"]][config.value("promotion", "primary_metric")]
