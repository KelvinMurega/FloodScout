"""Tests for the RiskScore API, the placeholder scorer, the score_regions
command, and the per-region latest-score endpoint.
"""

import datetime as dt
from io import StringIO

from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from django.test import TestCase

from geodata.models import EnvironmentalData, GeospatialFeature, Region
from riskengine.management.commands.score_regions import start_of_day
from riskengine.ml.infer import (
    MODEL_VERSION,
    NEUTRAL_COMPONENT,
    RAINFALL_SATURATION_MM,
    infer,
)

from .models import RiskScore


def _square(x=36.8, y=-1.3, size=0.1):
    return MultiPolygon(
        Polygon(((x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)))
    )


class RiskScoreAPITests(APITestCase):
    def setUp(self):
        self.region = Region.objects.create(
            county_name="Nairobi", geom=_square(), area_size=696.0
        )
        RiskScore.objects.create(
            region=self.region, predicted_value=0.2, risk_category=RiskScore.RiskCategory.LOW
        )
        self.latest = RiskScore.objects.create(
            region=self.region, predicted_value=0.8, risk_category=RiskScore.RiskCategory.HIGH
        )

    def test_list_scores_for_region(self):
        response = self.client.get(f"/api/risk-scores/?region={self.region.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

    def test_latest_action_returns_most_recent_score(self):
        response = self.client.get(f"/api/risk-scores/latest/?region={self.region.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.latest.id)
        self.assertEqual(response.data["risk_category"], "high")

    def test_latest_action_404_when_no_scores_for_region(self):
        other = Region.objects.create(county_name="Kisumu", geom=_square(x=34.7, y=-0.1), area_size=100.0)
        response = self.client.get(f"/api/risk-scores/latest/?region={other.id}")
        self.assertEqual(response.status_code, 404)


# --- Placeholder scorer, score_regions, latest-by-region ---


SCORE_DATE = dt.date(2026, 4, 15)


def make_region(county_name: str, *, offset: float = 0.0) -> Region:
    """A Region with a throwaway 1x1 degree square for geometry."""
    square = Polygon.from_bbox((offset, offset, offset + 1.0, offset + 1.0))
    square.srid = 4326
    return Region.objects.create(
        county_name=county_name,
        geom=MultiPolygon(square, srid=4326),
        area_size=12345.0,
    )


class InferTests(TestCase):
    """infer() must always hand back a category the model field accepts."""

    def test_returns_a_valid_category_with_no_input_data(self):
        region = make_region("Nowhere")

        predicted_value, risk_category = infer(region, SCORE_DATE)

        self.assertIn(risk_category, RiskScore.RiskCategory.values)
        # Both components missing -> the documented neutral fallback.
        self.assertAlmostEqual(predicted_value, NEUTRAL_COMPONENT)
        self.assertEqual(risk_category, RiskScore.RiskCategory.MODERATE)

    def test_returns_a_valid_category_for_a_wet_lowland_region(self):
        region = make_region("Floodplain")
        GeospatialFeature.objects.create(
            region=region, elevation=0.0, slope=0.5, distance_to_river=50.0
        )
        for day_offset in range(7):
            EnvironmentalData.objects.create(
                region=region,
                record_date=SCORE_DATE - dt.timedelta(days=day_offset),
                rainfall_intensity=RAINFALL_SATURATION_MM / 7.0,
            )

        predicted_value, risk_category = infer(region, SCORE_DATE)

        self.assertIn(risk_category, RiskScore.RiskCategory.values)
        self.assertEqual(risk_category, RiskScore.RiskCategory.VERY_HIGH)
        self.assertAlmostEqual(predicted_value, 1.0)

    def test_returns_a_valid_category_for_a_dry_highland_region(self):
        region = make_region("Highland")
        GeospatialFeature.objects.create(
            region=region, elevation=3000.0, slope=20.0, distance_to_river=40000.0
        )
        EnvironmentalData.objects.create(
            region=region, record_date=SCORE_DATE, rainfall_intensity=0.0
        )

        predicted_value, risk_category = infer(region, SCORE_DATE)

        self.assertIn(risk_category, RiskScore.RiskCategory.values)
        self.assertEqual(risk_category, RiskScore.RiskCategory.LOW)
        self.assertAlmostEqual(predicted_value, 0.0)

    def test_rainfall_outside_the_window_is_ignored(self):
        region = make_region("StaleData")
        EnvironmentalData.objects.create(
            region=region,
            record_date=SCORE_DATE - dt.timedelta(days=30),
            rainfall_intensity=500.0,
        )

        predicted_value, risk_category = infer(region, SCORE_DATE)

        self.assertAlmostEqual(predicted_value, NEUTRAL_COMPONENT)
        self.assertIn(risk_category, RiskScore.RiskCategory.values)

    def test_every_category_produced_is_a_valid_choice(self):
        """Sweep elevations across the range; nothing may fall outside the enum."""
        for index, elevation in enumerate(range(0, 3500, 250)):
            region = make_region(f"Sweep {index}", offset=index * 2.0)
            GeospatialFeature.objects.create(
                region=region, elevation=float(elevation), slope=1.0, distance_to_river=100.0
            )
            _value, risk_category = infer(region, SCORE_DATE)
            self.assertIn(risk_category, RiskScore.RiskCategory.values)


class ScoreRegionsCommandTests(TestCase):
    def setUp(self):
        self.regions = [
            make_region("Alpha", offset=0.0),
            make_region("Beta", offset=2.0),
            make_region("Gamma", offset=4.0),
        ]

    def test_writes_one_row_per_region_stamped_with_the_model_version(self):
        call_command("score_regions", "--date", SCORE_DATE.isoformat(), stdout=StringIO())

        self.assertEqual(RiskScore.objects.count(), len(self.regions))
        self.assertEqual(
            set(RiskScore.objects.values_list("region_id", flat=True)),
            {region.pk for region in self.regions},
        )
        self.assertEqual(
            set(RiskScore.objects.values_list("model_version", flat=True)),
            {MODEL_VERSION},
        )

    def test_is_idempotent_for_the_same_date(self):
        call_command("score_regions", "--date", SCORE_DATE.isoformat(), stdout=StringIO())
        first_pass = {
            score.region_id: (score.pk, score.predicted_value, score.risk_category)
            for score in RiskScore.objects.all()
        }

        call_command("score_regions", "--date", SCORE_DATE.isoformat(), stdout=StringIO())

        self.assertEqual(RiskScore.objects.count(), len(self.regions))
        second_pass = {
            score.region_id: (score.pk, score.predicted_value, score.risk_category)
            for score in RiskScore.objects.all()
        }
        # Same rows updated in place, not a second set inserted.
        self.assertEqual(first_pass, second_pass)

    def test_a_different_date_adds_a_separate_set_of_rows(self):
        call_command("score_regions", "--date", SCORE_DATE.isoformat(), stdout=StringIO())
        call_command(
            "score_regions",
            "--date",
            (SCORE_DATE + dt.timedelta(days=1)).isoformat(),
            stdout=StringIO(),
        )

        self.assertEqual(RiskScore.objects.count(), len(self.regions) * 2)

    def test_defaults_to_today(self):
        call_command("score_regions", stdout=StringIO())

        self.assertEqual(RiskScore.objects.count(), len(self.regions))
        self.assertEqual(
            RiskScore.objects.filter(
                prediction_date=start_of_day(timezone.localdate())
            ).count(),
            len(self.regions),
        )


class LatestByRegionEndpointTests(APITestCase):
    def setUp(self):
        self.regions = [
            make_region("Alpha", offset=0.0),
            make_region("Beta", offset=2.0),
            make_region("Gamma", offset=4.0),
        ]
        # Two days of scores, so "latest per region" has something to choose.
        call_command("score_regions", "--date", SCORE_DATE.isoformat(), stdout=StringIO())
        self.latest_date = SCORE_DATE + dt.timedelta(days=1)
        call_command("score_regions", "--date", self.latest_date.isoformat(), stdout=StringIO())

    def test_returns_exactly_one_row_per_region(self):
        response = self.client.get(reverse("risk-score-latest-by-region"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), len(self.regions))
        self.assertEqual(
            sorted(row["region"] for row in response.data),
            sorted(region.pk for region in self.regions),
        )

    def test_each_row_is_the_most_recent_score_for_its_region(self):
        response = self.client.get(reverse("risk-score-latest-by-region"))

        expected = start_of_day(self.latest_date)
        for row in response.data:
            self.assertEqual(dt.datetime.fromisoformat(row["prediction_date"]), expected)

    def test_rows_expose_the_model_version(self):
        response = self.client.get(reverse("risk-score-latest-by-region"))

        for row in response.data:
            self.assertEqual(row["model_version"], MODEL_VERSION)

    def test_all_regions_come_back_in_a_single_query(self):
        """The whole point: one request and one query, not one per county."""
        with self.assertNumQueries(1):
            self.client.get(reverse("risk-score-latest-by-region"))
