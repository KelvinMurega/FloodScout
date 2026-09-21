from django.contrib.gis.geos import MultiPolygon, Polygon
from rest_framework.test import APITestCase

from geodata.models import Region

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
