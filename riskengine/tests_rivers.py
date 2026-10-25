"""Tests for the river and soil inputs under trial: GloFAS discharge on each
county's largest river, and ERA5-Land soil moisture over the county."""

from __future__ import annotations

import datetime as dt
import json
import math
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
from django.test import SimpleTestCase

from geodata.services import earth_engine_rain, open_meteo, river_points
from geodata.services.open_meteo import Point
from riskengine.management.commands import build_discharge
from riskengine.ml import experiments, train
from riskengine.ml.features import (
    CANDIDATE_FEATURES,
    DISCHARGE_CANDIDATES,
    FEATURE_NAMES,
    SOIL_CANDIDATES,
    discharge_features,
    soil_features,
)

END = dt.date(2019, 11, 30)


def _days(values, end=END):
    """{date: value} for consecutive days ending on `end`, oldest first."""
    return {end - dt.timedelta(days=len(values) - 1 - i): v for i, v in enumerate(values)}


class DischargeFeatureTests(SimpleTestCase):
    def test_the_new_inputs_are_under_trial_not_in_the_model(self):
        self.assertFalse(set(DISCHARGE_CANDIDATES + SOIL_CANDIDATES) & set(FEATURE_NAMES))
        self.assertTrue(set(DISCHARGE_CANDIDATES + SOIL_CANDIDATES) <= set(CANDIDATE_FEATURES))

    def test_peak_mean_and_rise_against_the_year_before(self):
        # A year at 9 m³/s, then a window that peaks at 99.
        flows = _days([9.0] * 365 + [9.0] * 29 + [99.0])

        features = discharge_features(flows, END)

        self.assertEqual(features["discharge_30d_max"], 99.0)
        self.assertAlmostEqual(features["discharge_30d_mean"], (29 * 9 + 99) / 30)
        self.assertAlmostEqual(features["discharge_rise"], math.log(100 / 10))

    def test_a_missing_day_of_flow_is_not_zero_flow(self):
        flows = _days([5.0] * 395)
        del flows[END - dt.timedelta(days=3)]

        self.assertEqual(discharge_features(flows, END)["discharge_30d_mean"], 5.0)

    def test_without_the_year_before_the_rise_is_unknown(self):
        features = discharge_features(_days([5.0] * 30), END)

        self.assertEqual(features["discharge_30d_max"], 5.0)
        self.assertTrue(math.isnan(features["discharge_rise"]))

    def test_no_record_at_all_is_unknown_not_zero(self):
        features = discharge_features({}, END)

        self.assertTrue(all(math.isnan(value) for value in features.values()))


class SoilFeatureTests(SimpleTestCase):
    def test_the_day_before_the_window_and_the_wettest_day_in_it(self):
        soil = _days([0.20] + [0.25] * 29 + [0.40])  # the day before, then the 30 days

        features = soil_features(soil, END)

        self.assertEqual(features["soil_moisture_start"], 0.20)
        self.assertEqual(features["soil_moisture_max"], 0.40)

    def test_missing_soil_moisture_is_unknown(self):
        features = soil_features({}, END)

        self.assertTrue(math.isnan(features["soil_moisture_start"]))
        self.assertTrue(math.isnan(features["soil_moisture_max"]))


class GlofasClientTests(SimpleTestCase):
    def test_a_point_is_moved_to_the_centre_of_its_grid_cell(self):
        self.assertAlmostEqual(open_meteo.glofas_cell(39.646), 39.625)
        self.assertAlmostEqual(open_meteo.glofas_cell(-0.456), -0.475)

    def test_a_long_history_counts_as_many_calls(self):
        self.assertEqual(open_meteo.calls_for(1, 7), 1.0)
        self.assertAlmostEqual(open_meteo.calls_for(2, 365), 2 * 365 / 14)

    def test_discharge_names_the_glofas_record(self):
        payload = [{"daily": {"time": ["2020-05-01", "2020-05-02"], "river_discharge": [10.0, None]}}]
        day = dt.date(2020, 5, 1)
        with mock.patch.object(open_meteo, "_get_json", return_value=payload) as get:
            (series,) = open_meteo.fetch_discharge([Point(39.6, -0.5)], day, day)

        params = get.call_args.args[1]
        self.assertEqual(get.call_args.args[0], open_meteo.FLOOD_URL)
        self.assertEqual(params["models"], "seamless_v4")
        self.assertEqual(params["daily"], "river_discharge")
        self.assertEqual(series, {day: 10.0})

    def test_the_cell_nearest_the_rivers_size_is_taken_not_the_wettest(self):
        # Nine cells: one a lake carrying 900 m³/s, one the river at 60, the
        # rest dry or a trickle.
        flows = [0.0, 0.0, 900.0, 2.0, 60.0, 0.0, 0.0, 0.0, 0.0]

        def fake(cells, start, end):
            return [{start: flow} if flow else {} for flow in flows[: len(cells)]]

        with mock.patch.object(open_meteo, "fetch_discharge", side_effect=fake):
            cell, mean = open_meteo.river_cell(
                Point(39.62, -0.47), dt.date(2020, 5, 1), dt.date(2020, 5, 14), expected_m3s=50.0
            )

        self.assertEqual(mean, 60.0)
        self.assertEqual(cell, Point(39.625, -0.475))  # the centre of the block


