"""Replay the candidate map bands over 1996 to 2016 and print what each colour meant.

    manage.py backtest_map_bands

See riskengine/bands.py for the candidates, what each band has to mean and
why S3 is the map's. Needs data/processed/training_set.csv and the rainfall
`build_training_set` cached for it, as `backtest_alerts` does.
"""

from __future__ import annotations

import json

import numpy as np
from django.core.management.base import BaseCommand, CommandError

from alerts import backtest
from riskengine import bands
from riskengine.management.commands.build_training_set import (
    DATA_DIR,
    RAINFALL_DIR,
    TRAINING_SET_PATH,
    rain_model_of,
    rainfall_cache_dir,
)
from riskengine.ml import experiments, train

RESULT_PATH = DATA_DIR / "processed" / "map_bands_backtest.json"


class Command(BaseCommand):
    help = "Backtest the candidate map risk bands on 1996-2016."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seeds",
            type=int,
            nargs="+",
            default=list(train.SEEDS),
            metavar="SEED",
            help=f"Seeds to average the scores over (default {' '.join(map(str, train.SEEDS))}).",
        )

    def handle(self, *args, **options):
        if not TRAINING_SET_PATH.exists():
            raise CommandError(f"{TRAINING_SET_PATH} not found — run `manage.py build_training_set` first.")
        data = train.load_training_set(TRAINING_SET_PATH)
        if data.meta.get("rain_source") == "earth-engine-county-mean":
            raise CommandError("Built for the point-rainfall training set, which the live model uses.")
        try:
            rainfall = experiments.cached_rainfall(
                rainfall_cache_dir(RAINFALL_DIR, rain_model_of(data.meta)), sorted(set(data.counties))
            )
        except (FileNotFoundError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Scoring {len(data.y)} county-months on held-out years, seeds {options['seeds']}…")
        probability, _before = backtest.held_out_scores(data, rainfall, options["seeds"])
        result = bands.replay(
            probability,
            data.y == 1,
            data.X[:, data.column("rain_30d")],
            data.X[:, data.column("rain_30d_ratio")],
            np.asarray(data.months),
            np.asarray(data.years),
        )
        RESULT_PATH.write_text(json.dumps(result, indent=2))

        self.stdout.write(f"\nUsual chance {result['usual_chance']:.3f}. 1996 to 2016; 2017 to 2021 not scored.")
        self.stdout.write(
            f"  {'Candidate':<44} {'Low':>6} {'Mod':>6} {'High':>6} {'VH':>6} {'High+':>7} "
            f"{'Floods at High+':>16} {'Typical High+ Oct-Dec / Mar-May':>32}"
        )
        for name, text in result["candidates"].items():
            r = result["results"][name]
            t = r["times_usual"]
            cells = " ".join("     —" if t[k] is None else f"{t[k]:5.2f}x" for k in t)
            mark = "" if r["meets_meanings"] else "  fails"
            self.stdout.write(
                f"  {name} {text:<41} {cells} {r['high_or_above_share']:6.1%} "
                f"{r['floods_high_or_above']:>8} of {r['floods']:<5} "
                f"{r['typical_high_short_rains']:>14.0f} / {r['typical_high_long_rains']:<14.0f}{mark}"
            )
        self.stdout.write(
            f"\nBy the rule: {result['chosen_by_rule']}. On the map: {result['chosen']} (see bands.py)."
        )
        self.stdout.write(f"Full result in {RESULT_PATH}")
