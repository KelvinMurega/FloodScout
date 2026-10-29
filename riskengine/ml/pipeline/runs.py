"""Where a run's results go: its own folder, and one line in the ledger.

    riskengine/ml/runs/<run_id>/
        flood_model.joblib   the model (not committed; kept in Drive or blob storage)
        metrics.json         same format as artifacts/metrics.json
        config.toml          the config the run used, copied byte for byte
        summary.md           what it scored, against the champion

Every run gets a ledger line in riskengine/ml/experiments.md, whether it is
kept or not.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from pathlib import Path

from riskengine.ml import train

from .config import RunConfig

ML_DIR = Path(__file__).resolve().parents[1]
RUNS_DIR = ML_DIR / "runs"
LEDGER_PATH = ML_DIR / "experiments.md"
CHAMPION_METRICS = train.METRICS_PATH


def run_id(now: dt.datetime | None = None, commit: str | None = None) -> str:
    """`FLOODSCOUT_RUN_ID` when the notebook set it, else <UTC time>_<short commit>."""
    from_env = os.environ.get(train.RUN_ID_ENV, "").strip()
    if from_env:
        return from_env
    now = now or dt.datetime.now(dt.UTC)
    commit = commit if commit is not None else train.git_commit()
    return f"{now:%Y%m%dT%H%MZ}_{(commit or 'nocommit')[:7]}"


def champion_comparison(config: RunConfig, metrics: dict, champion_path: Path = CHAMPION_METRICS) -> dict:
    """The run's primary metric against the served model's, and whether that comparison is fair."""
    metric = config.value("promotion", "primary_metric")
    margin = config.value("promotion", "margin")
    score = metrics["comparison"][metrics["selected"]][metric]
    if not champion_path.is_file():
        return {"metric": metric, "score": score, "margin": margin, "champion": None}
    champion = json.loads(champion_path.read_text())
    champion_score = champion["comparison"][champion["selected"]][metric]
    same_data = (
        champion["provenance"]["training_set_sha256"] == metrics["provenance"]["training_set_sha256"]
    )
    return {
        "metric": metric,
        "score": score,
        "margin": margin,
        "champion": champion["version"],
        "champion_score": champion_score,
        "difference": round(score - champion_score, 4),
        "same_training_set": same_data,
        # Only meaningful on the same training set and folds.
        "meets_margin": same_data and score - champion_score >= margin,
    }


def summary(run: str, config: RunConfig, metrics: dict, versus: dict) -> str:
    lines = [
        f"# Run {run}",
        "",
        f"- Config: `{config.path.name}` ({config.model})",
        f"- Commit: `{metrics['provenance']['git_commit']}`",
        f"- Training set SHA-256: `{metrics['provenance']['training_set_sha256']}`",
        f"- Rows: {metrics['rows']}, positives: {metrics['positives']}",
        f"- Selected: {metrics['selected']}, calibration: {metrics['calibration']}",
        f"- {versus['metric']} on held-out years: {versus['score']}",
        f"- Lift over the season-only model: {metrics['lift_over_season_only']}",
        "",
        "## Against the champion",
        "",
    ]
    if versus["champion"] is None:
        lines.append("No champion on disk to compare with.")
    elif not versus["same_training_set"]:
        lines.append(
            f"{versus['champion']} scored {versus['champion_score']} on a different training set, "
            "so the two are not comparable. Score both on the same training set and folds "
            "before deciding anything."
        )
    else:
        verdict = "meets" if versus["meets_margin"] else "does not meet"
        lines.append(
            f"{versus['champion']}: {versus['champion_score']}. Difference {versus['difference']:+}, "
            f"margin {versus['margin']}: the run {verdict} the primary rule."
        )
    lines += [
        "",
        "Promotion also needs the guard metrics, a shadow period and a reviewed registry PR",
        "(riskengine/ml/README.md). The final test is closed and was not scored.",
        "",
    ]
    return "\n".join(lines)


def write(run: str, config: RunConfig, artifact: dict, metrics: dict, runs_dir: Path = RUNS_DIR) -> Path:
    """Write the run's folder and return it."""
    folder = runs_dir / run
    train.save(artifact, metrics, folder)
    shutil.copyfile(config.path, folder / "config.toml")
    versus = champion_comparison(config, metrics)
    (folder / "summary.md").write_text(summary(run, config, metrics, versus), encoding="utf-8")
    return folder


def record(
    run: str,
    config: RunConfig,
    metrics: dict,
    outcome: str = "candidate",
    ledger: Path = LEDGER_PATH,
    today: dt.date | None = None,
) -> str:
    """Add the run's line to the ledger and return it. The ledger's last table is the runs table."""
    metric = config.value("promotion", "primary_metric")
    score = metrics["comparison"][metrics["selected"]][metric]
    line = (
        f"| {run} | {(today or dt.date.today()).isoformat()} | {config.model} | "
        f"`{config.path.name}` | {metrics['selected']} | {metric} {score} | {outcome} |"
    )
    text = ledger.read_text(encoding="utf-8") if ledger.is_file() else ""
    ledger.write_text(text.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")
    return line
