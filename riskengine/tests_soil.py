"""Soil moisture as a model input: the row, and the training set that carries it."""

import csv
import datetime as dt
import json
import math
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from riskengine.ml.features import FEATURE_NAMES, feature_row
from riskengine.ml.train import load_training_set, meta_path
from riskengine.tests_analytics import make_region

END = dt.date(2020, 4, 30)
ROW = dict(normal_30d=100.0, oni=0.5, dmi=0.1, terrain=None, area_km2=1000.0)


class FeatureRowTests(SimpleTestCase):
    def test_soil_moisture_is_the_day_before_the_window(self):
        soil = {END - dt.timedelta(days=30): 0.31, END: 0.40}
        row = feature_row({}, END, soil=soil, **ROW)
        self.assertAlmostEqual(row[FEATURE_NAMES.index("soil_moisture_start")], 0.31)

    def test_without_soil_moisture_it_is_missing_not_made_up(self):
        row = feature_row({}, END, **ROW)
        self.assertTrue(math.isnan(row[FEATURE_NAMES.index("soil_moisture_start")]))

    def test_a_model_trained_without_it_gets_the_row_it_expects(self):
        names = [name for name in FEATURE_NAMES if name != "soil_moisture_start"]
        self.assertEqual(len(feature_row({}, END, names=names, **ROW)), len(names))


class AddSoilMoistureTests(TestCase):
    def setUp(self):
        make_region("Kisumu")
        self.dir = Path(tempfile.mkdtemp())
        self.source = self.dir / "training_set.csv"
        with self.source.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["county", "date", "normal_30d", "rain_30d", "flood", "severity", "events"])
            writer.writerow(["Kisumu", "2020-04-30", "100", "150", "1", "1", "1"])
            writer.writerow(["Kisumu", "2020-05-31", "90", "20", "0", "0", "0"])
        meta_path(self.source).write_text(json.dumps({"rain_model": "era5"}))

    def _run(self, soil, out):
        with mock.patch(
            "geodata.services.earth_engine_rain.fetch_county_soil_moisture", return_value=soil
        ):
            call_command("add_soil_moisture", "--source", str(self.source), "--out", str(out), stdout=StringIO())

    def test_the_column_is_filled_and_the_source_left_alone(self):
        before = self.source.read_text()
        out = self.dir / "with_soil.csv"
        self._run({"Kisumu": {dt.date(2020, 3, 31): 0.35}}, out)

        self.assertEqual(self.source.read_text(), before)
        with out.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(list(rows[0])[-4:], ["soil_moisture_start", "flood", "severity", "events"])
        self.assertEqual(rows[0]["soil_moisture_start"], "0.35000")
        self.assertEqual(rows[1]["soil_moisture_start"], "nan")  # no value for 1 May
        meta = json.loads(meta_path(out).read_text())
        self.assertEqual(meta["rain_model"], "era5")
        self.assertEqual(meta["built_from"], "training_set.csv")

        data = load_training_set(out, first_year=2020)
        self.assertIn("soil_moisture_start", data.features)

    def test_it_will_not_overwrite_the_source(self):
        with self.assertRaises(CommandError):
            self._run({}, self.source)
