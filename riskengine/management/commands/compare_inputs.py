"""Score the model with and without a change to its inputs.

    manage.py compare_inputs rain_60d rain_90d
    manage.py compare_inputs --county-mean-rain

The first compares the current inputs with the current inputs plus the ones
named. The second works the rainfall inputs out from rainfall averaged over
each county (ERA5-Land, through Earth Engine) in place of one point, with
the labels and every other input unchanged. Both can be given together.

Both sides are scored on held-out years and printed. Nothing is saved: the
shipped model is not touched. See riskengine/ml/experiments.py for how, and
record the result in the experiments log in riskengine/ml/README.md.

Needs data/processed/training_set.csv and the rainfall `build_training_set`
cached for it. The trial inputs are worked out from the rainfall the
training set was built on, point or county mean. --county-mean-rain is for a
training set built on point rainfall, and needs Earth Engine the first time
(see geodata/services/earth_engine_rain.py).
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand, CommandError

from geodata.models import Region
from geodata.services import earth_engine_rain
from riskengine.ml import experiments, train
from riskengine.ml.features import CANDIDATE_FEATURES, HISTORY_DAYS

from .build_training_set import (
    EE_RAINFALL_DIR,
    RAINFALL_DIR,
    TRAINING_SET_PATH,
    rain_model_of,
    rainfall_cache_dir,
)

MEASURES = (
    ("average_precision", "Average precision, pooled", None),
    ("average_precision_fold_mean", "Average precision, mean of folds", None),
    ("roc_auc", "ROC AUC, pooled", None),
    ("brier", "Brier score, pooled", None),
    ("floods_missed", "Floods missed", "floods"),
    ("false_alarms", "False alarms", None),
    ("june_missed", "June floods missed", "june_floods"),
    ("intense_rain_missed", "Missed in " + ", ".join(experiments.INTENSE_RAIN_COUNTIES), "intense_rain_floods"),
)


class Command(BaseCommand):
    help = "Compare the model's held-out scores with and without inputs under trial."

    def add_arguments(self, parser):
        parser.add_argument(
            "inputs",
            nargs="*",
            choices=CANDIDATE_FEATURES,
            metavar="INPUT",
            help=f"Inputs to add, from: {', '.join(CANDIDATE_FEATURES)}.",
        )
        parser.add_argument(
            "--county-mean-rain",
            action="store_true",
            help="Measure rainfall as the mean over each county instead of at one point.",
        )
        parser.add_argument(
            "--seeds",
            type=int,
            nargs="+",
            default=list(train.SEEDS),
            metavar="SEED",
            help=f"Seeds to average over (default {' '.join(map(str, train.SEEDS))}).",
        )

    def handle(self, *args, **options):
        if not options["inputs"] and not options["county_mean_rain"]:
            raise CommandError("Nothing to compare: name an input or give --county-mean-rain.")
        if not TRAINING_SET_PATH.exists():
            raise CommandError(
                f"{TRAINING_SET_PATH} not found — run `manage.py build_training_set` first."
            )
        data = train.load_training_set(TRAINING_SET_PATH)
        on_county_mean = data.meta.get("rain_source") == "earth-engine-county-mean"
        if on_county_mean and options["county_mean_rain"]:
            raise CommandError("The training set is already built on county-mean rainfall.")
        try:
            rainfall = (
                self._county_mean_rainfall(data)
                if on_county_mean
                else experiments.cached_rainfall(
                    rainfall_cache_dir(RAINFALL_DIR, rain_model_of(data.meta)),
                    sorted(set(data.counties)),
                )
            )
            county_mean = self._county_mean_rainfall(data) if options["county_mean_rain"] else None
            result = experiments.compare(
                data, rainfall, options["inputs"], options["seeds"], other_rainfall=county_mean
            )
        except (FileNotFoundError, ValueError, earth_engine_rain.EarthEngineError) as exc:
            raise CommandError(str(exc)) from exc

        before, after = result["before"], result["after"]
        self.stdout.write(
            f"{experiments.MODEL} on held-out years, mean (sd) over seeds {before['seeds']}; "
            f"a warning is above {100 * train.WARNING_THRESHOLD:.0f}%"
        )
        if options["county_mean_rain"]:
            self.stdout.write("Rainfall: one point per county before, the county mean after")
        if result["added"]:
            self.stdout.write(f"Added: {', '.join(result['added'])}")
        self.stdout.write("")
        self.stdout.write(f"  {'':<44} {'before':>18} {'after':>18}")
        for key, label, total in MEASURES:
            if total:
                label = f"{label} (of {before[total]})"
            cells = [
                # One seed has no spread to report.
                f"{scores[key]['mean']:g}"
                + ("" if scores[key]["sd"] is None else f" ({scores[key]['sd']:g})")
                for scores in (before, after)
            ]
            self.stdout.write(f"  {label:<44} {cells[0]:>18} {cells[1]:>18}")
        self.stdout.write("")
        verdict = "beyond" if result["beyond_noise"] else "within"
        self.stdout.write(
            f"Pooled average precision changed by {result['average_precision_change']:+.4f}, "
            f"{verdict} the noise between seeds ({experiments.NOISE})."
        )

    def _county_mean_rainfall(self, data):
        """Daily county-mean rainfall over the span the training set was built on."""
        counties = list(Region.objects.filter(level=Region.Level.COUNTY).order_by("name"))
        first, last = (dt.date.fromisoformat(date) for date in (min(data.dates), max(data.dates)))
        return earth_engine_rain.fetch_county_rainfall(
            counties, first - dt.timedelta(days=HISTORY_DAYS), last, EE_RAINFALL_DIR, log=self.stdout.write
        )
