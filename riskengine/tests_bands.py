"""The map's bands (riskengine/bands.py) and the rain High needs (dry.py)."""

import math

import numpy as np
from django.test import SimpleTestCase

from riskengine import bands, dry
from riskengine.ml.infer import classify, model_thresholds

NAMES = ["rain_30d", "rain_30d_ratio"]


class MapBandsTests(SimpleTestCase):
    def test_the_map_uses_its_own_bands_not_the_models(self):
        thresholds = model_thresholds({"probability_bands": [0.05, 0.10, 0.20]})
        self.assertEqual(classify(0.04, thresholds), "low")
        self.assertEqual(classify(0.12, thresholds), "moderate")  # High before
        self.assertEqual(classify(0.15, thresholds), "moderate")
        self.assertEqual(classify(0.16, thresholds), "high")
        self.assertEqual(classify(0.25, thresholds), "high")  # Very High before
        self.assertEqual(classify(0.31, thresholds), "very_high")


class RainForHighTests(SimpleTestCase):
    def test_high_needs_the_countys_normal_rain(self):
        # Garissa on 10 November 2026: 53 mm, two thirds of normal.
        self.assertEqual(dry.gated("very_high", [53.0, 0.66], NAMES), "moderate")
        self.assertEqual(dry.gated("high", [120.0, 1.0], NAMES), "high")
        self.assertEqual(dry.gated("very_high", [200.0, 1.9], NAMES), "very_high")

    def test_low_and_moderate_are_left_alone(self):
        self.assertEqual(dry.gated("low", [5.0, 0.1], NAMES), "low")
        self.assertEqual(dry.gated("moderate", [5.0, 0.1], NAMES), "moderate")

    def test_no_normal_on_file_caps_nothing_but_the_dry_gate(self):
        self.assertEqual(dry.gated("high", [80.0, math.nan], NAMES), "high")
        self.assertEqual(dry.gated("high", [10.0, math.nan], NAMES), "moderate")  # under 25 mm


class ReplayTests(SimpleTestCase):
    def test_levels_follow_the_edges_and_the_rain(self):
        probability = np.array([0.02, 0.12, 0.2, 0.4, 0.4])
        rain = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
        ratio = np.array([1.2, 1.2, 1.2, 1.2, 0.8])

        old = bands.levels(probability, rain, ratio, (0.05, 0.10, 0.20), None)
        new = bands.levels(probability, rain, ratio, bands.MAP_BANDS, 1.0)

        self.assertEqual(old.tolist(), [0, 2, 2, 3, 3])
        self.assertEqual(new.tolist(), [0, 1, 2, 3, 1])

    def test_a_candidate_passes_only_when_each_colour_means_what_it_says(self):
        self.assertTrue(bands._meets([0.24, 1.2, 1.9, 3.5]))
        self.assertFalse(bands._meets([0.24, 1.0, 1.4, 2.8]))  # the old map
        self.assertFalse(bands._meets([0.24, 1.0, 1.9, None]))

    def test_replay_leaves_out_the_final_test_years(self):
        n = 40
        probability = np.tile([0.02, 0.4], n // 2)
        flooded = np.tile([False, True], n // 2)
        years = np.array([2000] * (n - 2) + [2018, 2018])
        result = bands.replay(
            probability, flooded, np.full(n, 100.0), np.full(n, 1.5), np.full(n, 4), years
        )
        self.assertEqual(result["results"]["S3"]["floods"], (n - 2) // 2)
        self.assertEqual(result["chosen"], "S3")
