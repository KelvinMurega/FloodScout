"""Train the flood model on data/processed/training_set.csv.

Compares Logistic Regression, Random Forest and Gradient Boosting on
held-out years, refits the best on everything, and writes it to
riskengine/ml/artifacts/ where `infer()` picks it up. Run
`build_training_set` first.

notebooks/train_flood_model.ipynb does the same thing on Google Colab, by
calling the same `riskengine.ml.train`.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from geodata.services.climate_indices import ClimateIndexError, fetch_dmi, fetch_oni
from riskengine.ml import train

from .build_training_set import TRAINING_SET_PATH


class Command(BaseCommand):
    help = "Compare the three models on held-out years and save the best."

    def add_arguments(self, parser):
        parser.add_argument(
            "--test-from",
            type=int,
            default=train.FINAL_TEST_FROM,
            metavar="YEAR",
            help=(
                "Years from this one on are set aside as the final test and scored once "
                f"(default {train.FINAL_TEST_FROM}). 0 skips the final test."
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

        self.stdout.write("Training (cross-validating three models, this takes a few minutes)")
        artifact, metrics = train.train(
            TRAINING_SET_PATH, oni, dmi, test_from=options["test_from"] or None
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
