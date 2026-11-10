"""Score a change to the flood labels.

    manage.py compare_labels --min-overlap-days 7
    manage.py compare_labels --mapped-where-available --circle-weight 0.5

The first changes how a flood is assigned to a month; the second, where it
is placed.

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

With --mapped-where-available a flood the Global Flood Database mapped
labels the counties that held mapped flood water, and not its circle (see
riskengine/ml/labels.py). Two more sets of columns follow: every model
scored only on the rows where a mapped flood decides the label, the surest
labels there are, and with --circle-weight a model trained with labels from
circles alone counting for less. The mapped water comes from the cache
`check_labels_gfd` writes, so run that first.

With --county-mean-rain the rainfall inputs are worked out from county-mean
rainfall first, as `compare_inputs --county-mean-rain` does.
"""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
from django.core.management.base import BaseCommand, CommandError

from geodata.models import Region
from geodata.services import dfo, earth_engine_rain
from riskengine.ml import experiments, labels, train
from riskengine.ml.features import HISTORY_DAYS, WINDOW_DAYS

from .build_training_set import (
    DFO_PATH,
    EE_RAINFALL_DIR,
    MIN_OVERLAP_DAYS,
    TRAINING_SET_PATH,
    events_by_county,
    in_window,
)
from .check_labels_gfd import CACHE_PATH as GFD_CACHE_PATH

COLUMNS = (
    ("current", "current labels"),
    ("new", "new labels"),
    ("current_on_new", "current, scored on new"),
    ("new_weighted", "new, weighted"),
    ("current_on_sure", "current, on mapped rows"),
    ("new_on_sure", "new, on mapped rows"),
    ("new_weighted_on_sure", "weighted, on mapped rows"),
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
        rule = parser.add_mutually_exclusive_group(required=True)
        rule.add_argument(
            "--min-overlap-days",
            type=int,
            metavar="DAYS",
            help="Days of a flood that must fall in a window for it to count there.",
        )
        rule.add_argument(
            "--mapped-where-available",
            action="store_true",
            help="Label a flood the Global Flood Database mapped from its mapped water, not its circle.",
        )
        parser.add_argument(
            "--min-km2",
            type=float,
            default=5.0,
            help="Mapped flood water a county needs to count as flooded (default 5, as check_labels_gfd).",
        )
        parser.add_argument(
            "--circle-weight",
            type=float,
            help="With --mapped-where-available: also train with circle-only labels at this weight.",
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

        if options["mapped_where_available"]:
            new_labels, sources = self._mapped(data, placed, built_with, options["min_km2"])
            row_weights = (
                None if options["circle_weight"] is None else labels.weights(sources, options["circle_weight"])
            )
            result = experiments.compare_labels(
                data, new_labels, options["seeds"], weights=row_weights, sure_rows=sources == labels.MAPPED
            )
            rule = (
                f"New labels: a mapped flood labels the counties with {options['min_km2']:g} km² "
                "or more of mapped water; other floods keep their circle."
            )
        else:
            if options["circle_weight"] is not None:
                raise CommandError("--circle-weight goes with --mapped-where-available.")
            min_days = options["min_overlap_days"]
            new_labels = labels_for(data, placed, min_days)
            sources = None
            result = experiments.compare_labels(data, new_labels, options["seeds"])
            rule = f"New labels: the window it began in, or {min_days} days in a later one."

        self.stdout.write(
            f"{experiments.MODEL} on held-out years, mean over seeds {options['seeds']}; "
            f"a warning is above {100 * train.WARNING_THRESHOLD:.0f}%"
        )
        self.stdout.write(f"Current labels: a flood counts with {built_with} day(s) in the window. {rule}")
        dropped = int(((data.y == 1) & (new_labels == 0)).sum())
        added = int(((data.y == 0) & (new_labels == 1)).sum())
        self.stdout.write(f"County-months no longer flooded: {dropped}; newly flooded: {added}")
        if sources is not None:
            counts = {source: int((sources == source).sum()) for source in (labels.MAPPED, labels.CIRCLE)}
            self.stdout.write(
                f"Rows labelled from a map: {counts[labels.MAPPED]} "
                f"({int(new_labels[sources == labels.MAPPED].sum())} flooded); "
                f"flooded by a circle alone: {counts[labels.CIRCLE]}"
            )
        columns = [(key, title) for key, title in COLUMNS if key in result]
        self.stdout.write("")
        self.stdout.write(f"  {'':<34}" + "".join(f"{title:>26}" for _key, title in columns))

        def line(label, cell):
            self.stdout.write(f"  {label:<34}" + "".join(f"{cell(result[key]):>26}" for key, _t in columns))

        line("Rows scored", lambda r: f"{r['rows']}")

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

    def _mapped(self, data, placed, built_with: int, min_km2: float):
        """(labels, sources) with mapped floods labelled from their mapped water."""
        if not GFD_CACHE_PATH.exists():
            raise CommandError(f"{GFD_CACHE_PATH} not found: run `manage.py check_labels_gfd` first.")
        mapped = {int(event_id): water for event_id, water in json.loads(GFD_CACHE_PATH.read_text()).items()}
        try:
            events = {event.id: event for event in dfo.load_events(dfo.download(DFO_PATH)) if event.id in mapped}
        except dfo.DFOError as exc:
            raise CommandError(str(exc)) from exc
        keys = list(zip(data.counties, (dt.date.fromisoformat(date) for date in data.dates)))

        def rule(event, start, end):
            return in_window(event, start, end, built_with)

        return labels.mapped_labels(keys, placed, events, mapped, rule, min_km2)
