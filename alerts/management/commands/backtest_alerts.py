"""Replay the candidate alert rules over 1996 to 2021 and print how each did.

    manage.py backtest_alerts
    manage.py backtest_alerts --save-thresholds   # also write the heavy-rain lines the live rules read

See alerts/backtest.py for how the scores, the rain and the rules are
worked out, and why the rain is a perfect forecast. The winner is chosen on
1996 to 2016; 2017 to 2021 is scored once, for it and for today's rules.

Needs data/processed/training_set.csv, the rainfall `build_training_set`
cached for it, and the soil moisture `compare_inputs soil_moisture_start`
cached (Earth Engine only if it is missing).
"""

from __future__ import annotations

import datetime as dt
import json

from django.core.management.base import BaseCommand, CommandError

from alerts import backtest
from geodata.models import Region
from geodata.services import earth_engine_rain
from riskengine.management.commands.build_training_set import (
    DATA_DIR,
    RAINFALL_DIR,
    TRAINING_SET_PATH,
    rain_model_of,
    rainfall_cache_dir,
)
from riskengine.management.commands.compare_inputs import EE_SOIL_DIR
from riskengine.ml import experiments, train
from riskengine.ml.features import WINDOW_DAYS

from ...rules import HEAVY_RAIN_7D_PATH

RESULT_PATH = DATA_DIR / "processed" / "alert_backtest.json"


class Command(BaseCommand):
    help = "Backtest the candidate alert rules on 1996-2021 and print each one's alerts and hits."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seeds", type=int, nargs="+", default=list(train.SEEDS), metavar="SEED",
            help=f"Seeds to average the scores over (default {' '.join(map(str, train.SEEDS))}).",
        )
        parser.add_argument(
            "--save-thresholds", action="store_true",
            help=f"Write each county's heavy-rain line per month to {HEAVY_RAIN_7D_PATH.name}.",
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
            counties = list(Region.objects.filter(level=Region.Level.COUNTY).order_by("name"))
            first, last = (dt.date.fromisoformat(date) for date in (min(data.dates), max(data.dates)))
            soil = earth_engine_rain.fetch_county_soil_moisture(
                counties, first - dt.timedelta(days=WINDOW_DAYS), last, EE_SOIL_DIR, log=self.stdout.write
            )
        except (FileNotFoundError, ValueError, earth_engine_rain.EarthEngineError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Scoring {len(data.y)} county-months on held-out years, seeds {options['seeds']}…")
        result = backtest.backtest(backtest.history(data, rainfall, soil, options["seeds"]))
        RESULT_PATH.write_text(json.dumps(result, indent=2))

        self.stdout.write("")
        self.stdout.write("Rain is what fell, standing in for a perfect forecast: every rain rule looks better than it will live.")
        self.stdout.write("Floods are flooded county-months.")
        self.stdout.write("")
        self._table("1996–2016 (choose here)", result["development"], result["rules"])
        dev = result["development"]
        self.stdout.write(
            f"Chosen: {result['winner']}, the best hit rate keeping {backtest.KEEP_CAUGHT:.0%} of the "
            f"{dev['A']['caught']} floods A catches."
        )
        self.stdout.write("")
        for name, years in result["development_by_year"].items():
            cells = " ".join(f"{year % 100:02d}:{r['alerts']}/{r['caught']}" for year, r in years.items())
            self.stdout.write(f"  {name} by year (alerts/caught)  {cells}")
        self.stdout.write("")
        if result["test"]:
            self._table("2017–2021 (scored once)", result["test"], result["rules"])
        if options["save_thresholds"]:
            HEAVY_RAIN_7D_PATH.write_text(json.dumps(result["heavy_rain_7d_mm"], indent=1, sort_keys=True))
            self.stdout.write(f"Wrote {HEAVY_RAIN_7D_PATH}")
        self.stdout.write(f"Full result in {RESULT_PATH}")

    def _table(self, title, results, rules):
        self.stdout.write(title)
        self.stdout.write(
            f"  {'Rule':<62} {'Alerts':>7} {'Caught':>12} {'Hit rate':>9} {'Per month':>10}"
        )
        for name, r in results.items():
            caught = f"{r['caught']}/{r['floods']}"
            hit = "—" if r["hit_rate"] is None else f"{r['hit_rate']:.3f}"
            self.stdout.write(
                f"  {name}. {rules[name]:<59} {r['alerts']:>7} {caught:>12} {hit:>9} "
                f"{r['counties_per_alert_month']:>10}"
            )
        self.stdout.write("")
