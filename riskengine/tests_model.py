"""Tests for the accuracy changes to the flood model: training, features,
calibration, ocean indices, area-averaged rainfall and UNOSAT validation."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import math
import subprocess
import tarfile
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

import numpy as np
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from sklearn.base import clone
from sklearn.model_selection import GroupKFold

from geodata.models import Region
from geodata.services import earth_engine_rain
from geodata.services.climate_indices import dmi_for, parse_dmi
from riskengine.ml import experiments, train
from riskengine.ml.calibration import CalibratedModel, Calibrator
from riskengine.ml.features import CANDIDATE_FEATURES, FEATURE_NAMES, feature_row, rainfall_features
from riskengine.ml.infer import ModelIntegrityError, load_model, model_infer, region_row

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
        # The settings search has its own tests below; skipping it here keeps
        # this class quick.
        cls.artifact, cls.metrics = train.train(
            cls.path,
            {(2019, 9): 1.0},
            {(2019, 9): 0.5},
            # Synthetic data, so the closed final test's code can still be tested.
            test_from=train.FINAL_TEST_FROM,
            tune_models=False,
            seeds=(0, 7),
        )

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

    def test_every_fold_reports_its_years_and_each_model_s_scores(self):
        folds = self.metrics["folds"]
        self.assertEqual(len(folds), train.CV_FOLDS)
        self.assertEqual(sum(fold["rows"] for fold in folds), self.metrics["rows"])
        self.assertEqual(sum(fold["positives"] for fold in folds), self.metrics["positives"])
        years = [year for fold in folds for year in fold["years"]]
        self.assertEqual(sorted(years), list(range(1996, 2022)))  # each year held out once
        models = {"logreg", "random-forest", "gradient-boosting", "season-only"}
        self.assertEqual(set(folds[0]["scores"]), models)
        self.assertEqual(set(self.metrics["fold_summary"]), models)
        selected = self.metrics["selected"]
        values = [fold["scores"][selected]["average_precision"] for fold in folds]
        self.assertAlmostEqual(
            self.metrics["fold_summary"][selected]["average_precision_mean"],
            np.mean(values),
            places=3,
        )

    def test_threshold_errors_add_up_and_recall_falls_as_the_bar_rises(self):
        rows = self.metrics["threshold_errors"]
        self.assertEqual([row["threshold"] for row in rows], list(train.PROBABILITY_BANDS))
        for row in rows:
            self.assertEqual(
                row["true_positives"] + row["false_negatives"], self.metrics["positives"]
            )
            total = sum(row[key] for key in row if key.startswith(("true_", "false_")))
            self.assertEqual(total, self.metrics["rows"])
        recalls = [row["recall"] for row in rows]
        self.assertEqual(recalls, sorted(recalls, reverse=True))

    def test_threshold_rows_carry_accuracy_f1_and_balanced_accuracy(self):
        for row in self.metrics["threshold_errors"] + self.metrics["final_test"]["threshold_errors"]:
            tp, fp = row["true_positives"], row["false_positives"]
            fn, tn = row["false_negatives"], row["true_negatives"]
            self.assertEqual(row["support_positive"], tp + fn)
            self.assertEqual(row["support_negative"], tn + fp)
            self.assertAlmostEqual(row["accuracy"], (tp + tn) / (tp + fp + fn + tn), places=4)
            self.assertAlmostEqual(row["f1"], 2 * tp / (2 * tp + fp + fn), places=4)
            self.assertAlmostEqual(
                row["balanced_accuracy"], (row["recall"] + row["specificity"]) / 2, places=3
            )

    def test_seed_variation_reports_each_seed_and_seed_0_is_the_reported_score(self):
        variation = self.metrics["seed_variation"]
        self.assertEqual(variation["seeds"], [0, 7])
        self.assertEqual(variation["logreg"], {"note": "deterministic"})
        for name in ("random-forest", "gradient-boosting"):
            for metric in ("average_precision", "roc_auc"):
                record = variation[name][metric]
                self.assertEqual(len(record["values"]), 2)
                # Seed 0 is the seed of the comparison, so it must give the same score.
                self.assertEqual(record["values"][0], self.metrics["comparison"][name][metric])
                self.assertAlmostEqual(record["mean"], np.mean(record["values"]), places=3)
                self.assertIsNotNone(record["sd"])

    def test_seed_variation_is_skipped_without_seeds(self):
        data = train.load_training_set(self.path)
        chosen = train.select(data, tune_models=False)
        _artifact, metrics = train.build(
            data, chosen, self.path, {}, test_from=None, tune_models=False, seeds=()
        )
        self.assertIsNone(metrics["seed_variation"])
        self.assertEqual(metrics["comparison"], self.metrics["comparison"])

    def test_learning_curve_ends_at_the_cross_validated_score(self):
        curve = self.metrics["learning_curve"]
        self.assertEqual([point["fraction"] for point in curve], list(train.LEARNING_CURVE_FRACTIONS))
        years = [point["training_years_mean"] for point in curve]
        self.assertEqual(years, sorted(years))
        # With every training year, it is the cross-validation already reported.
        selected = self.metrics["selected"]
        self.assertAlmostEqual(
            curve[-1]["average_precision_mean"],
            self.metrics["fold_summary"][selected]["average_precision_mean"],
            places=3,
        )
        self.assertEqual(curve[-1]["folds"], train.CV_FOLDS)

    def test_learning_curve_samples_whole_years_and_nests_them(self):
        years = np.repeat(np.arange(2000, 2010), 12)
        order = np.random.default_rng(0).permutation(np.unique(years))
        quarter = train.sample_years(years, 0.25, order)
        half = train.sample_years(years, 0.5, order)
        self.assertEqual(len(set(years[quarter])), 3)  # a quarter of ten years, rounded up
        self.assertEqual(len(set(years[half])), 5)
        for year in np.unique(years):
            self.assertIn(quarter[years == year].sum(), (0, 12))  # all of a year or none of it
        self.assertTrue(set(years[quarter]) <= set(years[half]))
        self.assertTrue(train.sample_years(years, 1.0, order).all())
        self.assertEqual(len(set(years[train.sample_years(years, 0.01, order)])), 1)

    def test_compute_records_the_time_and_the_machine(self):
        compute = self.metrics["compute"]
        for stage in ("cross_validation", "final_fit", "seed_variation", "learning_curve"):
            self.assertGreaterEqual(compute[f"seconds_{stage}"], 0)
        self.assertGreaterEqual(compute["cores"], 1)
        self.assertTrue(compute["cpu"])

    def test_provenance_ties_the_result_to_its_data_and_seed(self):
        recorded = self.metrics["provenance"]
        self.assertEqual(
            recorded["training_set_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest()
        )
        self.assertEqual(recorded["random_state"], train.RANDOM_STATE)
        self.assertIn("git_commit", recorded)

    def test_git_commit_follows_a_branch_ref_and_is_none_outside_a_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch.dict("os.environ", {train.COMMIT_ENV: ""}):
                self.assertIsNone(train.git_commit(root))
            (root / ".git" / "refs" / "heads").mkdir(parents=True)
            (root / ".git" / "HEAD").write_text("ref: refs/heads/topic\n")
            (root / ".git" / "refs" / "heads" / "topic").write_text("abc123\n")
            self.assertEqual(train.git_commit(root), "abc123")
            (root / ".git" / "refs" / "heads" / "topic").unlink()
            (root / ".git" / "packed-refs").write_text("# pack-refs\ndef456 refs/heads/topic\n")
            self.assertEqual(train.git_commit(root), "def456")

    def test_git_commit_falls_back_to_the_environment_variable(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict("os.environ", {train.COMMIT_ENV: " fromenv1 \n"}):
                self.assertEqual(train.git_commit(Path(folder)), "fromenv1")

    def test_git_commit_falls_back_to_the_commit_file_in_the_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch.dict("os.environ", {train.COMMIT_ENV: ""}):
                (root / train.COMMIT_FILE).write_text("fromfile2\n")
                self.assertEqual(train.git_commit(root), "fromfile2")
                (root / train.COMMIT_FILE).write_text("\n")
                self.assertIsNone(train.git_commit(root))

    def test_a_real_checkout_wins_over_the_variable_and_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".git").mkdir()
            (root / ".git" / "HEAD").write_text("checkout3\n")
            (root / train.COMMIT_FILE).write_text("fromfile2\n")
            with mock.patch.dict("os.environ", {train.COMMIT_ENV: "fromenv1"}):
                self.assertEqual(train.git_commit(root), "checkout3")

    def test_provenance_records_the_run_id_when_one_is_set(self):
        with mock.patch.dict("os.environ", {train.RUN_ID_ENV: "20261007T1200Z_abc1234"}):
            self.assertEqual(train.provenance(self.path)["run_id"], "20261007T1200Z_abc1234")
        with mock.patch.dict("os.environ", {train.RUN_ID_ENV: ""}):
            self.assertIsNone(train.provenance(self.path)["run_id"])

    def test_final_test_scores_only_years_the_choice_never_saw(self):
        test = self.metrics["final_test"]
        self.assertEqual(test["test_from"], train.FINAL_TEST_FROM)
        self.assertEqual(test["development_years"], [1996, train.FINAL_TEST_FROM - 1])
        self.assertEqual(test["test_years"], [train.FINAL_TEST_FROM, 2021])
        self.assertEqual(test["rows"], (2021 - train.FINAL_TEST_FROM + 1) * 12 * 6)
        self.assertEqual(test["development_rows"] + test["rows"], self.metrics["rows"])
        self.assertIn(test["selected"], test["development_comparison"])
        self.assertNotIn(test["selected"], test["other_candidates"])
        self.assertEqual(sum(band["rows"] for band in test["band_calibration"]), test["rows"])
        # Floods follow rainfall in this data, so a real model beats the calendar.
        self.assertGreater(
            test["scores"]["average_precision"],
            test["baselines"]["season-only"]["average_precision"],
        )

    def test_final_test_normals_come_from_the_development_years(self):
        data = train.load_training_set(self.path)
        rain = data.column("rain_30d")
        late = data.years >= train.FINAL_TEST_FROM
        wetter = train.TrainingSet(
            features=data.features,
            X=data.X.copy(),
            y=data.y,
            years=data.years,
            months=data.months,
            counties=data.counties,
            dates=data.dates,
            meta=data.meta,
        )
        # Changing only how the test years' rain compares with normal must not
        # move the development side of the result at all.
        wetter.X[late, rain] *= 3
        before = train.final_test(data, train.FINAL_TEST_FROM, tune_models=False)
        after = train.final_test(wetter, train.FINAL_TEST_FROM, tune_models=False)
        self.assertEqual(before["development_comparison"], after["development_comparison"])
        self.assertEqual(before["selected"], after["selected"])

    def test_final_test_is_skipped_when_a_side_is_empty(self):
        data = train.load_training_set(self.path)
        self.assertIsNone(train.final_test(data, 1996))
        self.assertIsNone(train.final_test(data, 2030))

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
        artifact, metrics = train.train(path, {}, {}, tune_models=False)
        self.assertEqual((artifact["rain_points"], metrics["rain_source"]), (5, "earth-engine-county-mean"))

    def test_error_breakdown_accounts_for_every_flood_by_month_and_by_county(self):
        breakdown = self.metrics["error_breakdown"]
        at_threshold = next(
            row for row in self.metrics["threshold_errors"] if row["threshold"] == breakdown["threshold"]
        )
        self.assertEqual([row["month"] for row in breakdown["by_month"]], list(range(1, 13)))
        self.assertEqual(len(breakdown["by_county"]), 6)
        for groups in (breakdown["by_month"], breakdown["by_county"]):
            self.assertEqual(sum(row["rows"] for row in groups), self.metrics["rows"])
            self.assertEqual(sum(row["caught"] for row in groups), at_threshold["true_positives"])
            self.assertEqual(sum(row["missed"] for row in groups), at_threshold["false_negatives"])
            self.assertEqual(sum(row["false_alarms"] for row in groups), at_threshold["false_positives"])

    def test_permutation_importance_finds_the_input_the_floods_follow(self):
        importance = self.metrics["permutation_importance"]
        self.assertEqual(set(importance), set(FEATURE_NAMES))
        # Floods in this data depend on rain_30d alone, and elevation never varies.
        # The three inputs that carry it share the credit.
        carries_rain = ("rain_30d", "rain_7d_max", "rain_30d_ratio")
        rain = max(importance[name]["mean"] for name in carries_rain)
        others = max(importance[name]["mean"] for name in FEATURE_NAMES if name not in carries_rain)
        self.assertGreater(rain, 0)
        self.assertGreater(rain, others)
        self.assertEqual(importance["elevation"]["mean"], 0.0)


SMALL_GRIDS = {
    "logreg": {"logisticregression__C": [0.01, 1.0]},
    "random-forest": {"randomforestclassifier__min_samples_leaf": [20, 50]},
    "gradient-boosting": {"gradientboostingclassifier__max_depth": [1, 3]},
}


class TuningTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "training_set.csv"
            write_training_csv(path, first_year=2004)
            cls.data = train.load_training_set(path)
        with mock.patch.dict(train.PARAM_GRIDS, SMALL_GRIDS):
            cls.chosen = train.select(cls.data)

    def test_every_grid_contains_the_fixed_settings(self):
        for name, pipeline in train.candidates().items():
            current = pipeline.get_params()
            for parameter, values in train.PARAM_GRIDS[name].items():
                self.assertIn(current[parameter], values, f"{name} {parameter}")

    def test_each_candidate_reports_its_search(self):
        self.assertEqual(set(self.chosen.tuning), set(train.candidates()))
        for record in self.chosen.tuning.values():
            self.assertEqual(set(record["searched_settings"]), set(record["fixed_settings"]))
            for parameter, value in record["searched_settings"].items():
                self.assertIn(value, record["grid"][parameter])

    def test_searched_settings_are_kept_only_when_they_score_higher(self):
        for name, record in self.chosen.tuning.items():
            better = record["searched"]["average_precision"] > record["fixed"]["average_precision"]
            self.assertEqual(record["adopted"], better, name)
            pipeline = self.chosen.pipelines[name]
            kept = pipeline[-1].get_params()
            settings = record["searched_settings"] if record["adopted"] else record["fixed_settings"]
            for parameter, value in settings.items():
                self.assertEqual(kept[parameter], value)
            self.assertEqual(
                self.chosen.comparison[name], record["searched" if record["adopted"] else "fixed"]
            )

    def test_without_the_search_the_fixed_settings_are_used(self):
        chosen = train.select(self.data, tune_models=False)
        self.assertEqual(chosen.tuning, {})
        for name, pipeline in train.candidates().items():
            self.assertEqual(
                repr(chosen.pipelines[name][-1].get_params()), repr(pipeline[-1].get_params())
            )


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


class ModelIntegrityTests(SimpleTestCase):
    """A model file is unpickled only when its recorded SHA-256 matches."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.model = self.folder / "flood_model.joblib"
        self.metrics = self.folder / "metrics.json"

    def save(self):
        with (
            mock.patch.object(train, "ARTIFACT_DIR", self.folder),
            mock.patch.object(train, "ARTIFACT_PATH", self.model),
            mock.patch.object(train, "METRICS_PATH", self.metrics),
        ):
            train.save({"version": "test-model"}, {"base_rate": 0.1})

    def test_a_saved_model_records_its_hash_and_loads(self):
        self.save()
        recorded = json.loads(self.metrics.read_text())["model_sha256"]
        self.assertEqual(recorded, hashlib.sha256(self.model.read_bytes()).hexdigest())
        self.assertEqual(load_model(self.model)["version"], "test-model")

    def test_a_swapped_model_file_is_refused(self):
        self.save()
        self.model.write_bytes(self.model.read_bytes() + b"\0")
        with self.assertRaises(ModelIntegrityError):
            load_model(self.model)

    def test_a_model_with_no_recorded_hash_is_refused(self):
        self.save()
        self.metrics.write_text(json.dumps({"version": "test-model"}))
        with self.assertRaises(ModelIntegrityError):
            load_model(self.model)

    def test_the_shipped_metrics_match_the_shipped_model(self):
        shipped = train.ARTIFACT_PATH
        if not shipped.exists():
            self.skipTest("No model artifact on disk")
        self.assertIsNotNone(load_model(shipped))


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


