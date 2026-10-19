"""Score a change to how a flood is assigned to a month.

    manage.py compare_labels --min-overlap-days 7

A county-month is labelled as flooded when a recorded flood overlaps its 30
days. To date any overlap has counted, so a flood that ends on 3 June labels
the whole of June. This works the labels out again so that a flood counts
for the window it began in, and for a later window only with a minimum
number of days in it (`in_window` in build_training_set), and compares.

Changing the labels changes what is predicted, so there is no single
before-and-after figure. Three columns are printed: the model trained and
scored on the current labels, the same on the new labels, and the model
trained on the current labels scored against the new ones. The base rate
and the season-only model are given for each, because both move with the
labels.

Nothing is saved. To adopt a rule, set MIN_OVERLAP_DAYS in
build_training_set, rebuild the training set and retrain. See
riskengine/ml/experiments.py, and record the result in the experiments log
in riskengine/ml/README.md.

With --county-mean-rain the rainfall inputs are worked out from county-mean
rainfall first, as `compare_inputs --county-mean-rain` does.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
from django.core.management.base import BaseCommand, CommandError

from geodata.models import Region
from geodata.services import dfo, earth_engine_rain
from riskengine.ml import experiments, train
from riskengine.ml.features import HISTORY_DAYS, WINDOW_DAYS

from .build_training_set import (
    EE_RAINFALL_DIR,
    MIN_OVERLAP_DAYS,
    TRAINING_SET_PATH,
    events_by_county,
    in_window,
)

COLUMNS = (
    ("current", "current labels"),
    ("new", "new labels"),
    ("current_on_new", "current, scored on new"),
)
MEASURES = (
    ("average_precision", "Average precision, pooled"),
    ("average_precision_fold_mean", "Average precision, mean of folds"),
    ("roc_auc", "ROC AUC, pooled"),
    ("brier", "Brier score, pooled"),
    ("floods_missed", "Floods missed"),
    ("false_alarms", "False alarms"),
    ("june_missed", "June floods missed"),
)


def labels_for(data: train.TrainingSet, placed: dict, min_days: int) -> np.ndarray:
    """Each row's flood label under a minimum overlap of `min_days`."""
    labels = np.zeros(len(data.y), dtype=int)
    for index, (county, date) in enumerate(zip(data.counties, data.dates)):
        end = dt.date.fromisoformat(date)
        start = end - dt.timedelta(days=WINDOW_DAYS - 1)
        labels[index] = any(in_window(event, start, end, min_days) for event in placed[county])
    return labels


class Command(BaseCommand):
    help = "Compare the model under a minimum overlap between a flood and a month."

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-overlap-days",
            type=int,
            required=True,
            metavar="DAYS",
            help="Days of a flood that must fall in a window for it to count there.",
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
        if not TRAINING_SET_PATH.exists():
            raise CommandError(
                f"{TRAINING_SET_PATH} not found — run `manage.py build_training_set` first."
            )
        data = train.load_training_set(TRAINING_SET_PATH)
        counties = list(Region.objects.filter(level=Region.Level.COUNTY).order_by("name"))
        try:
            placed = events_by_county(counties, log=self.stdout.write)
            if options["county_mean_rain"]:
                first, last = (dt.date.fromisoformat(d) for d in (min(data.dates), max(data.dates)))
                data = experiments.with_rainfall(
                    data,
                    earth_engine_rain.fetch_county_rainfall(
                        counties,
                        first - dt.timedelta(days=HISTORY_DAYS),
                        last,
                        EE_RAINFALL_DIR,
                        log=self.stdout.write,
                    ),
                )
        except (dfo.DFOError, earth_engine_rain.EarthEngineError) as exc:
            raise CommandError(str(exc)) from exc

        # The rule the training set was built with has to give its labels
        # back, or the events here are not the ones it was built from.
        built_with = int(data.meta.get("min_overlap_days", MIN_OVERLAP_DAYS))
        if not np.array_equal(labels_for(data, placed, built_with), data.y):
            raise CommandError("The flood archive does not reproduce the training set's labels.")

        min_days = options["min_overlap_days"]
        labels = labels_for(data, placed, min_days)
        result = experiments.compare_labels(data, labels, options["seeds"])

        self.stdout.write(
            f"{experiments.MODEL} on held-out years, mean over seeds {options['seeds']}; "
            f"a warning is above {100 * train.WARNING_THRESHOLD:.0f}%"
        )
        self.stdout.write(
            f"Current labels: a flood counts with {built_with} day(s) in the window. "
            f"New labels: the window it began in, or {min_days} days in a later one."
        )
        dropped = int(((data.y == 1) & (labels == 0)).sum())
        added = int(((data.y == 0) & (labels == 1)).sum())
        self.stdout.write(f"County-months no longer flooded: {dropped}; newly flooded: {added}")
        self.stdout.write("")
        self.stdout.write(f"  {'':<34}" + "".join(f"{title:>24}" for _key, title in COLUMNS))

        def line(label, cell):
            self.stdout.write(f"  {label:<34}" + "".join(f"{cell(result[key]):>24}" for key, _t in COLUMNS))

        line("Floods (county-months)", lambda r: f"{r['floods']}")
        line("Base rate", lambda r: f"{r['base_rate']:g}")
        line("Season-only average precision", lambda r: f"{r['season_only_average_precision']:g}")
        for key, label in MEASURES:
            line(label, lambda r, key=key: f"{r[key]['mean']:g}")
        line("June floods", lambda r: f"{r['june_floods']}")
        line(
            "Lift over the base rate",
            lambda r: f"{r['average_precision']['mean'] / r['base_rate']:.2f}",
        )
        line(
            "Lift over season only",
            lambda r: f"{r['average_precision']['mean'] / r['season_only_average_precision']:.2f}",
        )
