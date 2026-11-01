"""Tests for the training pipeline: configs, the closed final test, run folders,
the ledger, and that training and serving build the same input row."""

from __future__ import annotations

import copy
import csv
import datetime as dt
import hashlib
import json
import math
import tarfile
import tempfile
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from django.test import SimpleTestCase, TestCase

from riskengine.management.commands.build_training_set import LABEL_COLUMNS, training_row
from riskengine.ml import train
from riskengine.ml.features import FEATURE_NAMES
from riskengine.ml.infer import region_row
from riskengine.ml.pipeline import config as run_config
from riskengine.ml.pipeline import hazard, runs
from riskengine.ml.pipeline.__main__ import main
from riskengine.tests_model import BuildColabBundleTests, _county, write_training_csv

SHIPPED_CONFIG = Path(train.REPO_ROOT) / "riskengine" / "ml" / "configs" / "hazard_rf_20261007.toml"
TRAINING_SET = "data/processed/training_set.csv"


def synthetic_config(root: Path, *, tune: bool = False, seeds=(0,)) -> run_config.RunConfig:
    """The shipped config, pointed at a small synthetic training set under `root`."""
    path = root / TRAINING_SET
    path.parent.mkdir(parents=True, exist_ok=True)
    write_training_csv(path)
    shipped = run_config.load(SHIPPED_CONFIG)
    settings = copy.deepcopy(shipped.settings)
    settings["data"]["training_set"] = TRAINING_SET
    settings["data"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    settings["selection"]["tune"] = tune
    settings["selection"]["seeds"] = list(seeds)
    return run_config.RunConfig(path=root / "synthetic.toml", model="hazard", settings=settings)


def write_config_file(root: Path, config: run_config.RunConfig) -> Path:
    """`config` as a TOML file, written by editing the shipped file's text."""
    text = SHIPPED_CONFIG.read_text(encoding="utf-8")
    shipped = run_config.load(SHIPPED_CONFIG).settings["data"]
    text = text.replace(shipped["training_set"], config.value("data", "training_set"))
    text = text.replace(shipped["sha256"], config.value("data", "sha256"))
    config.path.write_text(text, encoding="utf-8")
    return config.path


class ConfigTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_the_shipped_config_describes_what_train_py_does(self):
        self.assertEqual(hazard.differences(run_config.load(SHIPPED_CONFIG)), [])

    def test_the_shipped_config_names_the_champions_training_set_and_inputs(self):
        config = run_config.load(SHIPPED_CONFIG)
        champion = json.loads(train.METRICS_PATH.read_text())
        self.assertEqual(config.value("data", "sha256"), champion["provenance"]["training_set_sha256"])
        self.assertEqual(config.value("data", "features"), champion["features"])
        self.assertEqual(config.value("data", "first_year"), champion["first_training_year"])

    def test_an_open_final_test_is_refused(self):
        config = synthetic_config(self.root)
        config.settings["cv"]["final_test"] = "2017"
        self.assertTrue(any("final_test" in problem for problem in hazard.differences(config)))
        with self.assertRaises(run_config.ConfigError):
            hazard.check(config, self.root)

    def test_a_grid_that_differs_from_the_code_is_refused(self):
        config = synthetic_config(self.root)
        config.settings["selection"]["grids"]["logreg"]["logisticregression__C"] = [1.0]
        with self.assertRaisesRegex(run_config.ConfigError, "grids"):
            hazard.check(config, self.root)

    def test_a_training_set_that_changed_is_refused(self):
        config = synthetic_config(self.root)
        with (self.root / TRAINING_SET).open("a") as handle:
            handle.write("\n")
        with self.assertRaisesRegex(run_config.ConfigError, "SHA-256"):
            hazard.check(config, self.root)

    def test_a_missing_training_set_is_refused(self):
        config = synthetic_config(self.root)
        (self.root / TRAINING_SET).unlink()
        with self.assertRaisesRegex(run_config.ConfigError, "not found"):
            hazard.check(config, self.root)

    def test_an_unknown_model_or_bad_toml_is_refused(self):
        path = self.root / "bad.toml"
        path.write_text('model = "rainbow"\n')
        with self.assertRaisesRegex(run_config.ConfigError, "model must be"):
            run_config.load(path)
        path.write_text("model = \n")
        with self.assertRaisesRegex(run_config.ConfigError, "not valid TOML"):
            run_config.load(path)


class RunTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.config = synthetic_config(cls.root)
        cls.result = hazard.run(cls.config, {(2019, 9): 1.0}, {}, cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        super().tearDownClass()

    def test_a_run_never_scores_the_final_test(self):
        self.assertIsNone(self.result.metrics["final_test"])

    def test_a_run_records_its_config(self):
        self.assertEqual(self.result.metrics["config"], "synthetic")

    def test_inputs_other_than_the_configs_are_refused(self):
        config = copy.deepcopy(self.config)
        config.settings["data"]["features"] = FEATURE_NAMES[:-1]
        with self.assertRaisesRegex(run_config.ConfigError, "inputs"):
            hazard.run(config, {}, {}, self.root)

    def test_the_run_folder_holds_the_model_its_metrics_the_config_and_a_summary(self):
        self.config.path.write_text("model = 'hazard'\n")  # what write copies
        folder = runs.write("RUN2", self.config, self.result.artifact, self.result.metrics, self.root / "runs")
        metrics = json.loads((folder / "metrics.json").read_text())
        digest = hashlib.sha256((folder / "flood_model.joblib").read_bytes()).hexdigest()
        self.assertEqual(metrics["model_sha256"], digest)
        self.assertEqual((folder / "config.toml").read_text(), "model = 'hazard'\n")
        self.assertIn("# Run RUN2", (folder / "summary.md").read_text())

    def test_a_run_does_not_replace_the_served_model(self):
        before = train.ARTIFACT_PATH.stat().st_mtime if train.ARTIFACT_PATH.exists() else None
        self.config.path.write_text("model = 'hazard'\n")
        runs.write("RUN3", self.config, self.result.artifact, self.result.metrics, self.root / "runs")
        after = train.ARTIFACT_PATH.stat().st_mtime if train.ARTIFACT_PATH.exists() else None
        self.assertEqual(before, after)

    def test_the_ledger_gets_one_line_per_run(self):
        ledger = self.root / "experiments.md"
        ledger.write_text("| Run | Date |\n|---|---|\n")
        line = runs.record("RUN4", self.config, self.result.metrics, ledger=ledger, today=dt.date(2026, 10, 29))
        self.assertTrue(line.startswith("| RUN4 | 2026-10-29 | hazard | `synthetic.toml` |"))
        self.assertEqual(ledger.read_text().splitlines()[-1], line)

    def test_the_champion_comparison_is_only_called_fair_on_the_same_training_set(self):
        champion = self.root / "champion.json"
        metrics = self.result.metrics
        record = {
            "version": "champ",
            "selected": metrics["selected"],
            "comparison": {metrics["selected"]: {"average_precision": 0.0}},
            "provenance": dict(metrics["provenance"]),
        }
        champion.write_text(json.dumps(record))
        fair = runs.champion_comparison(self.config, metrics, champion)
        self.assertTrue(fair["same_training_set"])
        self.assertTrue(fair["meets_margin"])

        record["provenance"]["training_set_sha256"] = "other"
        champion.write_text(json.dumps(record))
        unfair = runs.champion_comparison(self.config, metrics, champion)
        self.assertFalse(unfair["meets_margin"])
        self.assertIn("not comparable", runs.summary("R", self.config, metrics, unfair))


class RunIdTests(SimpleTestCase):
    def test_the_notebooks_run_id_wins(self):
        with mock.patch.dict("os.environ", {train.RUN_ID_ENV: "20261029T0900Z_abcdef0"}):
            self.assertEqual(runs.run_id(), "20261029T0900Z_abcdef0")

    def test_otherwise_time_and_commit(self):
        with mock.patch.dict("os.environ", {train.RUN_ID_ENV: ""}):
            now = dt.datetime(2026, 10, 29, 9, 5, tzinfo=dt.UTC)
            self.assertEqual(runs.run_id(now, "0123456789"), "20261029T0905Z_0123456")


class CommandLineTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = write_config_file(self.root, synthetic_config(self.root))

    def _main(self, *args):
        out, err = StringIO(), StringIO()
        with (
            mock.patch.object(train, "REPO_ROOT", self.root),
            mock.patch("sys.stdout", out),
            mock.patch("sys.stderr", err),
        ):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_check_passes_on_a_config_that_matches(self):
        code, out, _err = self._main("train", "--model", "hazard", "--config", str(self.path), "--check")
        self.assertEqual(code, 0)
        self.assertIn("matches train.py", out)

    def test_a_model_that_is_not_the_configs_is_refused(self):
        path = self.root / "other.toml"
        path.write_text('model = "hazard"\n')
        with mock.patch.object(run_config, "MODELS", ("hazard", "susceptibility")):
            code, _out, err = self._main(
                "train", "--model", "susceptibility", "--config", str(path), "--check"
            )
        self.assertEqual(code, 2)
        self.assertIn("is a hazard config", err)


class TrainServeParityTests(TestCase):
    """Training and serving must build the same row for the same county and date."""

    def test_one_county_month_is_the_same_row_both_ways(self):
        county = _county("Paritia", 36.0, -1.0, area=5432.1)
        terrain = SimpleNamespace(elevation=812.0, slope=3.25)
        end = dt.date(2019, 11, 30)
        rng = np.random.default_rng(3)
        rain = {end - dt.timedelta(days=offset): float(rng.gamma(1.5, 6.0)) for offset in range(60)}
        oni = {(2019, 9): 0.35, (2019, 10): 0.5}
        dmi = {(2019, 10): 1.9}
        normal = 88.0

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "training_set.csv"
            with path.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["county", "date", "normal_30d", *FEATURE_NAMES, *LABEL_COLUMNS])
                features = training_row(county, rain, end, normal_30d=normal, oni=oni, dmi=dmi, terrain=terrain)
                writer.writerow([county.name, end.isoformat(), normal, *features, 0, 0, 0])
            data = train.load_training_set(path, first_year=2019)
        normals = {county.name: {end.month: normal}}
        trained = train.with_normals(data, normals)[0]

        model = {"features": list(FEATURE_NAMES), "normals": normals, "oni": oni, "dmi": dmi}
        served = region_row(model, county, rain, end, terrain)

        self.assertEqual(list(data.features), list(FEATURE_NAMES))
        for name, a, b in zip(FEATURE_NAMES, trained, served):
            if math.isnan(b):
                self.assertTrue(math.isnan(a), name)
            else:
                self.assertAlmostEqual(a, b, places=4, msg=name)


class BundleWithConfigTests(SimpleTestCase):
    """With --config the bundle carries the config's training set, at its own path."""

    COMMIT = BuildColabBundleTests.COMMIT
    setUp = BuildColabBundleTests.setUp
    _git = BuildColabBundleTests._git

    def test_the_bundle_carries_the_configs_training_set_and_names_the_config(self):
        from riskengine.management.commands.build_colab_bundle import build_bundle

        config = synthetic_config(self.root)
        write_config_file(self.root, config)
        with self._git():
            path, _commit = build_bundle(self.root, self.training_set, self.root / "out", Path("synthetic.toml"))
        with tarfile.open(path) as bundle:
            self.assertEqual(bundle.extractfile("CONFIG").read().decode().strip(), "synthetic.toml")
            packed = bundle.extractfile(TRAINING_SET).read()
        self.assertEqual(hashlib.sha256(packed).hexdigest(), config.value("data", "sha256"))