class LoadExtentTests(SimpleTestCase):
    def test_a_list_of_layers_is_merged_into_one_extent(self):
        from riskengine.management.commands.validate_unosat import load_extent

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "water.geojson"
            path.write_text(json.dumps({
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {}, "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[36.4, -0.6], [36.6, -0.6], [36.6, -0.4], [36.4, -0.4], [36.4, -0.6]]],
                }}],
            }))
            one = load_extent(path, 0)
            both = load_extent(path, [0, 0])  # the same layer twice adds no area

        self.assertAlmostEqual(one.area, 0.04, places=6)
        self.assertAlmostEqual(both.area, one.area, places=6)


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


class ValidateUnosatSubCountyTests(TestCase):
    def test_sub_counties_are_scored_and_ranked_within_their_county(self):
        county = _county("Wetland", 36.0, -1.0)
        for name, x in (("West", 36.0), ("East", 36.5)):
            half = Polygon.from_bbox((x, -1.0, x + 0.5, 0.0))
            half.srid = 4326
            Region.objects.create(
                name=name,
                county_name="Wetland",
                level=Region.Level.SUB_COUNTY,
                parent=county,
                geom=MultiPolygon(half, srid=4326),
                area_size=6000.0,
            )

        with tempfile.TemporaryDirectory() as tmp:
            extent = Path(tmp) / "water.geojson"
            # About 490 km² of water, all of it in West.
            extent.write_text(json.dumps({
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {}, "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[36.1, -0.6], [36.3, -0.6], [36.3, -0.4], [36.1, -0.4], [36.1, -0.6]]],
                }}],
            }))
            manifest = Path(tmp) / "events.json"
            manifest.write_text(json.dumps([{
                "name": "Test event", "date": "2024-05-05", "extent": str(extent), "covers": ["Wetland"],
            }]))
            output = Path(tmp) / "out.csv"
            wet_rain = {dt.date(2024, 5, 5) - dt.timedelta(days=d): 20.0 for d in range(61)}
            dry_rain = {dt.date(2024, 5, 5) - dt.timedelta(days=d): 0.0 for d in range(61)}
            stdout = StringIO()
            commands = "riskengine.management.commands.validate_unosat"
            with mock.patch(
                f"{commands}.county_rainfall", return_value=({"West": wet_rain, "East": dry_rain}, 1)
            ) as rainfall, mock.patch(f"{commands}.fetch_oni", return_value={}), mock.patch(
                f"{commands}.fetch_dmi", return_value={}
            ), mock.patch(f"{commands}.SUB_COUNTY_OUTPUT_PATH", output):
                call_command("validate_unosat", str(manifest), "--level", "sub_county", stdout=stdout)

            with output.open() as handle:
                rows = {row["sub_county"]: row for row in csv.DictReader(handle)}

        # `covers` names the county; its sub-counties are what is scored.
        self.assertEqual(set(rows), {"West", "East"})
        self.assertEqual({row["county"] for row in rows.values()}, {"Wetland"})
        self.assertEqual((rows["West"]["flooded"], rows["East"]["flooded"]), ("1", "0"))
        # Sub-county rainfall is cached apart from county rainfall.
        self.assertEqual(rainfall.call_args.kwargs["cache_dir"].name, "sub_county")
        self.assertIn("Ranked within their own county", stdout.getvalue())

    def test_within_county_ranking_skips_counties_that_cannot_be_ranked(self):
        from riskengine.management.commands.validate_unosat import within_county_auc

        def row(county, flooded, probability):
            return {"event": "E", "county": county, "flooded": flooded, "probability": probability}

        results = [
            row("A", 1, 0.9), row("A", 0, 0.1),  # ranked correctly: 1.0
            row("B", 1, 0.2), row("B", 0, 0.6),  # ranked backwards: 0.0
            row("C", 0, 0.5), row("C", 0, 0.4),  # nothing flooded: left out
        ]
        self.assertEqual(within_county_auc(results), (0.5, 2))
        self.assertIsNone(within_county_auc(results[4:]))

    def test_a_slash_in_a_name_does_not_break_the_rainfall_cache(self):
        from riskengine.management.commands.build_training_set import open_meteo_rainfall

        region = _county("Sigowet/Soin", 35.0, -1.0)
        day = dt.date(2024, 4, 1)
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "riskengine.management.commands.build_training_set.fetch_archive_rainfall",
            return_value=[{day: 4.0}],
        ):
            cache = Path(tmp) / "sub_county"
            fetched = open_meteo_rainfall([region], day, day, log=lambda _: None, cache_dir=cache)
            again = open_meteo_rainfall([region], day, day, log=lambda _: None, cache_dir=cache)
            self.assertEqual(
                [path.name for path in (cache / "era5").iterdir()], [f"Sigowet-Soin_{day}_{day}.json"]
            )
        self.assertEqual(fetched, {"Sigowet/Soin": {day: 4.0}})
        self.assertEqual(again, fetched)  # read back from the cache

    def test_the_rainfall_model_reaches_the_request_and_the_cache(self):
        from riskengine.management.commands.build_training_set import open_meteo_rainfall

        region = _county("Wetland", 35.0, -1.0)
        day = dt.date(2018, 4, 1)
        name = f"Wetland_{day}_{day}.json"
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "riskengine.management.commands.build_training_set.fetch_archive_rainfall",
            side_effect=[[{day: 4.0}], [{day: 9.0}]],
        ) as fetch:
            cache = Path(tmp)
            era5 = open_meteo_rainfall([region], day, day, log=lambda _: None, cache_dir=cache)
            # Rainfall the service chose is not read back as ERA5, nor ERA5 as it.
            chosen = open_meteo_rainfall(
                [region], day, day, log=lambda _: None, cache_dir=cache, model="best_match"
            )
            self.assertTrue((cache / "era5" / name).exists())
            self.assertTrue((cache / name).exists())

        self.assertEqual([call.args[3] for call in fetch.call_args_list], ["era5", "best_match"])
        self.assertEqual((era5["Wetland"][day], chosen["Wetland"][day]), (4.0, 9.0))

    def test_a_training_set_without_a_rainfall_model_was_the_services_choice(self):
        from riskengine.management.commands.build_training_set import rain_model_of

        self.assertEqual(rain_model_of({"rain_source": "open-meteo-point"}), "best_match")
        self.assertEqual(rain_model_of({"rain_model": "era5"}), "era5")


