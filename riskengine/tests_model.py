"""Tests for the accuracy changes to the flood model: training, features,
calibration, ocean indices, area-averaged rainfall and UNOSAT validation."""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

import numpy as np
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from geodata.models import Region
from geodata.services import earth_engine_rain
from geodata.services.climate_indices import dmi_for, parse_dmi
from riskengine.ml import train
from riskengine.ml.calibration import CalibratedModel, Calibrator
from riskengine.ml.features import FEATURE_NAMES, feature_row
from riskengine.ml.infer import load_model, model_infer, region_row

WINDOW_END = dt.date(2019, 11, 30)


def _county(name, x, y, size=1.0, area=None):
    square = Polygon.from_bbox((x, y, x + size, y + size))
    square.srid = 4326
    return Region.objects.create(
        name=name,
        county_name=name,
        geom=MultiPolygon(square, srid=4326),
        area_size=area or 12345.0,
    )


def write_training_csv(path: Path, *, columns=FEATURE_NAMES, first_year=1990, seed=0):
    """A small training set: floods follow rainfall, and none are recorded before 1996."""
    rng = np.random.default_rng(seed)
    counties = [f"C{i}" for i in range(6)]
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["county", "date", "normal_30d", *columns, "flood", "severity", "events"])
        for year in range(first_year, 2022):
            oni = rng.normal()
            for month in range(1, 13):
                for county in counties:
                    rain = rng.gamma(2, 50)
                    values = {
                        "rain_30d": rain,
                        "rain_7d_max": rain * 0.4,
                        "rain_prev_30d": rng.gamma(2, 50),
                        "rain_30d_ratio": 999.0,  # deliberately wrong: training must recompute it
                        "oni": oni,
                        "dmi": rng.normal(0, 0.5),
                        "oni_short_rains": oni if month >= 10 else 0.0,
                        "dmi_short_rains": 0.0,
                        "month_sin": math.sin(2 * math.pi * (month - 1) / 12),
                        "month_cos": math.cos(2 * math.pi * (month - 1) / 12),
                        "elevation": 500.0,
                        "slope": 2.0,
                        "log_area": 9.0,
                    }
                    flood = int(year >= 1996 and rain > 150 and rng.random() < 0.7)
                    writer.writerow(
                        [county, f"{year}-{month:02d}-28", 100.0]
                        + [values[name] for name in columns]
                        + [flood, flood, flood]
                    )


class TrainingTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "training_set.csv"
        write_training_csv(cls.path)
        cls.artifact, cls.metrics = train.train(cls.path, {(2019, 9): 1.0}, {(2019, 9): 0.5})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        super().tearDownClass()

    def test_rows_before_the_first_training_year_are_dropped(self):
        self.assertEqual(self.metrics["first_training_year"], 1996)
        self.assertTrue(self.metrics["period"][0].startswith("1996"))
        self.assertEqual(self.metrics["rows"], (2021 - 1996 + 1) * 12 * 6)

    def test_ratio_is_recomputed_not_read_from_the_csv(self):
        data = train.load_training_set(self.path)
        normals = train.county_normals(data, np.arange(len(data.y)))
        ratio = train.with_normals(data, normals)[:, data.column("rain_30d_ratio")]
        self.assertTrue(np.all(ratio < 50))  # the CSV said 999

    def test_fold_normals_use_only_the_training_rows(self):
        data = train.load_training_set(self.path)
        only_1996 = np.flatnonzero(data.years == 1996)
        normals = train.county_normals(data, only_1996)
        rain = data.X[:, data.column("rain_30d")]
        first = only_1996[0]
        expected = np.mean(
            [rain[i] for i in only_1996
             if data.counties[i] == data.counties[first] and data.months[i] == data.months[first]]
        )
        self.assertAlmostEqual(normals[data.counties[first]][int(data.months[first])], expected)

    def test_reports_both_baselines_and_beats_them(self):
        baselines = self.metrics["baselines"]
        self.assertEqual(set(baselines), {"base-rate", "season-only"})
        best = self.metrics["comparison"][self.metrics["selected"]]
        self.assertGreater(best["average_precision"], baselines["season-only"]["average_precision"])
        self.assertGreater(self.metrics["lift_over_base_rate"], 1)

    def test_calibration_choice_is_the_lowest_held_out_brier(self):
        scores = self.metrics["calibration_comparison"]
        self.assertEqual(set(scores), {"none", "sigmoid", "isotonic"})
        chosen = self.metrics["calibration"]
        self.assertEqual(scores[chosen]["brier"], min(s["brier"] for s in scores.values()))

    def test_artifact_records_features_indices_and_rain_sampling(self):
        self.assertEqual(self.artifact["features"], list(FEATURE_NAMES))
        self.assertEqual(self.artifact["dmi"], {(2019, 9): 0.5})
        self.assertEqual(self.artifact["rain_points"], 1)
        probabilities = self.artifact["pipeline"].predict_proba(np.zeros((3, len(FEATURE_NAMES))))
        self.assertEqual(probabilities.shape, (3, 2))
        self.assertTrue(np.all((probabilities >= 0) & (probabilities <= 1)))

    def test_an_older_csv_trains_and_gets_the_derived_inputs(self):
        older = [n for n in FEATURE_NAMES if n not in ("dmi", "dmi_short_rains", "oni_short_rains", "log_area")]
        path = Path(self.tmp.name) / "older.csv"
        write_training_csv(path, columns=older)
        data = train.load_training_set(path)
        self.assertIn("oni_short_rains", data.features)
        self.assertNotIn("log_area", data.features)
        october = np.flatnonzero(data.months == 10)[0]
        march = np.flatnonzero(data.months == 3)[0]
        self.assertEqual(data.X[october, data.column("oni_short_rains")], data.X[october, data.column("oni")])
        self.assertEqual(data.X[march, data.column("oni_short_rains")], 0.0)

    def test_metadata_next_to_the_csv_sets_rain_sampling(self):
        path = Path(self.tmp.name) / "ee.csv"
        write_training_csv(path, first_year=2010)
        train.meta_path(path).write_text(json.dumps({"rain_source": "earth-engine-county-mean", "rain_points": 5}))
        artifact, metrics = train.train(path, {}, {})
        self.assertEqual((artifact["rain_points"], metrics["rain_source"]), (5, "earth-engine-county-mean"))


class CalibrationTests(SimpleTestCase):
    def test_calibration_corrects_an_overconfident_model(self):
        rng = np.random.default_rng(0)
        truth = rng.uniform(0, 0.3, 5000)
        y = (rng.random(5000) < truth).astype(int)
        overconfident = np.clip(truth * 2, 0, 1)
        for method in ("sigmoid", "isotonic"):
            calibrated = Calibrator(method).fit(overconfident, y).predict(overconfident)
            self.assertLess(abs(calibrated.mean() - y.mean()), abs(overconfident.mean() - y.mean()))
            self.assertTrue(np.all((calibrated > 0) & (calibrated < 1)))

    def test_calibrated_model_keeps_the_ranking(self):
        class Raw:
            def predict_proba(self, X):
                p = np.asarray(X)[:, 0]
                return np.column_stack([1 - p, p])

        p = np.linspace(0.01, 0.9, 50)
        model = CalibratedModel(Raw(), Calibrator("sigmoid").fit(p, (p > 0.5).astype(int)))
        out = model.predict_proba(p.reshape(-1, 1))[:, 1]
        self.assertTrue(np.all(np.diff(out) >= 0))


