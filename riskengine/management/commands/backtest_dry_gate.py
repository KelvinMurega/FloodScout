"""Replay the dry-gate candidates over 1996 to 2021 and print how each did.

    manage.py backtest_dry_gate

Every county-month gets the probability of a model that never saw its year
(alerts/backtest.py, `history`), its category from the model's bands, and
then each candidate gate from riskengine/dry.py. For each it counts how much
of the map is High or above (how red it is), the floods there, and the floods
the gate moved below High.

The choice is fixed before the run (CHOOSE): the gate that takes the most
county-months out of High and above while keeping at least KEEP of the
floods the ungated map has there, chosen on 1996 to 2016. 2017 to 2021 is
scored once, after.
"""

from __future__ import annotations

import json
from collections import Counter

import numpy as np
from django.core.management.base import BaseCommand, CommandError

from alerts import backtest
from riskengine import dry
from riskengine.management.commands.build_training_set import (
    DATA_DIR,
    RAINFALL_DIR,
    TRAINING_SET_PATH,
    rain_model_of,
    rainfall_cache_dir,
)
from riskengine.ml import experiments, train

RESULT_PATH = DATA_DIR / "processed" / "dry_gate_backtest.json"

# A gate must keep this share of the floods the ungated map puts at High or above.
KEEP = 0.9
CHOOSE = f"the most county-months out of High and above, keeping {KEEP:.0%} of the floods there"


def levels(probability: np.ndarray) -> np.ndarray:
    """0 Low, 1 Moderate, 2 High, 3 Very High, from the model's bands."""
    return np.searchsorted(np.array(train.PROBABILITY_BANDS), probability, side="left")


def score(level: np.ndarray, flooded: np.ndarray, rows: np.ndarray, counties, base=None) -> dict:
    level, flooded = level[rows], flooded[rows]
    high = level >= 2
    out = {
        "county_months": int(rows.sum()),
        "floods": int(flooded.sum()),
        "high_or_above": int(high.sum()),
        "high_share": round(float(high.mean()), 3),
        "very_high": int((level == 3).sum()),
        "floods_high_or_above": int((high & flooded).sum()),
        "flood_rate_high_or_above": round(float(flooded[high].mean()), 3) if high.any() else None,
        "flood_rate_below_high": round(float(flooded[~high].mean()), 3) if (~high).any() else None,
    }
    if base is not None:
        lost = (base[rows] >= 2) & ~high & flooded
        out["floods_lost"] = int(lost.sum())
        out["floods_lost_by_county"] = dict(Counter(map(str, counties[rows][lost])).most_common(6))
    return out


class Command(BaseCommand):
    help = "Backtest the dry-gate candidates on 1996-2021: how red the map is, and the floods kept."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seeds", type=int, nargs="+", default=list(train.SEEDS), metavar="SEED",
            help=f"Seeds to average the scores over (default {' '.join(map(str, train.SEEDS))}).",
        )

    def handle(self, *args, **options):
        if not TRAINING_SET_PATH.exists():
            raise CommandError(f"{TRAINING_SET_PATH} not found — run `manage.py build_training_set` first.")
        data = train.load_training_set(TRAINING_SET_PATH)
        try:
            rainfall = experiments.cached_rainfall(
                rainfall_cache_dir(RAINFALL_DIR, rain_model_of(data.meta)), sorted(set(data.counties))
            )
        except (FileNotFoundError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Scoring {len(data.y)} county-months on held-out years, seeds {options['seeds']}…")
        h = backtest.history(data, rainfall, None, options["seeds"])
        rain = data.X[:, data.column("rain_30d")]
        ratio = data.X[:, data.column("rain_30d_ratio")]
        base = levels(h.probability)
        gated = {
            name: np.where(
                [dry.dry_by(name, mm, r) for mm, r in zip(rain, ratio)], np.minimum(base, 1), base
            )
            for name in dry.GATES
        }

        development = h.years < train.FINAL_TEST_FROM
        test = ~development
        dev = {"none": score(base, h.flooded, development, h.counties)}
        dev |= {name: score(level, h.flooded, development, h.counties, base) for name, level in gated.items()}
        floor = KEEP * dev["none"]["floods_high_or_above"]
        eligible = [name for name in dry.GATES if dev[name]["floods_high_or_above"] >= floor]
        winner = min(eligible, key=lambda name: dev[name]["high_or_above"]) if eligible else None

        checked = {"none": score(base, h.flooded, test, h.counties)}
        if winner:
            checked[winner] = score(gated[winner], h.flooded, test, h.counties, base)

        result = {
            "gates": dry.GATES,
            "choose": CHOOSE,
            "development": dev,
            "winner": winner,
            "test": checked,
        }
        RESULT_PATH.write_text(json.dumps(result, indent=2))

        self._table("1996–2016 (choose here)", dev)
        self.stdout.write(f"Rule: {CHOOSE}. Chosen: {winner or 'none qualifies'}.")
        self.stdout.write("")
        self._table("2017–2021 (scored once)", checked)
        for part, results in (("1996–2016", dev), ("2017–2021", checked)):
            for name, r in results.items():
                if r.get("floods_lost"):
                    self.stdout.write(f"  {part} {name} floods moved below High: {r['floods_lost_by_county']}")
        self.stdout.write(f"Full result in {RESULT_PATH}")

    def _table(self, title, results):
        self.stdout.write(title)
        self.stdout.write(
            f"  {'Gate':<52} {'High+':>12} {'Very High':>10} {'Floods in High+':>16} "
            f"{'Flood rate High+':>17} {'below High':>11}"
        )
        for name, r in results.items():
            label = f"{name}. {dry.GATES.get(name, 'no gate')}"
            self.stdout.write(
                f"  {label:<52} {r['high_or_above']:>6} ({r['high_share']:.0%}) {r['very_high']:>10} "
                f"{r['floods_high_or_above']:>9}/{r['floods']:<6} {r['flood_rate_high_or_above'] or 0:>17.3f} "
                f"{r['flood_rate_below_high'] or 0:>11.3f}"
            )
        self.stdout.write("")
