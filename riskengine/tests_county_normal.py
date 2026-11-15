"""The county-normal replay: its own line, the candidates and the choice."""

import numpy as np
from django.test import SimpleTestCase

from riskengine import county_normal as cn


class OwnLineTests(SimpleTestCase):
    def test_a_year_is_measured_against_the_other_years_only(self):
        # Kericho in April over three years; the 2001 line is the median of
        # 1999 and 2000, not of all three.
        probability = np.array([0.1, 0.3, 0.9])
        counties = np.array(["Kericho"] * 3)
        months = np.array([4, 4, 4])
        years = np.array([1999, 2000, 2001])

        line = cn.own_line(probability, counties, months, years, 50)

        np.testing.assert_allclose(line, [0.6, 0.5, 0.2])

    def test_counties_and_months_are_kept_apart(self):
        probability = np.array([0.1, 0.2, 0.8, 0.9])
        counties = np.array(["Kericho", "Kericho", "Kericho", "Garissa"])
        months = np.array([4, 4, 5, 4])
        years = np.array([1999, 2000, 1999, 1999])

        line = cn.own_line(probability, counties, months, years, 50)

        np.testing.assert_allclose(line[:2], [0.2, 0.1])
        self.assertTrue(np.isnan(line[2]) and np.isnan(line[3]))


class WarnedTests(SimpleTestCase):
    def test_a_county_needs_to_be_unusual_for_itself(self):
        # Kericho is always about 15%: above 10%, never above its own median.
        # Garissa is usually 2% and 15% in one year: that one warns.
        probability = np.array([0.15, 0.15, 0.16, 0.02, 0.02, 0.15])
        counties = np.array(["Kericho"] * 3 + ["Garissa"] * 3)
        months = np.array([4] * 6)
        years = np.array([1999, 2000, 2001] * 2)

        today = cn.warned("today", probability, counties, months, years)
        own = cn.warned("own_50", probability, counties, months, years)

        self.assertEqual(today.tolist(), [True, True, True, False, False, True])
        self.assertEqual(own.tolist(), [False, False, True, False, False, True])

    def test_a_county_with_no_other_year_keeps_todays_line(self):
        alarm = cn.warned("own_90", np.array([0.3]), np.array(["Lamu"]), np.array([4]), np.array([2000]))
        self.assertEqual(alarm.tolist(), [True])


class ChooseTests(SimpleTestCase):
    def test_the_best_hit_rate_that_keeps_enough_floods_is_chosen(self):
        results = {
            "today": {"caught": 100, "hit_rate": 0.2},
            "own_50": {"caught": 85, "hit_rate": 0.3},
            "own_90": {"caught": 40, "hit_rate": 0.5},  # too few floods kept
        }
        self.assertEqual(cn.choose(results), "own_50")

    def test_replay_scores_only_the_years_before_the_final_test(self):
        probability = np.array([0.3, 0.3, 0.3])
        flooded = np.array([True, False, True])
        years = np.array([2000, 2001, 2018])
        result = cn.replay(
            probability, flooded, np.array(["Nairobi"] * 3), np.array([4, 4, 4]), years
        )

        self.assertEqual(result["years"], [2000, 2001])
        self.assertEqual(result["results"]["today"]["all"]["warnings"], 2)
        self.assertEqual(result["results"]["today"]["all"]["caught"], 1)