class BuildColabBundleTests(SimpleTestCase):
    """The bundle is the committed code plus the training set, tied to one commit."""

    COMMIT = "0123456789abcdef0123456789abcdef01234567"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.training_set = self.root / "data" / "processed" / "training_set.csv"
        self.training_set.parent.mkdir(parents=True)
        self.training_set.write_bytes(b"date,county,flood\n2019-11-30,Wetland,1\n")
        train.meta_path(self.training_set).write_text('{"rain_source": "test"}')

    def _git(self, status=b""):
        """Stands in for git: a clean checkout of COMMIT holding three files."""
        code = io.BytesIO()
        with tarfile.open(fileobj=code, mode="w") as archive:
            for name in (
                "riskengine/ml/train.py",
                "riskengine/ml/artifacts/flood_model.joblib",
                "riskengine/ml/artifacts/metrics.json",
                "requirements.txt",
            ):
                info = tarfile.TarInfo(name)
                info.size = 4
                archive.addfile(info, io.BytesIO(b"code"))
        answers = {
            "status": status,
            "rev-parse": self.COMMIT.encode() + b"\n",
            "archive": code.getvalue(),
        }

        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout=answers[command[1]], stderr=b"")

        return mock.patch("subprocess.run", side_effect=run)

    def test_bundle_holds_the_committed_code_the_data_and_the_commit(self):
        from riskengine.management.commands.build_colab_bundle import build_bundle

        with self._git() as git:
            path, commit = build_bundle(self.root, self.training_set, self.root / "out")

        self.assertEqual(commit, self.COMMIT)
        self.assertEqual(path.name, "colab_bundle_0123456.tar.gz")
        archive_call = git.call_args_list[-1].args[0]
        self.assertEqual(archive_call[:4], ["git", "archive", "--format=tar", "HEAD"])
        with tarfile.open(path) as bundle:
            names = set(bundle.getnames())
            recorded = bundle.extractfile("COMMIT").read().decode().strip()
            digest = bundle.extractfile("DATASET_SHA256").read().decode().strip()
            data = bundle.extractfile("data/processed/training_set.csv").read()
        self.assertEqual(
            names,
            {
                "riskengine/ml/train.py",
                "riskengine/ml/artifacts/metrics.json",
                "requirements.txt",
                "data/processed/training_set.csv",
                "data/processed/training_set.meta.json",
                "COMMIT",
                "DATASET_SHA256",
            },
        )
        self.assertEqual(recorded, self.COMMIT)
        self.assertEqual(data, self.training_set.read_bytes())
        self.assertEqual(digest, hashlib.sha256(data).hexdigest())

    def test_unpacked_bundle_reports_its_commit(self):
        from riskengine.management.commands.build_colab_bundle import build_bundle

        with self._git():
            path, _ = build_bundle(self.root, self.training_set, self.root / "out")
        unpacked = self.root / "unpacked"
        with tarfile.open(path) as bundle:
            bundle.extractall(unpacked, filter="data")
        with mock.patch.dict("os.environ", {train.COMMIT_ENV: ""}):
            self.assertEqual(train.git_commit(unpacked), self.COMMIT)

    def test_uncommitted_changes_stop_the_bundle(self):
        from riskengine.management.commands.build_colab_bundle import BundleError, build_bundle

        with self._git(status=b" M riskengine/ml/train.py\n"):
            with self.assertRaisesRegex(BundleError, "uncommitted changes"):
                build_bundle(self.root, self.training_set, self.root / "out")
        self.assertFalse((self.root / "out").exists())

    def test_a_missing_training_set_stops_the_bundle(self):
        from riskengine.management.commands.build_colab_bundle import BundleError, build_bundle

        self.training_set.unlink()
        with self._git(), self.assertRaisesRegex(BundleError, "build_training_set"):
            build_bundle(self.root, self.training_set, self.root / "out")


