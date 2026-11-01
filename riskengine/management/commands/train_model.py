"""Train the flood model on data/processed/training_set.csv.

Compares Logistic Regression, Random Forest and Gradient Boosting on
held-out years, searching each one's settings over a small grid, refits the
best on everything, and writes it to riskengine/ml/artifacts/ where
`infer()` picks it up. Run `build_training_set` first.

notebooks/train_flood_model.ipynb does the same thing on Google Colab,
through `python -m riskengine.ml.pipeline` and a committed config. The
shipped model comes from that notebook; this command is for checking a
change locally, and it overwrites riskengine/ml/artifacts/.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from geodata.services.climate_indices import ClimateIndexError, fetch_dmi, fetch_oni
from riskengine.ml import train

from .build_training_set import TRAINING_SET_PATH


class Command(BaseCommand):
    help = "Compare the three models on held-out years and save the best."

    def add_arguments(self, parser):
        # There is no final test option: the 2017 to 2021 test was closed on
        # 29 October 2026 (riskengine/ml/experiments.md) and is never scored again.
        parser.add_argument(
            "--no-tune",
            action="store_true",
            help="Skip the settings search and compare the models at their fixed settings.",
        )
        parser.add_argument(
            "--seeds",
            type=int,
            nargs="*",
            default=list(train.SEEDS),
            metavar="SEED",
            help=(
                "Seeds the cross-validation is repeated with, to show how much the scores "
                f"vary (default {' '.join(map(str, train.SEEDS))}). Give none to skip. The "
                f"saved model always uses seed {train.RANDOM_STATE}."
            ),
        )

    def handle(self, *args, **options):
        if not TRAINING_SET_PATH.exists():
            raise CommandError(
                f"{TRAINING_SET_PATH} not found — run `manage.py build_training_set` first."
            )
        try:
            oni = fetch_oni()
            dmi = fetch_dmi()
        except ClimateIndexError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write("Training (cross-validating three models, this takes several minutes)")
        artifact, metrics = train.train(
            TRAINING_SET_PATH,
            oni,
            dmi,
            test_from=None,
            tune_models=not options["no_tune"],
            seeds=tuple(options["seeds"]),
        )
        train.save(artifact, metrics)

        self.stdout.write("")
        self.stdout.write(
            f"{metrics['rows']} county-months, {metrics['positives']} with a recorded flood "
            f"({100 * metrics['base_rate']:.1f}%)"
        )
        self.stdout.write(f"Held-out years ({metrics['validation']}):")
        self.stdout.write(f"  {'model':<18} {'ROC AUC':>8} {'avg precision':>14} {'Brier':>8}")
        for name, scores in metrics["comparison"].items():
            self.stdout.write(
                f"  {name:<18} {scores['roc_auc']:>8.3f} {scores['average_precision']:>14.3f} "
                f"{scores['brier']:>8.4f}"
            )
        for name, scores in metrics["baselines"].items():
            self.stdout.write(
                f"  {'(' + name + ')':<18} {scores['roc_auc']:>8.3f} "
                f"{scores['average_precision']:>14.3f} {scores['brier']:>8.4f}"
            )
        self.stdout.write("")
        self.stdout.write(
            f"Selected {metrics['selected']}: average precision {metrics['lift_over_base_rate']}x "
            f"the base rate, {metrics['lift_over_season_only']}x the season-only model"
        )
        self.stdout.write(f"Calibration kept: {metrics['calibration']}")
        if metrics["tuning"]:
            self.stdout.write("")
            self.stdout.write("Settings search, average precision on held-out years:")
            for name, record in metrics["tuning"].items():
                self.stdout.write(
                    f"  {name:<18} fixed {record['fixed']['average_precision']:.4f}, "
                    f"searched {record['searched']['average_precision']:.4f} "
                    f"{record['searched_settings']} "
                    f"({'adopted' if record['adopted'] else 'fixed settings kept'})"
                )
        self.stdout.write("")
        self.stdout.write("Permutation importance (fall in held-out average precision):")
        ranked = sorted(
            metrics["permutation_importance"].items(), key=lambda item: -item[1]["mean"]
        )
        for name, value in ranked:
            self.stdout.write(f"  {name:<18} {value['mean']:>7.4f}")
        self.stdout.write("")
        self.stdout.write("Average precision by fold, mean (standard deviation):")
        for name, summary in metrics["fold_summary"].items():
            self.stdout.write(
                f"  {name:<18} {summary['average_precision_mean']:.3f} "
                f"({summary['average_precision_sd']:.3f})"
            )
        self.stdout.write("")
        self.stdout.write("As a warning, on held-out years:")
        for row in metrics["threshold_errors"]:
            self.stdout.write(
                f"  above {100 * row['threshold']:.0f}%: caught {row['true_positives']} floods, "
                f"missed {row['false_negatives']}, {row['false_positives']} false alarms "
                f"(recall {row['recall']:.3f}, precision {row['precision']:.3f})"
            )
        self.stdout.write("")
        self.stdout.write("Risk bands on held-out years:")
        for band in metrics["band_calibration"]:
            observed = band["observed_flood_rate"]
            self.stdout.write(
                f"  {band['band']:<10} {band['rows']:>6} rows, flood recorded in "
                f"{'n/a' if observed is None else f'{100 * observed:.1f}%'}"
            )
        variation = metrics["seed_variation"]
        if variation:
            self.stdout.write("")
            self.stdout.write(
                f"Average precision over seeds {variation['seeds']}, mean (standard deviation):"
            )
            for name in metrics["comparison"]:
                record = variation[name]
                if "note" in record:
                    self.stdout.write(f"  {name:<18} {record['note']}")
                else:
                    spread = record["average_precision"]
                    self.stdout.write(f"  {name:<18} {spread['mean']:.4f} ({spread['sd']})")
        self.stdout.write("")
        self.stdout.write("Average precision by share of the training years used:")
        for point in metrics["learning_curve"]:
            self.stdout.write(
                f"  {100 * point['fraction']:>3.0f}%  {point['average_precision_mean']} "
                f"({point['average_precision_sd']})"
            )
        compute = metrics["compute"]
        self.stdout.write("")
        self.stdout.write(
            f"Computing: {compute['cores']} cores, {compute['cpu']}; cross-validation "
            f"{compute['seconds_cross_validation']} s, final fit {compute['seconds_final_fit']} s"
        )
        test = metrics["final_test"]
        if test:
            first, last = test["test_years"]
            self.stdout.write("")
            self.stdout.write(
                f"Final test, {first} to {last} ({test['rows']} county-months, "
                f"{test['positives']} floods), model chosen on earlier years only:"
            )
            rows = [(test["selected"], test["scores"])]
            rows += [(f"({name})", scores) for name, scores in test["other_candidates"].items()]
            rows += [(f"({name})", scores) for name, scores in test["baselines"].items()]
            for name, scores in rows:
                self.stdout.write(
                    f"  {name:<20} {scores['roc_auc']:>8.3f} "
                    f"{scores['average_precision']:>14.3f} {scores['brier']:>8.4f}"
                )
            for band in test["band_calibration"]:
                observed = band["observed_flood_rate"]
                self.stdout.write(
                    f"  {band['band']:<10} {band['rows']:>6} rows, flood recorded in "
                    f"{'n/a' if observed is None else f'{100 * observed:.1f}%'}"
                )
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Saved {artifact['version']} to {train.ARTIFACT_PATH}"))