class RiverPointTests(SimpleTestCase):
    def test_the_middle_vertex_lies_on_the_line(self):
        line = {"type": "LineString", "coordinates": [[0, 0], [1, 1], [2, 2]]}
        multi = {"type": "MultiLineString", "coordinates": [[[5, 5], [6, 6]], [[0, 0], [1, 1], [2, 2]]]}

        self.assertEqual(river_points.middle_vertex(line), (1.0, 1.0))
        self.assertEqual(river_points.middle_vertex(multi), (1.0, 1.0))
        self.assertIsNone(river_points.middle_vertex({"type": "Point", "coordinates": [0, 0]}))

    def test_a_reach_in_a_lake_is_passed_over(self):
        candidates = [
            {"point": (34.7, -0.2), "water": 98, "mean_m3s": 400.0},  # Winam Gulf
            {"point": None, "water": None, "mean_m3s": 90.0},  # no part inside the county
            {"point": (34.9, -0.3), "water": 0, "mean_m3s": 54.0},  # the Nyando
        ]

        self.assertEqual(river_points.choose_reach(candidates)["mean_m3s"], 54.0)
        self.assertIsNone(river_points.choose_reach(candidates[:2]))


class DischargeDownloadTests(SimpleTestCase):
    def test_years_are_clipped_to_the_record(self):
        spans = build_discharge.year_spans(dt.date(1997, 1, 1), dt.date(1999, 6, 30))

        self.assertEqual(
            spans,
            [
                (dt.date(1997, 1, 1), dt.date(1997, 12, 31)),
                (dt.date(1998, 1, 1), dt.date(1998, 12, 31)),
                (dt.date(1999, 1, 1), dt.date(1999, 6, 30)),
            ],
        )

    def test_a_download_picks_up_where_it_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Garissa").mkdir()
            (root / "Garissa" / "1997.json").write_text(json.dumps({"1997-01-01": 120.0}))

            left = build_discharge.missing_years(["Garissa"], root)
            series = build_discharge.cached_discharge(["Garissa"], root)

        self.assertNotIn(1997, [start.year for _county, start, _end in left])
        self.assertEqual(len(left), len(build_discharge.year_spans()) - 1)
        self.assertEqual(series, {"Garissa": {dt.date(1997, 1, 1): 120.0}})


class SoilMoistureClientTests(SimpleTestCase):
    def test_soil_moisture_keeps_its_units(self):
        features = [{"properties": {"county": "Kisumu", "date": "2010-04-01", "mean": 0.31}}]

        self.assertEqual(
            earth_engine_rain.parse_features(features, factor=1.0),
            {"Kisumu": {dt.date(2010, 4, 1): 0.31}},
        )

    def test_cached_quarters_need_no_earth_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "2010-01-01_2010-03-31.json").write_text(json.dumps({"Kisumu": {"2010-01-01": 0.3}}))
            with mock.patch.object(earth_engine_rain, "_ee", side_effect=AssertionError):
                soil = earth_engine_rain.fetch_county_soil_moisture(
                    [], dt.date(2010, 1, 1), dt.date(2010, 3, 31), cache
                )

        self.assertEqual(soil, {"Kisumu": {dt.date(2010, 1, 1): 0.3}})


class RiverTrialTests(SimpleTestCase):
    """River and soil inputs added to a training set for `compare_inputs`."""

    def _data(self):
        dates = ["2010-01-31", "2010-02-28"]
        return train.TrainingSet(
            features=("rain_30d",),
            X=np.array([[10.0], [20.0]]),
            y=np.array([0, 1]),
            years=np.array([2010, 2010]),
            months=np.array([1, 2]),
            counties=np.array(["Garissa", "Garissa"]),
            dates=dates,
            meta={},
        )

    def test_river_and_soil_columns_come_from_their_own_series(self):
        first = dt.date(2009, 1, 1)
        days = [first + dt.timedelta(days=i) for i in range(425)]
        discharge = {"Garissa": {day: 50.0 for day in days}}
        soil = {"Garissa": {day: 0.3 for day in days}}

        wider = experiments.with_candidates(
            self._data(), {}, ["discharge_30d_max", "soil_moisture_start"], discharge=discharge, soil=soil
        )

        self.assertEqual(wider.features, ("rain_30d", "discharge_30d_max", "soil_moisture_start"))
        np.testing.assert_allclose(wider.X[:, 1:], [[50.0, 0.3], [50.0, 0.3]])

    def test_a_river_input_without_discharge_is_refused(self):
        with self.assertRaises(ValueError):
            experiments.with_candidates(self._data(), {}, ["discharge_rise"])