class CandidateInputTests(SimpleTestCase):
    """The rainfall inputs under trial (CANDIDATE_FEATURES)."""

    def test_candidates_are_not_given_to_the_model(self):
        self.assertFalse(set(CANDIDATE_FEATURES) & set(FEATURE_NAMES))

    def test_longer_totals_reach_further_back(self):
        rain = {WINDOW_END - dt.timedelta(days=offset): 1.0 for offset in range(90)}
        rain[WINDOW_END - dt.timedelta(days=75)] = 21.0  # only the 90-day window holds this

        features = rainfall_features(rain, WINDOW_END)

        self.assertAlmostEqual(features["rain_60d"], 60)
        self.assertAlmostEqual(features["rain_60d"], features["rain_30d"] + features["rain_prev_30d"])
        self.assertAlmostEqual(features["rain_90d"], 89 + 21)

    def test_a_long_total_is_unknown_without_the_days_behind_it(self):
        rain = {WINDOW_END - dt.timedelta(days=offset): 1.0 for offset in range(60)}

        features = rainfall_features(rain, WINDOW_END)

        self.assertAlmostEqual(features["rain_60d"], 60)
        self.assertTrue(math.isnan(features["rain_90d"]))

    def test_short_peak_and_heavy_days(self):
        rain = {WINDOW_END - dt.timedelta(days=offset): 1.0 for offset in range(30)}
        rain[WINDOW_END - dt.timedelta(days=5)] = 60.0
        rain[WINDOW_END - dt.timedelta(days=4)] = 50.0  # the threshold itself counts
        rain[WINDOW_END - dt.timedelta(days=20)] = 49.9

        features = rainfall_features(rain, WINDOW_END)

        self.assertAlmostEqual(features["rain_3d_max"], 60 + 50 + 1)
        self.assertEqual(features["heavy_days"], 2)


