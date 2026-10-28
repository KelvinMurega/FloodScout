"""The dry gate: a dry window is shown no higher than Moderate (riskengine/dry.py)."""

import math

from django.test import SimpleTestCase

from . import dry
from .models import RiskScore

C = RiskScore.RiskCategory
NAMES = ["rain_30d", "rain_7d_max", "rain_30d_ratio", "month_sin"]


def row(rain_30d, ratio):
    return [rain_30d, 10.0, ratio, 0.5]


class DryGateTests(SimpleTestCase):
    def test_the_candidates(self):
        # 20 mm that is still 80% of normal: dry by the amount, not by the ratio.
        self.assertTrue(dry.dry_by("G2", 20.0, 0.8))
        self.assertFalse(dry.dry_by("G1", 20.0, 0.8))
        self.assertFalse(dry.dry_by("G3", 20.0, 0.8))
        self.assertTrue(dry.dry_by("G4", 20.0, 0.8))
        # 60 mm that is 40% of normal: dry by the ratio only.
        self.assertTrue(dry.dry_by("G4", 60.0, 0.4))
        self.assertFalse(dry.dry_by("G4", 60.0, 0.9))

    def test_missing_rain_is_not_dry(self):
        self.assertFalse(dry.dry_by("G4", None, None))
        self.assertFalse(dry.dry_by("G4", math.nan, math.nan))

    def test_a_dry_window_is_capped_at_moderate(self):
        self.assertEqual(dry.gated(C.VERY_HIGH, row(6.0, 0.1), NAMES), C.MODERATE)
        self.assertEqual(dry.gated(C.HIGH, row(80.0, 0.4), NAMES), C.MODERATE)

    def test_a_wet_window_and_a_low_category_are_left_alone(self):
        self.assertEqual(dry.gated(C.VERY_HIGH, row(90.0, 1.2), NAMES), C.VERY_HIGH)
        self.assertEqual(dry.gated(C.LOW, row(2.0, 0.05), NAMES), C.LOW)
        self.assertEqual(dry.gated(C.MODERATE, row(2.0, 0.05), NAMES), C.MODERATE)

    def test_a_model_without_the_rain_columns_is_not_gated(self):
        self.assertEqual(dry.gated(C.VERY_HIGH, [0.5], ["month_sin"]), C.VERY_HIGH)
