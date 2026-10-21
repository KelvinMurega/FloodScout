"""The written situation briefing and its hotspots (riskengine/briefing.py)."""

import datetime as dt

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APITestCase

from .briefing import HEAVY_RAIN_MM, Place, briefing, hotspots
from .models import RiskForecast, RiskScore
from .scoring import start_of_day
from .tests import make_region

DAY = dt.date(2026, 10, 20)


def week(*days):
    """Forecast days from (category, times, wettest mm) tuples, from tomorrow."""
    return [(DAY + dt.timedelta(days=n + 1), *day) for n, day in enumerate(days)]


class HotspotTests(SimpleTestCase):
    def test_each_rule_and_its_reason(self):
        places = [
            Place(1, "Rising", "high", 1.5, change_7d=0.6),
            Place(2, "Heavy", "high", 1.2, week=week(("high", 1.3, HEAVY_RAIN_MM + 4))),
            Place(3, "Turning", "high", 1.4, week=week(("high", 1.6, 5), ("very_high", 2.2, 9))),
            Place(4, "Quiet", "high", 1.1, change_7d=0.0, week=week(("high", 1.1, 2))),
            Place(5, "Top", "very_high", 3.0),
            Place(6, "AlreadyVeryHigh", "very_high", 2.9, week=week(("very_high", 3.0, 1))),
            Place(7, "Third", "very_high", 2.8),
        ]
        found = {h["name"]: [r["kind"] for r in h["reasons"]] for h in hotspots(places)}
        self.assertEqual(found["Rising"], ["rising_fast"])
        self.assertEqual(found["Heavy"], ["heavy_rain"])
        self.assertEqual(found["Turning"], ["forecast_very_high"])
        self.assertEqual(found["Top"], ["top"])
        # Very High already: not "forecast to reach" it, but among the highest.
        self.assertEqual(found["AlreadyVeryHigh"], ["top"])
        self.assertNotIn("Quiet", found)

    def test_heavy_rain_comes_first(self):
        places = [
            Place(1, "Top", "very_high", 3.0),
            Place(2, "Heavy", "high", 1.2, week=week(("high", 1.3, 80))),
        ]
        self.assertEqual([h["name"] for h in hotspots(places)][0], "Heavy")


class BriefingTextTests(SimpleTestCase):
    def test_reads_the_numbers_in_order(self):
        places = [
            Place(
                1,
                "Garissa",
                "very_high",
                2.7,
                change_7d=0.1,
                rain_ratio=1.5,
                week=week(("very_high", 2.8, 6)),
            ),
            Place(
                2,
                "Kisumu",
                "high",
                1.6,
                change_7d=0.4,
                rain_ratio=0.5,
                week=week(("very_high", 2.1, 55)),
            ),
            Place(
                3,
                "Samburu",
                "high",
                1.2,
                change_7d=-0.3,
                rain_ratio=0.4,
                week=week(("high", 1.2, 0)),
            ),
        ]
        body = briefing(places, date=DAY, area="Kenya", unit="county", oni=2.2)
        self.assertEqual(body["headline"], "1 county of 3 at Very High flood risk")
        text = body["paragraphs"]
        self.assertEqual(
            text[0],
            "On Tuesday 20 October, 1 county in Kenya is at Very High risk and 2 counties at "
            "High. The highest are Garissa (2.7×), Kisumu (1.6×) and Samburu (1.2×).",
        )
        self.assertEqual(
            text[1],
            "Risk rose in 1 county over the last 7 days, most in Kisumu (up 0.4×), and fell in 1.",
        )
        self.assertIn("puts 2 counties at Very High on at least one day, against 1 today", text[2])
        self.assertIn(
            "The biggest rise is expected in Kisumu, to 2.1× by Wednesday 21 October", text[2]
        )
        self.assertIn("The heaviest rain forecast is in Kisumu: 55 mm", text[3])
        self.assertIn("1 county has a forecast day of 50 mm or more", text[3])
        self.assertIn("Most of this risk comes from El Niño conditions (ONI 2.2)", text[4])
        self.assertIn("2 of 3 had less than 80%", text[4])

    def test_la_nina_and_no_forecast(self):
        body = briefing(
            [Place(1, "A", "high", 1.0, rain_ratio=1.0)],
            date=DAY,
            area="Kenya",
            unit="county",
            oni=-1.0,
        )
        self.assertEqual(body["headline"], "No county at Very High flood risk")
        self.assertIn("La Niña conditions (ONI -1.0)", body["paragraphs"][-1])

    def test_nothing_scored(self):
        body = briefing([], date=DAY, area="Kenya", unit="county", oni=None)
        self.assertEqual(body["paragraphs"], [])
        self.assertEqual(body["hotspots"], [])


class BriefingAPITests(APITestCase):
    def test_built_from_the_stored_scores_and_forecast(self):
        alpha = make_region("Alpha")
        RiskScore.objects.create(
            region=alpha,
            prediction_date=start_of_day(DAY),
            predicted_value=0.3,
            risk_category=RiskScore.RiskCategory.VERY_HIGH,
        )
        RiskForecast.objects.create(
            region=alpha,
            issued_on=DAY,
            target_date=DAY + dt.timedelta(days=1),
            predicted_value=0.31,
            risk_category=RiskScore.RiskCategory.VERY_HIGH,
            rain_forecast_max_24h=70.0,
            model_version="test",
        )
        response = self.client.get("/api/briefing/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["date"], "2026-10-20")
        self.assertEqual(response.data["forecast_issued_on"], "2026-10-20")
        self.assertEqual(response.data["hotspots"][0]["reasons"][0]["kind"], "heavy_rain")

    def test_an_empty_database(self):
        response = self.client.get("/api/briefing/", {"level": "sub_county"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["paragraphs"], [])