def _trial_data(years=range(2000, 2012), counties=("C0", "C1", "C2")):
    """Daily rainfall and a matching training set whose floods follow rain_90d."""
    rng = np.random.default_rng(0)
    first, last = dt.date(years[0] - 1, 1, 1), dt.date(years[-1], 12, 31)
    days = [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]
    rainfall = {county: dict(zip(days, rng.gamma(0.4, 8.0, len(days)))) for county in counties}

    rows, y, dates, names = [], [], [], []
    for year in years:
        for month in range(1, 13):
            end = dt.date(year, month, 28)
            for county in counties:
                rain = rainfall_features(rainfall[county], end)
                rows.append([rain["rain_30d"], rain["rain_prev_30d"]])
                y.append(int(rain["rain_90d"] > 330))
                dates.append(end.isoformat())
                names.append(county)
    data = train.TrainingSet(
        features=("rain_30d", "rain_prev_30d"),
        X=np.array(rows),
        y=np.array(y),
        years=np.array([int(date[:4]) for date in dates]),
        months=np.array([int(date[5:7]) for date in dates]),
        counties=np.array(names),
        dates=dates,
        meta={},
    )
    return data, rainfall


class ExperimentTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data, cls.rainfall = _trial_data()

    def test_trial_inputs_are_added_as_columns(self):
        wider = experiments.with_candidates(self.data, self.rainfall, ["rain_90d", "heavy_days"])

        self.assertEqual(wider.features, ("rain_30d", "rain_prev_30d", "rain_90d", "heavy_days"))
        self.assertEqual(wider.X.shape, (len(self.data.y), 4))
        np.testing.assert_array_equal(wider.X[:, :2], self.data.X)
        expected = rainfall_features(self.rainfall["C0"], dt.date.fromisoformat(self.data.dates[0]))
        self.assertAlmostEqual(wider.X[0, 2], expected["rain_90d"])

    def test_only_inputs_under_trial_can_be_added(self):
        with self.assertRaises(ValueError):
            experiments.with_candidates(self.data, self.rainfall, ["elevation"])

    def test_rainfall_that_does_not_match_the_training_set_is_refused(self):
        other = {
            county: {day: mm + 1.0 for day, mm in series.items()}
            for county, series in self.rainfall.items()
        }
        with self.assertRaises(ValueError):
            experiments.with_candidates(self.data, other, ["rain_90d"])

    def test_the_longest_cached_series_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "Meru_1995-12-02_2021-09-30.json").write_text('{"2000-01-01": 1.0}')
            (cache / "Meru_1984-12-02_2021-09-30.json").write_text('{"2000-01-01": 2.0}')
            (cache / "Meru_2023-09-29_2023-11-28.json").write_text('{"2000-01-01": 3.0}')
            # A county whose name starts the same way must not be picked up.
            (cache / "Meru North_1970-01-01_2021-09-30.json").write_text('{"2000-01-01": 4.0}')

            rainfall = experiments.cached_rainfall(cache, ["Meru"])

            self.assertEqual(rainfall, {"Meru": {dt.date(2000, 1, 1): 2.0}})
            with self.assertRaises(FileNotFoundError):
                experiments.cached_rainfall(cache, ["Embu"])

    def test_an_input_the_floods_follow_is_found_to_help(self):
        result = experiments.compare(self.data, self.rainfall, ["rain_90d"], seeds=(0, 1))

        before, after = result["before"], result["after"]
        self.assertEqual(result["added"], ["rain_90d"])
        self.assertEqual(after["features"], [*before["features"], "rain_90d"])
        self.assertEqual(before["seeds"], [0, 1])
        self.assertEqual(before["floods"], int(self.data.y.sum()))
        self.assertGreater(after["average_precision"]["mean"], before["average_precision"]["mean"])
        self.assertLess(after["floods_missed"]["mean"], before["floods_missed"]["mean"])
        self.assertTrue(result["beyond_noise"])
        self.assertAlmostEqual(
            result["average_precision_change"],
            after["average_precision"]["mean"] - before["average_precision"]["mean"],
            places=4,
        )

    def test_rainfall_inputs_are_worked_out_again_from_other_rainfall(self):
        doubled = {
            county: {day: 2 * mm for day, mm in series.items()}
            for county, series in self.rainfall.items()
        }

        changed = experiments.with_rainfall(self.data, doubled)

        self.assertEqual(changed.features, self.data.features)
        np.testing.assert_allclose(changed.X, 2 * self.data.X)
        np.testing.assert_array_equal(changed.y, self.data.y)

    def test_trial_inputs_follow_the_other_rainfall(self):
        doubled = {
            county: {day: 2 * mm for day, mm in series.items()}
            for county, series in self.rainfall.items()
        }

        result = experiments.compare(
            self.data, self.rainfall, ["rain_90d"], seeds=(0,), other_rainfall=doubled
        )

        # Doubling every day's rain keeps the order of every row, so a
        # forest scores the same as on the original rainfall.
        same = experiments.compare(self.data, self.rainfall, ["rain_90d"], seeds=(0,))
        self.assertEqual(result["after"]["features"], same["after"]["features"])
        self.assertAlmostEqual(
            result["after"]["average_precision"]["mean"],
            same["after"]["average_precision"]["mean"],
            places=3,
        )


