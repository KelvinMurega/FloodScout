"""Score the model with and without inputs that are under trial.

    manage.py compare_inputs rain_60d rain_90d

Compares the current inputs with the current inputs plus the ones named, on
held-out years, and prints both. Nothing is saved: the shipped model is not
touched. See riskengine/ml/experiments.py for how, and record the result in
the experiments log in riskengine/ml/README.md.

Needs data/processed/training_set.csv and the rainfall `build_training_set`
cached. Point rainfall only: a training set built with --rain-source
earth-engine has no daily point series to work the trial inputs out from.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from riskengine.ml import experiments, train
from riskengine.ml.features import CANDIDATE_FEATURES

from .build_training_set import RAINFALL_DIR, TRAINING_SET_PATH

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
            nargs="+",
            choices=CANDIDATE_FEATURES,
            metavar="INPUT",
            help=f"Inputs to add, from: {', '.join(CANDIDATE_FEATURES)}.",
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
        if data.meta.get("rain_source", "open-meteo-point") != "open-meteo-point":
            raise CommandError("The training set was not built on point rainfall.")
        try:
            rainfall = experiments.cached_rainfall(RAINFALL_DIR, sorted(set(data.counties)))
            result = experiments.compare(data, rainfall, options["inputs"], options["seeds"])
        except (FileNotFoundError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        before, after = result["before"], result["after"]
        self.stdout.write(
            f"{experiments.MODEL} on held-out years, mean (sd) over seeds {before['seeds']}; "
            f"a warning is above {100 * train.WARNING_THRESHOLD:.0f}%"
        )
        self.stdout.write(f"Added: {', '.join(result['added'])}")
        self.stdout.write("")
        self.stdout.write(f"  {'':<44} {'before':>18} {'after':>18}")
        for key, label, total in MEASURES:
            if total:
                label = f"{label} (of {before[total]})"
            cells = [
                f"{scores[key]['mean']:g} ({scores[key]['sd']:g})" for scores in (before, after)
            ]
            self.stdout.write(f"  {label:<44} {cells[0]:>18} {cells[1]:>18}")
        self.stdout.write("")
        verdict = "beyond" if result["beyond_noise"] else "within"
        self.stdout.write(
            f"Pooled average precision changed by {result['average_precision_change']:+.4f}, "
            f"{verdict} the noise between seeds ({experiments.NOISE})."
        )
