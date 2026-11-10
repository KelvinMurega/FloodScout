"""The one UNOSAT look: SUSCEPTIBILITY_PROTOCOL.md, sections 8 and 9.

Plain Python on NumPy arrays, so every rule can be tested without Earth
Engine, GDAL or the real extents. The command `susceptibility_unosat_test`
fetches the pixels and the extents and hands them to these functions.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score

# Fine cells per 250 m pixel side when the extent is burned in. 5 x 5 = 25
# cells, so "at least half covered" is read to the nearest 4%.
SUPERSAMPLE = 5
HALF = 0.5
FEATURE_BANDS = ["hand", "twi", "log_upa", "dist_river", "elev", "slope", "flood_zone_100y"]
OTHER_BANDS = ["sub_county", "permanent", "near_lake", "area_m2"]
BANDS = FEATURE_BANDS + OTHER_BANDS
LOOK_HEADER = "| Date | Test set | Run or model | Commit | Result |"
TEST_SET = "UNOSAT FL20231124KEN and FL20240426KEN, susceptibility model"


def coverage(fine: np.ndarray, factor: int = SUPERSAMPLE) -> np.ndarray:
    """Share of each coarse pixel covered, from a burned raster `factor` times finer."""
    rows, cols = fine.shape[0] // factor, fine.shape[1] // factor
    if (rows * factor, cols * factor) != fine.shape:
        raise ValueError(f"A {fine.shape} raster is not a multiple of {factor}.")
    blocks = fine.reshape(rows, factor, cols, factor).astype(float)
    return blocks.mean(axis=(1, 3))


def flooded(fine: np.ndarray, factor: int = SUPERSAMPLE) -> np.ndarray:
    """Section 8: a pixel is flooded when at least half of it is inside the extent."""
    return (coverage(fine, factor) >= HALF).astype(int)


def usable(pixels: dict) -> np.ndarray:
    """Pixels in a sub-county, not permanent water, with every feature present."""
    complete = np.all([np.isfinite(pixels[name]) for name in FEATURE_BANDS], axis=0)
    return complete & (pixels["sub_county"] > 0) & (pixels["permanent"] == 0)


def pixel_table(events: list[tuple[int, dict, np.ndarray]], county_of: dict) -> dict:
    """The test pixels as `evaluate` reads them.

    `events` is [(event number, pixels, labels)], `pixels` a dict of equal-length
    arrays named as BANDS and `labels` 0 or 1 for each. Dry is every other usable
    pixel in a county with mapped water, unweighted, so `weight` is 1.
    """
    columns = {name: [] for name in ["event", "county", "flooded", "weight", "near_lake"]}
    columns |= {name: [] for name in FEATURE_BANDS}
    for event, pixels, labels in events:
        keep = usable(pixels)
        county = np.array([county_of[int(number)] for number in pixels["sub_county"][keep]])
        wet = set(county[labels[keep] == 1])
        mapped = np.isin(county, sorted(wet))
        size = int(mapped.sum())
        columns["event"].append(np.full(size, event, dtype=float))
        columns["county"].append(county[mapped])
        columns["flooded"].append(labels[keep][mapped].astype(float))
        columns["weight"].append(np.ones(size))
        columns["near_lake"].append(pixels["near_lake"][keep][mapped])
        for name in FEATURE_BANDS:
            columns[name].append(pixels[name][keep][mapped])
    return {name: np.concatenate(parts) for name, parts in columns.items()}


def expected_area(pixels: dict, probability: np.ndarray) -> dict[int, float]:
    """Section 8: {sub-county number: km² expected to flood}, permanent water left out."""
    keep = usable(pixels)
    numbers = pixels["sub_county"][keep].astype(int)
    km2 = probability[keep] * pixels["area_m2"][keep] / 1e6
    totals = np.bincount(numbers, weights=km2)
    return {int(number): float(totals[number]) for number in np.unique(numbers)}


def land_area(pixels: dict) -> dict[int, float]:
    """The sub-county area baseline: km² of usable land."""
    return expected_area(pixels, np.ones(len(pixels["sub_county"])))


def sub_county_labels(path: Path, event_numbers: dict) -> list[dict]:
    """The labels behind the engine's 0.461, from `validate_unosat --level sub_county`."""
    with open(path, newline="") as handle:
        return [
            {
                "event": event_numbers[row["event"]],
                "county": row["county"],
                "sub_county": row["sub_county"],
                "flooded": int(row["flooded"]),
            }
            for row in csv.DictReader(handle)
        ]


def within_county_auc(rows: list[dict], score: str) -> dict:
    """Mean ROC AUC of sub-counties ranked inside their own county and event.

    The same rule as `validate_unosat.within_county_auc`: only a county-event
    with some flooded and some dry sub-counties can be ranked.
    """
    groups = {}
    for row in rows:
        groups.setdefault((row["event"], row["county"]), []).append(row)
    values = [
        roc_auc_score([row["flooded"] for row in group], [row[score] for row in group])
        for group in groups.values()
        if 0 < sum(row["flooded"] for row in group) < len(group)
    ]
    return {"within_county_roc_auc": round(float(np.mean(values)), 4), "county_events": len(values)}


def already_looked(ledger: str, run_id: str) -> bool:
    """True when experiments.md records a UNOSAT look for this run."""
    return any(
        line.startswith("|") and TEST_SET in line and run_id in line for line in ledger.splitlines()
    )


def record_look(ledger: str, date: str, run_id: str, commit: str, result: str) -> str:
    """experiments.md with the look added under the looks table."""
    lines = ledger.splitlines(keepends=True)
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == LOOK_HEADER)
    except StopIteration as exc:
        raise ValueError("experiments.md has no looks table.") from exc
    end = start + 2
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    row = f"| {date} | {TEST_SET} | {run_id} | `{commit}` | {result} |\n"
    return "".join(lines[:end] + [row] + lines[end:])