class FloodWindowTests(SimpleTestCase):
    """How a flood is assigned to a 30-day window (`in_window`)."""

    START, END = dt.date(2005, 6, 1), dt.date(2005, 6, 30)

    def _event(self, began, ended):
        from geodata.services.dfo import FloodEvent

        return FloodEvent(1, "Kenya", began, ended, 37.0, 0.0, 1000.0, 1.0, 0, 0, "Heavy rain")

    def _counts(self, began, ended, min_days):
        from riskengine.management.commands.build_training_set import in_window

        return in_window(self._event(began, ended), self.START, self.END, min_days)

    def test_any_overlap_counts_by_default(self):
        from riskengine.management.commands.build_training_set import MIN_OVERLAP_DAYS, in_window

        tail = self._event(dt.date(2005, 5, 3), dt.date(2005, 6, 3))
        self.assertEqual(MIN_OVERLAP_DAYS, 1)
        self.assertTrue(in_window(tail, self.START, self.END))
        self.assertFalse(in_window(self._event(dt.date(2005, 5, 3), dt.date(2005, 5, 31)), self.START, self.END))
        self.assertFalse(in_window(self._event(dt.date(2005, 7, 1), dt.date(2005, 7, 9)), self.START, self.END))

    def test_a_short_tail_from_an_earlier_window_does_not_count(self):
        self.assertFalse(self._counts(dt.date(2005, 5, 3), dt.date(2005, 6, 3), 7))
        self.assertFalse(self._counts(dt.date(2005, 5, 3), dt.date(2005, 6, 6), 7))
        self.assertTrue(self._counts(dt.date(2005, 5, 3), dt.date(2005, 6, 7), 7))

    def test_a_flood_counts_where_it_began_however_briefly(self):
        self.assertTrue(self._counts(dt.date(2005, 6, 17), dt.date(2005, 6, 20), 7))
        self.assertTrue(self._counts(dt.date(2005, 6, 30), dt.date(2005, 8, 1), 7))

    def test_labels_can_be_scored_against_another_truth(self):
        data, _rainfall = _trial_data()
        fewer = data.y.copy()
        fewer[np.flatnonzero(data.y)[::2]] = 0  # every other flood removed
        kept = int(fewer.sum())

        result = experiments.compare_labels(data, fewer, seeds=(0,))

        self.assertEqual(result["current"]["floods"], int(data.y.sum()))
        self.assertEqual(result["new"]["floods"], kept)
        self.assertEqual(result["current_on_new"]["floods"], kept)
        self.assertEqual(result["new"]["base_rate"], round(kept / len(data.y), 4))
        # Trained on the current labels either way, so the same probabilities
        # are behind both: only the truth they are scored against differs.
        self.assertNotEqual(
            result["current"]["average_precision"], result["current_on_new"]["average_precision"]
        )
        # This small set has no month inputs, so no season-only model.
        self.assertIsNone(result["current"]["season_only_average_precision"])


