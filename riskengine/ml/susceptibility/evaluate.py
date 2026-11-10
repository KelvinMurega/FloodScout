"""Scoring for the susceptibility model: SUSCEPTIBILITY_PROTOCOL.md, sections 4, 5 and 7.

`data` is a dict of equal-length NumPy arrays, one per column of the sample.
Every score, whether from a baseline or a model, goes through the same
functions, so the comparison is like for like. All of them use the sampling
weights, which restore each event's true flooded share.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

MIN_FLOODED = 20
MIN_DRY = 20
TEXT_COLUMNS = {"county"}
# Scores with no fitted parameters. Higher means more susceptible.
BASELINES = {
    "hand": lambda data: -data["hand"],
    "flood-zone-100y": lambda data: data["flood_zone_100y"].astype(float),
    "distance-to-river": lambda data: -data["dist_river"],
}


def load_sample(path: Path) -> dict:
    with open(path, newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {
        name: np.array([row[name] for row in rows], dtype=str if name in TEXT_COLUMNS else float)
        for name in rows[0]
    }


def by_county_event(data: dict, score, min_flooded=MIN_FLOODED, min_dry=MIN_DRY) -> list[dict]:
    """One row per event and county with enough pixels of both classes."""
    score = np.asarray(score, dtype=float)
    keys = np.char.add(data["event"].astype(int).astype(str), np.char.add("|", data["county"]))
    rows = []
    for key in np.unique(keys):
        chosen = keys == key
        labels, weight = data["flooded"][chosen], data["weight"][chosen]
        flooded = int(labels.sum())
        dry = len(labels) - flooded
        if flooded < min_flooded or dry < min_dry:
            continue
        event, county = key.split("|")
        rows.append(
            {
                "event": int(event),
                "county": county,
                "flooded_pixels": flooded,
                "dry_pixels": dry,
                "base_rate": float(np.average(labels, weights=weight)),
                "roc_auc": float(roc_auc_score(labels, score[chosen], sample_weight=weight)),
                "average_precision": float(
                    average_precision_score(labels, score[chosen], sample_weight=weight)
                ),
            }
        )
    return rows


def summary(data: dict, score) -> dict:
    """The primary metric and its companions for one score."""
    table = by_county_event(data, score)
    if not table:
        raise ValueError("No county-event has enough pixels of both classes.")
    auc = np.array([row["roc_auc"] for row in table])
    precision = np.array([row["average_precision"] for row in table])
    flooded = np.array([row["flooded_pixels"] for row in table])
    events = sorted({row["event"] for row in table})
    per_event = {}
    for event in events:
        rows = [row for row in table if row["event"] == event]
        per_event[event] = {
            "county_events": len(rows),
            "roc_auc": round(float(np.mean([row["roc_auc"] for row in rows])), 4),
            "average_precision": round(
                float(np.mean([row["average_precision"] for row in rows])), 4
            ),
        }
    return {
        "within_county_roc_auc": round(float(auc.mean()), 4),
        "within_county_roc_auc_weighted_by_flooded": round(
            float(np.average(auc, weights=flooded)), 4
        ),
        "within_county_average_precision": round(float(precision.mean()), 4),
        "mean_base_rate": round(float(np.mean([row["base_rate"] for row in table])), 4),
        "county_events": len(table),
        "events": len(events),
        "per_event": per_event,
    }


def without_lakes(data: dict, score) -> tuple[dict, np.ndarray]:
    """The rows away from large lakes, and their scores. The sensitivity check only."""
    keep = data["near_lake"] == 0
    return {name: column[keep] for name, column in data.items()}, np.asarray(score)[keep]


def score_baselines(data: dict) -> dict:
    return {name: summary(data, score(data)) for name, score in BASELINES.items()}


def best_baseline(baselines: dict) -> str:
    return max(baselines, key=lambda name: baselines[name]["within_county_roc_auc"])


def calibration(data: dict, probability, bins: int = 10) -> dict:
    """Brier score, and a reliability table of equal-weight bins."""
    probability = np.asarray(probability, dtype=float)
    weight, observed = data["weight"], data["flooded"]
    order = np.argsort(probability, kind="stable")
    cumulative = np.cumsum(weight[order])
    which = np.minimum((cumulative / cumulative[-1] * bins).astype(int), bins - 1)
    table = []
    for number in range(bins):
        chosen = order[which == number]
        if len(chosen):
            table.append(
                {
                    "bin": number + 1,
                    "rows": len(chosen),
                    "mean_predicted": round(
                        float(np.average(probability[chosen], weights=weight[chosen])), 5
                    ),
                    "observed": round(
                        float(np.average(observed[chosen], weights=weight[chosen])), 5
                    ),
                }
            )
    return {
        "brier": round(float(brier_score_loss(observed, probability, sample_weight=weight)), 5),
        "reliability": table,
    }


def decide(model_event_cv, model_block_cv, baseline_event_cv, baseline_block_cv, margin) -> dict:
    """Section 7, rule 1, as written. The sign check is separate (rule 3)."""
    beats_margin = model_event_cv >= baseline_event_cv + margin
    holds_on_blocks = model_block_cv >= baseline_block_cv
    return {
        "beats_best_baseline_by_margin": bool(beats_margin),
        "not_below_baseline_on_block_cv": bool(holds_on_blocks),
        "ship": "model" if beats_margin and holds_on_blocks else "best-baseline",
    }
