"""Write each county's ordinary flood chance per month, for `times_season`.

    manage.py build_season_normals

Reads data/processed/training_set.csv and the model on disk, and writes
riskengine/ml/artifacts/season_normals.json (see riskengine/season.py).
Run it again after every retrain: the table is ignored under any other model
version.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from riskengine import season
from riskengine.management.commands.build_training_set import TRAINING_SET_PATH
from riskengine.ml import infer as scorer
from riskengine.ml import train


class Command(BaseCommand):
    help = "Write each county's flood chance in an ordinary year, per month, for the season colour."

    def handle(self, *args, **options):
        if scorer._MODEL is None:
            raise CommandError("No trained model on disk.")
        if not TRAINING_SET_PATH.exists():
            raise CommandError(f"{TRAINING_SET_PATH} not found — run `manage.py build_training_set` first.")
        data = train.load_training_set(TRAINING_SET_PATH)
        try:
            table = season.build(data, scorer._MODEL, train.FINAL_TEST_FROM)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        season.SEASON_NORMALS_PATH.write_text(json.dumps(table, indent=1, sort_keys=True))
        chances = [c for months in table["counties"].values() for c in months.values()]
        self.stdout.write(
            f"Wrote {season.SEASON_NORMALS_PATH.name} for {table['model_version']}: "
            f"{len(table['counties'])} counties, ordinary chance {min(chances):.3f} to {max(chances):.3f}"
        )
