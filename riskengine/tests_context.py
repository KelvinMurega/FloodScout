"""The map's theme values: risk-scores/context/ and riskengine/context.py."""

import datetime as dt
from unittest import mock

from rest_framework import status
from rest_framework.test import APITestCase

from geodata.models import EnvironmentalData, Region

from .ml.features import WINDOW_DAYS
from .ml.infer import times_usual
from .models import RiskScore
from .scoring import start_of_day
from .tests import make_region

DAY = dt.date(2026, 10, 20)
FAKE_MODEL = {"normals": {"Alpha": {10: 100.0}, "Beta": {10: 50.0}}}


def score(region, day, value):
    return RiskScore.objects.create(
        region=region,
        prediction_date=start_of_day(day),
        predicted_value=value,
        risk_category=RiskScore.RiskCategory.HIGH,
    )


def rain(region, end, mm_per_day, days=WINDOW_DAYS):
    EnvironmentalData.objects.bulk_create(
        EnvironmentalData(
            region=region, record_date=end - dt.timedelta(days=n), rainfall_intensity=mm_per_day
        )
        for n in range(days)
    )


@mock.patch("riskengine.ml.infer._MODEL", FAKE_MODEL)
class MapContextAPITests(APITestCase):
    def setUp(self):
        self.alpha = make_region("Alpha", offset=0.0)
        self.beta = make_region("Beta", offset=2.0)
        score(self.alpha, DAY, 0.30)
        score(self.alpha, DAY - dt.timedelta(days=7), 0.15)
        score(self.beta, DAY, 0.12)
        rain(self.alpha, DAY, 5.0)  # 150 mm against a normal of 100
        rain(self.beta, DAY, 1.0, days=WINDOW_DAYS - 10)  # too gappy to sum

    def rows(self, **params):
        response = self.client.get("/api/risk-scores/context/", params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data, {row["region"]: row for row in response.data["regions"]}

    def test_newest_day_by_default(self):
        body, _rows = self.rows()
        self.assertEqual(body["date"], DAY.isoformat())
        self.assertEqual(body["change_days"], 7)

    def test_change_over_the_week_and_rain_against_normal(self):
        _body, rows = self.rows()
        alpha = rows[self.alpha.id]
        self.assertEqual(alpha["times_usual"], times_usual(0.30))
        self.assertAlmostEqual(alpha["change_7d"], times_usual(0.30) - times_usual(0.15), places=1)
        self.assertEqual(alpha["rain_30d"], 150.0)
        self.assertEqual(alpha["rain_30d_normal"], 100.0)
        self.assertEqual(alpha["rain_30d_ratio"], 1.5)

    def test_unknown_values_are_null_not_zero(self):
        _body, rows = self.rows()
        beta = rows[self.beta.id]
        # No score a week before, and too many days of rain missing.
        self.assertIsNone(beta["change_7d"])
        self.assertIsNone(beta["rain_30d"])
        self.assertIsNone(beta["rain_30d_ratio"])

    def test_an_earlier_day(self):
        body, rows = self.rows(date=(DAY - dt.timedelta(days=7)).isoformat())
        self.assertEqual(body["date"], "2026-10-13")
        self.assertEqual(list(rows), [self.alpha.id])

    def test_region_filters_apply(self):
        sub = Region.objects.create(
            name="Alpha East",
            county_name="Alpha",
            level=Region.Level.SUB_COUNTY,
            parent=self.alpha,
            geom=self.alpha.geom,
            area_size=10.0,
        )
        score(sub, DAY, 0.2)
        _body, rows = self.rows(level="sub_county")
        self.assertEqual(list(rows), [sub.id])

    def test_no_scores_gives_an_empty_list(self):
        RiskScore.objects.all().delete()
        body, rows = self.rows()
        self.assertIsNone(body["date"])
        self.assertEqual(rows, {})

    def test_malformed_date_is_a_400(self):
        response = self.client.get("/api/risk-scores/context/", {"date": "20-10-2026"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
