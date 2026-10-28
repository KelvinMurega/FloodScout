"""The score against an ordinary year for the county and month (riskengine/season.py)."""

import json
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
from django.test import SimpleTestCase

from . import season
from .ml import train

FEATURES = ["rain_30d", "oni", "dmi", "month_sin"]


def training_set():
    # Kisumu in October in three years, one of them after the development years.
    X = np.array(
        [
            [100.0, 1.5, 0.4, 0.2],
            [140.0, -1.0, 0.1, 0.2],
            [500.0, 2.0, 0.9, 0.2],
        ]
    )
    return train.TrainingSet(
        features=FEATURES,
        X=X,
        y=np.array([0, 1, 1]),
        years=np.array([2000, 2001, 2018]),
        months=np.array([10, 10, 10]),
        counties=np.array(["Kisumu"] * 3),
        dates=["2000-10-31", "2001-10-31", "2018-10-31"],
        meta={},
    )


class OrdinaryRowTests(SimpleTestCase):
    def test_median_rain_of_the_development_years_with_the_oceans_at_zero(self):
        rows = season.ordinary_rows(training_set(), years_before=2017)
        self.assertEqual(list(rows), [("Kisumu", 10)])
        self.assertEqual(rows[("Kisumu", 10)], [120.0, 0.0, 0.0, 0.2])

    def test_build_scores_each_row_with_the_model(self):
        model = {"version": "m1", "features": FEATURES}
        with mock.patch.object(season.scorer, "flood_probabilities", return_value=[0.125]) as probabilities:
            table = season.build(training_set(), model, years_before=2017)
        self.assertEqual(table, {"model_version": "m1", "counties": {"Kisumu": {"10": 0.125}}})
        self.assertEqual(probabilities.call_args.args[1], [[120.0, 0.0, 0.0, 0.2]])

    def test_build_refuses_a_model_with_other_columns(self):
        with self.assertRaises(ValueError):
            season.build(training_set(), {"version": "m1", "features": ["rain_30d"]}, years_before=2017)


class TimesSeasonTests(SimpleTestCase):
    def setUp(self):
        season.normals.cache_clear()
        self.addCleanup(season.normals.cache_clear)
        self.path = Path(tempfile.mkdtemp()) / "season_normals.json"
        patcher = mock.patch.object(season, "SEASON_NORMALS_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, version):
        self.path.write_text(json.dumps({"model_version": version, "counties": {"Kisumu": {"10": 0.1}}}))

    def test_a_score_against_its_county_and_month(self):
        self.write(season.scorer.MODEL_VERSION)
        self.assertEqual(season.times_season(0.25, "Kisumu", 10), 2.5)
        self.assertEqual(season.times_season(0.05, "Kisumu", 10), 0.5)

    def test_nothing_without_a_table_a_county_or_a_score(self):
        self.assertIsNone(season.times_season(0.25, "Kisumu", 10))
        self.write(season.scorer.MODEL_VERSION)
        season.normals.cache_clear()
        self.assertIsNone(season.times_season(0.25, "Nowhere", 10))
        self.assertIsNone(season.times_season(None, "Kisumu", 10))

    def test_a_table_for_another_model_is_ignored(self):
        self.write("some-older-model")
        self.assertIsNone(season.times_season(0.25, "Kisumu", 10))