class FeatureTests(SimpleTestCase):
    RAIN = {WINDOW_END - dt.timedelta(days=d): 3.0 for d in range(60)}

    def test_ocean_indices_count_in_the_short_rains_only(self):
        november = dict(zip(FEATURE_NAMES, feature_row(
            self.RAIN, WINDOW_END, normal_30d=90, oni=1.2, dmi=0.9, terrain=None)))
        april = dict(zip(FEATURE_NAMES, feature_row(
            self.RAIN, dt.date(2019, 4, 30), normal_30d=90, oni=1.2, dmi=0.9, terrain=None)))
        self.assertEqual((november["oni_short_rains"], november["dmi_short_rains"]), (1.2, 0.9))
        self.assertEqual((april["oni_short_rains"], april["dmi_short_rains"]), (0.0, 0.0))
        self.assertEqual(april["dmi"], 0.9)

    def test_area_is_logged_and_unknown_area_is_nan(self):
        row = dict(zip(FEATURE_NAMES, feature_row(self.RAIN, WINDOW_END, normal_30d=90, oni=0,
                                                  terrain=None, area_km2=math.e ** 3)))
        self.assertAlmostEqual(row["log_area"], 3.0)
        row = dict(zip(FEATURE_NAMES, feature_row(self.RAIN, WINDOW_END, normal_30d=90, oni=0, terrain=None)))
        self.assertTrue(math.isnan(row["log_area"]))

    def test_names_select_and_order_the_row(self):
        row = feature_row(self.RAIN, WINDOW_END, normal_30d=90, oni=0.5, terrain=None,
                          names=("oni", "rain_30d"))
        self.assertEqual(row, [0.5, 90.0])


class DmiTests(SimpleTestCase):
    TEXT = (
        " 1870 2026\n"
        "2019     0.1     0.2     0.3     0.4     0.5     0.6     0.7     0.8     0.9     1.0     1.1     1.2\n"
        "2020     0.0 -9999.000 -9999.000 -9999.000 -9999.000 -9999.000 -9999.000 -9999.000 -9999.000"
        " -9999.000 -9999.000 -9999.000\n"
        "  -9999\nDMI HadISST1.1\n"
    )

    def test_parses_months_and_skips_missing_values(self):
        table = parse_dmi(self.TEXT)
        self.assertEqual(table[(2019, 10)], 1.0)
        self.assertEqual(table[(2020, 1)], 0.0)
        self.assertNotIn((2020, 2), table)

    def test_lookup_uses_the_publication_lag_and_carries_forward(self):
        table = parse_dmi(self.TEXT)
        self.assertEqual(dmi_for(dt.date(2019, 12, 31), table), 1.0)  # October's value
        self.assertEqual(dmi_for(dt.date(2020, 9, 30), table), 0.0)  # newest available


class ModelCompatibilityTests(TestCase):
    """The model shipped before this change must still score after it."""

    def test_the_shipped_model_scores_a_region(self):
        model = load_model()
        if model is None:
            self.skipTest("No model artifact on disk")
        region = _county("Shipped", 36.0, -1.0)
        value, category = model_infer(model, region, WINDOW_END)
        self.assertTrue(0 <= value <= 1)
        self.assertEqual(len(region_row(model, region, {}, WINDOW_END)), len(model["features"]))

    def test_a_sub_county_is_scored_with_its_county_area(self):
        county = _county("Big", 36.0, -1.0, area=40000.0)
        sub = Region.objects.create(
            name="Small", county_name="Big", level=Region.Level.SUB_COUNTY, parent=county,
            geom=county.geom, area_size=300.0,
        )
        model = {"features": ["log_area"], "normals": {}, "oni": {}, "dmi": {}}
        self.assertAlmostEqual(region_row(model, sub, {}, WINDOW_END)[0], math.log(40000.0))


