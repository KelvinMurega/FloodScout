"""Write a copy of the training set with `soil_moisture_start` filled in.

    manage.py add_soil_moisture --out data/processed/training_set_era5_soil_20261115.csv

`build_training_set` leaves the soil column empty: its rainfall comes from
Open-Meteo, and soil moisture from Earth Engine (ERA5-Land averaged over
each county), which needs an account. This reads the soil moisture
`compare_inputs soil_moisture_start` cached in data/raw/soil_moisture_ee/
(Earth Engine only for a quarter that is missing) and writes every row of
the training set again with it, so the trial that kept it and the training
run read the same numbers. The source is left untouched.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from geodata.models import Region
from geodata.services import earth_engine_rain
from riskengine.management.commands.build_training_set import TRAINING_SET_PATH
from riskengine.management.commands.compare_inputs import EE_SOIL_DIR
from riskengine.ml.features import WINDOW_DAYS, soil_features
from riskengine.ml.train import meta_path

COLUMN = "soil_moisture_start"


class Command(BaseCommand):
    help = "Copy the training set with soil_moisture_start filled from the cached ERA5-Land soil moisture."

    def add_arguments(self, parser):
        parser.add_argument("--source", type=Path, default=TRAINING_SET_PATH)
        parser.add_argument("--out", type=Path, required=True)

    def handle(self, *args, **options):
        source, out = options["source"], options["out"]
        if not source.is_file():
            raise CommandError(f"{source} not found.")
        if out.resolve() == source.resolve():
            raise CommandError("--out must be a new file; the source is left as it is.")
        with source.open(newline="") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or ())
            rows = list(reader)
        if not rows:
            raise CommandError(f"{source} has no rows.")
        if COLUMN not in fields:
            # Beside the other inputs, before the label columns.
            fields.insert(fields.index("flood"), COLUMN)

        dates = [dt.date.fromisoformat(row["date"]) for row in rows]
        counties = list(Region.objects.filter(level=Region.Level.COUNTY).order_by("name"))
        try:
            soil = earth_engine_rain.fetch_county_soil_moisture(
                counties, min(dates) - dt.timedelta(days=WINDOW_DAYS), max(dates), EE_SOIL_DIR,
                log=self.stdout.write,
            )
        except earth_engine_rain.EarthEngineError as exc:
            raise CommandError(str(exc)) from exc

        filled = 0
        for row, end in zip(rows, dates):
            value = soil_features(soil.get(row["county"], {}), end)[COLUMN]
            row[COLUMN] = "nan" if value != value else f"{value:.5f}"
            filled += value == value

        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        meta = json.loads(meta_path(source).read_text()) if meta_path(source).exists() else {}
        meta.update(
            {
                "soil_source": "ERA5-Land, top 28 cm, county mean (Earth Engine)",
                "built_from": source.name,
                "built": dt.date.today().isoformat(),
            }
        )
        meta_path(out).write_text(json.dumps(meta, indent=2))
        self.stdout.write(
            self.style.SUCCESS(f"Wrote {len(rows)} rows to {out}, {filled} with soil moisture.")
        )
