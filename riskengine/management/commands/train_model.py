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
        artifact, metrics = train.train(TRAINING_SET_PATH, oni, dmi)
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
        self.stdout.write("Risk bands on held-out years:")
        for band in metrics["band_calibration"]:
            observed = band["observed_flood_rate"]
            self.stdout.write(
                f"  {band['band']:<10} {band['rows']:>6} rows, flood recorded in "
                f"{'n/a' if observed is None else f'{100 * observed:.1f}%'}"
            )
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Saved {artifact['version']} to {train.ARTIFACT_PATH}"))