class EarthEngineRainTests(SimpleTestCase):
    def test_quarters_cover_the_range_without_gaps(self):
        spans = earth_engine_rain.quarters(dt.date(1995, 11, 2), dt.date(1996, 7, 5))
        self.assertEqual(spans[0], (dt.date(1995, 11, 2), dt.date(1995, 12, 31)))
        self.assertEqual(spans[-1], (dt.date(1996, 7, 1), dt.date(1996, 7, 5)))
        for (_a, end), (start, _b) in zip(spans, spans[1:]):
            self.assertEqual(start - end, dt.timedelta(days=1))

    def test_metres_become_millimetres_and_nulls_are_dropped(self):
        parsed = earth_engine_rain.parse_features([
            {"properties": {"county": "Kisumu", "date": "2019-11-01", "mean": 0.0123}},
            {"properties": {"county": "Kisumu", "date": "2019-11-02", "mean": None}},
        ])
        self.assertAlmostEqual(parsed["Kisumu"][dt.date(2019, 11, 1)], 12.3)
        self.assertNotIn(dt.date(2019, 11, 2), parsed["Kisumu"])


class ValidateUnosatTests(TestCase):
    def test_scores_only_covered_counties_against_the_extent(self):
        _county("Wetland", 36.0, -1.0)
        _county("Dryland", 38.0, -1.0)
        _county("Elsewhere", 40.0, -1.0)  # not in `covers`, so not scored

        with tempfile.TemporaryDirectory() as tmp:
            extent = Path(tmp) / "water.geojson"
            # A 0.2 x 0.2 degree patch (about 490 km²) inside Wetland only.
            extent.write_text(json.dumps({
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {}, "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[36.4, -0.6], [36.6, -0.6], [36.6, -0.4], [36.4, -0.4], [36.4, -0.6]]],
                }}],
            }))
            manifest = Path(tmp) / "events.json"
            manifest.write_text(json.dumps([{
                "name": "Test event", "date": "2024-05-05", "extent": str(extent),
                "covers": ["Wetland", "Dryland"],
            }]))
            output = Path(tmp) / "out.csv"
            wet_rain = {dt.date(2024, 5, 5) - dt.timedelta(days=d): 20.0 for d in range(61)}
            dry_rain = {dt.date(2024, 5, 5) - dt.timedelta(days=d): 0.0 for d in range(61)}
            stdout = StringIO()
            with mock.patch(
                "riskengine.management.commands.validate_unosat.county_rainfall",
                return_value=({"Wetland": wet_rain, "Dryland": dry_rain}, 1),
            ), mock.patch(
                "riskengine.management.commands.validate_unosat.fetch_oni", return_value={}
            ), mock.patch(
                "riskengine.management.commands.validate_unosat.fetch_dmi", return_value={}
            ), mock.patch(
                "riskengine.management.commands.validate_unosat.OUTPUT_PATH", output
            ):
                call_command("validate_unosat", str(manifest), stdout=stdout)

            with output.open() as handle:
                rows = {row["county"]: row for row in csv.DictReader(handle)}

        self.assertEqual(set(rows), {"Wetland", "Dryland"})
        self.assertEqual(rows["Wetland"]["flooded"], "1")
        self.assertGreater(float(rows["Wetland"]["flooded_km2"]), 400)
        self.assertEqual(rows["Dryland"]["flooded"], "0")
        self.assertIn("1/2 flooded", stdout.getvalue())


class OutlookAveragingTests(SimpleTestCase):
    def test_members_are_averaged_index_by_index_across_points(self):
        from riskengine.management.commands.flood_outlook import _per_region

        day = dt.date(2026, 11, 1)
        observed = [{day: 2.0}, {day: 4.0}, {day: 9.0}]
        forecast = [
            [{day: 10.0}, {day: 20.0}],
            [{day: 30.0}, {day: 40.0}],
            [{day: 5.0}, {day: 6.0}],
        ]
        region_observed, region_forecast = _per_region([["a", "b"], ["c"]], observed, forecast)
        self.assertEqual(region_observed, [{day: 3.0}, {day: 9.0}])
        self.assertEqual(region_forecast, [[{day: 20.0}, {day: 30.0}], [{day: 5.0}, {day: 6.0}]])