class LabelCheckTests(SimpleTestCase):
    """The circle labels set against mapped flood water (geodata/services/gfd.py)."""

    def test_counties_are_counted_under_the_circle_and_mapped_wet(self):
        from geodata.services import gfd

        circles = {7: {"A", "B", "C"}, 9: {"A"}}
        mapped = {7: {"A": 12.0, "B": 4.9, "C": 0.0, "D": 5.0}}

        rows = gfd.compare_with_circles(circles, mapped, min_km2=5.0)

        self.assertEqual(
            rows,
            [
                {
                    "event": 7,
                    "circle": 3,
                    "circle_and_wet": 1,
                    "circle_only": 2,
                    "wet_only": 1,
                    "mapped_km2": 21.9,
                }
            ],
        )

    def test_cached_events_need_no_earth_engine(self):
        from geodata.services import gfd

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "km2.json"
            cache.write_text(json.dumps({"7": {"A": 12.0}, "8": {"A": 1.0}}))
            with mock.patch.object(gfd.earth_engine_rain, "_ee", side_effect=AssertionError):
                mapped = gfd.flooded_km2([], [7], cache)

        self.assertEqual(mapped, {7: {"A": 12.0}})



class MappedLabelTests(SimpleTestCase):
    """Labels from mapped flood water where it exists (riskengine/ml/labels.py)."""

    WINDOW = dt.date(2006, 11, 30)

    def _event(self, event_id, began, ended):
        from geodata.services.dfo import FloodEvent

        return FloodEvent(event_id, "Kenya", began, ended, 37.0, 0.0, 1000.0, 1.0, 0, 0, "Heavy rain")

    def _labels(self, keys, min_km2=5.0):
        from riskengine.management.commands.build_training_set import in_window
        from riskengine.ml import labels

        mapped_flood = self._event(7, dt.date(2006, 11, 10), dt.date(2006, 11, 20))
        circle_flood = self._event(8, dt.date(2006, 11, 12), dt.date(2006, 11, 14))
        # The mapped flood's circle covers A and B; the other flood's covers C.
        placed = {"A": [mapped_flood], "B": [mapped_flood], "C": [circle_flood]}
        mapped = {7: {"A": 12.0, "B": 0.4, "D": 6.0}}
        return labels.mapped_labels(keys, placed, {7: mapped_flood}, mapped, in_window, min_km2)

    def test_a_mapped_flood_labels_where_the_water_was(self):
        from riskengine.ml.labels import CIRCLE, MAPPED, NONE

        keys = [(county, self.WINDOW) for county in ("A", "B", "C", "D", "E")]

        flooded, sources = self._labels(keys)

        # A: under the circle and wet. B: under the circle, dry on the map.
        # C: an unmapped flood's circle. D: wet outside any circle. E: nothing.
        self.assertEqual(list(flooded), [1, 0, 1, 1, 0])
        self.assertEqual(list(sources), [MAPPED, MAPPED, CIRCLE, MAPPED, NONE])

    def test_a_window_without_the_flood_is_not_labelled_by_it(self):
        from riskengine.ml.labels import NONE

        flooded, sources = self._labels([("A", dt.date(2006, 9, 30)), ("D", dt.date(2006, 9, 30))])

        self.assertEqual(list(flooded), [0, 0])
        self.assertEqual(list(sources), [NONE, NONE])

    def test_the_threshold_decides_how_much_water_is_a_flood(self):
        flooded, _sources = self._labels([("D", self.WINDOW)], min_km2=10.0)

        self.assertEqual(list(flooded), [0])

    def test_only_circle_labels_are_weighted_down(self):
        from riskengine.ml.labels import CIRCLE, MAPPED, NONE, weights

        row_weights = weights(np.array([MAPPED, CIRCLE, NONE], dtype=object), 0.5)

        self.assertEqual(list(row_weights), [1.0, 0.5, 1.0])

    def test_weights_of_one_change_nothing(self):
        data, _rainfall = _trial_data()
        folds = list(GroupKFold(train.CV_FOLDS).split(data.X, data.y, groups=data.years))
        model = train.candidates()["logreg"]
        columns = list(range(len(data.features)))

        plain = train.held_out_probabilities(data, lambda: clone(model), columns, folds)
        weighted = train.held_out_probabilities(
            data, lambda: clone(model), columns, folds, np.ones(len(data.y))
        )

        np.testing.assert_allclose(plain, weighted)

    def test_labels_are_also_scored_on_the_rows_they_are_surest_of(self):
        data, _rainfall = _trial_data()
        sure = np.zeros(len(data.y), dtype=bool)
        sure[::3] = True

        result = experiments.compare_labels(
            data, data.y, seeds=(0,), weights=np.full(len(data.y), 0.5), sure_rows=sure
        )

        self.assertEqual(result["current"]["rows"], len(data.y))
        for key in ("current_on_sure", "new_on_sure", "new_weighted_on_sure"):
            self.assertEqual(result[key]["rows"], int(sure.sum()))
            self.assertEqual(result[key]["floods"], int(data.y[sure].sum()))
        # The same labels and no weights: trained alike, scored alike.
        self.assertEqual(result["current_on_sure"]["average_precision"], result["new_on_sure"]["average_precision"])
