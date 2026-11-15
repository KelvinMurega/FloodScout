"""Replay a county-normal warning line over 1996 to 2016 and print how each candidate did.

    manage.py backtest_county_normal
    manage.py backtest_county_normal --seeds 0     # quicker, one seed

See riskengine/county_normal.py for what is replayed and why. Nothing on
the map changes. Needs data/processed/training_set.csv and the rainfall
`build_training_set` cached for it, as `backtest_alerts` does.
"""

from __future__ import annotations

import json

import numpy as np
from django.core.management.base import BaseCommand, CommandError

from alerts import backtest
from riskengine import county_normal
from riskengine.management.commands.build_training_set import (
    DATA_DIR,
    RAINFALL_DIR,
    TRAINING_SET_PATH,
    rain_model_of,
    rainfall_cache_dir,
)
from riskengine.ml import experiments, train

RESULT_PATH = DATA_DIR / "processed" / "county_normal_backtest.json"

GROUPS = {
    "all": "Every month",
    "long_rains": "March to May",
    "short_rains": "October to December",
    "western_highlands": "Western highlands",
}


class Command(BaseCommand):
    help = "Backtest a county-normal warning line on 1996-2016 against today's line."

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
        result = county_normal.replay(
            probability, data.y == 1, np.asarray(data.counties), np.asarray(data.months), np.asarray(data.years)
        )
        RESULT_PATH.write_text(json.dumps(result, indent=2))

        first, last = result["years"]
        self.stdout.write(f"\n{first} to {last}, flooded county-months as recorded. 2017 to 2021 is not scored.\n")
        for group, title in GROUPS.items():
            self.stdout.write(title)
            self.stdout.write(
                f"  {'Line':<66} {'Warnings':>8} {'Caught':>11} {'False alarms':>13} {'Hit rate':>9}"
            )
            for name, text in result["candidates"].items():
                r = result["results"][name][group]
                hit = "—" if r["hit_rate"] is None else f"{r['hit_rate']:.3f}"
                self.stdout.write(
                    f"  {text:<66} {r['warnings']:>8} {r['caught']:>5}/{r['floods']:<5} "
                    f"{r['false_alarms']:>13} {hit:>9}"
                )
            self.stdout.write("")
        for name, text in result["candidates"].items():
            per = result["results"][name]["counties_per_warned_month"]
            self.stdout.write(f"  {text:<66} {per:>5} counties in a warned month")
        chosen = result["chosen"]
        self.stdout.write(
            f"\nChosen: {result['candidates'][chosen] if chosen else 'none'}, the best hit rate keeping "
            f"{result['keep_caught']:.0%} of the floods today's line catches."
        )
        self.stdout.write(f"Full result in {RESULT_PATH}")
