"""Tests for the alert-rule backtest (alerts/backtest.py)."""

from __future__ import annotations

import datetime as dt

import numpy as np
from django.test import SimpleTestCase

from alerts import backtest


def _history(probability, before=None, rain_7d=None, wettest=None, soil=None, flooded=None, years=None):
    n = len(probability)
    years = np.array(years if years is not None else [2000] * n)
    return backtest.History(
        counties=np.array(["Kisumu"] * n),
        dates=np.array([dt.date(int(y), 4, 30) for y in years]),
        years=years,
        months=np.array([4] * n),
        flooded=np.array(flooded if flooded is not None else [False] * n),
        probability=np.array(probability, dtype=float),
        probability_before=np.array(before if before is not None else probability, dtype=float),
        rain_7d_max=np.array(rain_7d if rain_7d is not None else [10.0] * n, dtype=float),
        wettest_day=np.array(wettest if wettest is not None else [5.0] * n, dtype=float),
        soil_start=np.array(soil if soil is not None else [np.nan] * n, dtype=float),
    )


class SignalTests(SimpleTestCase):
    def test_heavy_rain_is_against_the_countys_own_month(self):
        # Ten Aprils of 10 to 100 mm: the 90th percentile is 91 mm.
        h = _history([0.5] * 10, rain_7d=list(range(10, 101, 10)))
        heavy = backtest.signals(h)["heavy_local"]
        self.assertEqual(heavy.tolist(), [False] * 9 + [True])

    def test_thresholds_come_from_before_the_final_test_only(self):
        years = [2000] * 10 + [2018]
        h = _history([0.5] * 11, rain_7d=[*range(10, 101, 10), 1000], years=years)
        thresholds = backtest.county_month_percentile(h, h.rain_7d_max, 90)
        self.assertEqual(thresholds["Kisumu"][4], 91.0)

    def test_rising_is_the_change_in_times_usual(self):
        h = _history([0.15, 0.15], before=[0.15, 0.0])
        rising = backtest.signals(h)["rising"]
        self.assertEqual(rising.tolist(), [False, True])

    def test_missing_soil_is_never_wet(self):
        h = _history([0.5, 0.5], soil=[np.nan, 0.3])
        self.assertFalse(backtest.signals(h)["wet_ground"][0])


class ScoreTests(SimpleTestCase):
    def test_counts_alerts_hits_and_breadth(self):
        h = _history([0.5] * 4, flooded=[True, False, True, False])
        alerted = np.array([True, True, False, False])
        result = backtest.score(alerted, h, np.ones(4, dtype=bool))
        self.assertEqual(result["alerts"], 2)
        self.assertEqual(result["caught"], 1)
        self.assertEqual(result["hit_rate"], 0.5)
        self.assertEqual(result["caught_share"], 0.5)
        self.assertEqual(result["counties_per_alert_month"], 2.0)

    def test_no_alerts_has_no_hit_rate(self):
        h = _history([0.0, 0.0], flooded=[True, False])
        result = backtest.score(np.zeros(2, dtype=bool), h, np.ones(2, dtype=bool))
        self.assertIsNone(result["hit_rate"])


class ChooseTests(SimpleTestCase):
    def test_best_hit_rate_that_keeps_enough_floods(self):
        results = {
            "A": {"caught": 100, "hit_rate": 0.2},
            "B": {"caught": 70, "hit_rate": 0.3},
            "C": {"caught": 30, "hit_rate": 0.9},  # too few floods caught
        }
        self.assertEqual(backtest.choose(results), "B")

    def test_today_wins_when_nothing_else_qualifies(self):
        results = {"A": {"caught": 100, "hit_rate": 0.2}, "C": {"caught": 10, "hit_rate": 0.9}}
        self.assertEqual(backtest.choose(results), "A")
