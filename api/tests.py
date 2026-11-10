"""Tests for the permission boundary: reading is open to everyone, and
rescoring is for a System Administrator only."""

import datetime as dt
from types import SimpleNamespace
from unittest import mock

from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase
from rest_framework.throttling import AnonRateThrottle

from accounts.models import User
from geodata.models import Region
from riskengine.models import RiskScore
from riskengine.views import _worked_out

PASSWORD = "a-long-test-passphrase"
SCORE_DATE = dt.date(2026, 4, 15)


def make_region(county_name: str, *, offset: float = 0.0) -> Region:
    square = Polygon.from_bbox((offset, offset, offset + 1.0, offset + 1.0))
    square.srid = 4326
    return Region.objects.create(
        county_name=county_name,
        geom=MultiPolygon(square, srid=4326),
        area_size=12345.0,
    )


# The scorer has its own tests (riskengine/tests.py); here it is replaced so
# these tests are only about who may call the endpoint.
@mock.patch("riskengine.scoring.infer", return_value=(0.12, RiskScore.RiskCategory.HIGH))
class RescorePermissionTests(APITestCase):
    def setUp(self):
        self.url = reverse("risk-score-rescore")
        self.regions = [make_region("Alpha"), make_region("Beta", offset=2.0)]
        self.admin = User.objects.create_user(
            "otieno", password=PASSWORD, role=User.Role.ADMINISTRATOR
        )
        self.end_user = User.objects.create_user("wanjiku", password=PASSWORD)

    def sign_in(self, user):
        token = Token.objects.create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_an_anonymous_request_is_refused_with_401(self, infer):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(RiskScore.objects.exists())
        infer.assert_not_called()

    def test_a_made_up_token_is_refused_with_401(self, infer):
        self.client.credentials(HTTP_AUTHORIZATION="Token not-a-real-token")

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(RiskScore.objects.exists())

    def test_an_end_user_is_refused_with_403(self, infer):
        self.sign_in(self.end_user)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(RiskScore.objects.exists())
        infer.assert_not_called()

    def test_an_administrator_rescores_every_region(self, infer):
        self.sign_in(self.admin)

        response = self.client.post(self.url, {"date": SCORE_DATE.isoformat()}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["date"], SCORE_DATE.isoformat())
        self.assertEqual(response.data["regions"], 2)
        self.assertEqual(response.data["created"], 2)
        self.assertEqual(response.data["categories"], {RiskScore.RiskCategory.HIGH: 2})
        self.assertEqual(RiskScore.objects.count(), 2)
        self.assertEqual(
            set(RiskScore.objects.values_list("model_version", flat=True)),
            {response.data["model_version"]},
        )

    def test_rescoring_the_same_date_updates_rows_in_place(self, infer):
        self.sign_in(self.admin)
        self.client.post(self.url, {"date": SCORE_DATE.isoformat()}, format="json")

        response = self.client.post(self.url, {"date": SCORE_DATE.isoformat()}, format="json")

        self.assertEqual(response.data["created"], 0)
        self.assertEqual(response.data["updated"], 2)
        self.assertEqual(RiskScore.objects.count(), 2)

    def test_a_bad_date_is_refused_with_400(self, infer):
        self.sign_in(self.admin)

        response = self.client.post(self.url, {"date": "15 April"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(RiskScore.objects.exists())

    def test_with_no_regions_it_reports_a_conflict(self, infer):
        Region.objects.all().delete()
        self.sign_in(self.admin)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_the_endpoint_does_not_answer_get(self, infer):
        self.sign_in(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class OpenReadTests(APITestCase):
    """The dashboard needs no account: every read endpoint answers anonymously."""

    READ_ROUTES = [
        "health-check",
        "region-list",
        "focus-region-list",
        "geospatial-feature-list",
        "environmental-data-list",
        "risk-score-list",
        "risk-score-latest-by-region",
        "risk-outlook-list",
        "risk-outlook-months",
    ]

    def test_read_endpoints_are_open_to_anonymous_requests(self):
        for name in self.READ_ROUTES:
            with self.subTest(route=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, status.HTTP_200_OK)

    def test_anonymous_writes_to_read_endpoints_are_refused(self):
        for name in ["region-list", "risk-score-list", "risk-outlook-list"]:
            with self.subTest(route=name):
                self.assertEqual(
                    self.client.post(reverse(name), {}).status_code,
                    status.HTTP_401_UNAUTHORIZED,
                )


class LimitTests(APITestCase):
    """Every request counts against its client, and bare lists come in pages."""

    def setUp(self):
        cache.clear()

    def test_anonymous_requests_are_rate_limited(self):
        # The suite runs without the general limits (settings.TESTING).
        with mock.patch.dict(AnonRateThrottle.THROTTLE_RATES, {"anon": "3/min"}):
            codes = [self.client.get(reverse("region-list")).status_code for _ in range(4)]
        self.assertEqual(codes, [status.HTTP_200_OK] * 3 + [status.HTTP_429_TOO_MANY_REQUESTS])
        # The health check is a plain view, so an uptime monitor is never limited.
        self.assertEqual(self.client.get(reverse("health-check")).status_code, status.HTTP_200_OK)

    def test_every_score_comes_in_pages_but_one_regions_history_whole(self):
        region = make_region("Alpha")
        for day in range(3):
            RiskScore.objects.create(
                region=region,
                prediction_date=dt.datetime(2026, 4, 10 + day, tzinfo=dt.UTC),
                predicted_value=0.1,
                risk_category=RiskScore.RiskCategory.LOW,
            )
        paged = self.client.get(reverse("risk-score-list"), {"page_size": 2})
        self.assertEqual((paged.data["count"], len(paged.data["results"])), (3, 2))
        history = self.client.get(reverse("risk-score-list"), {"region": region.id})
        self.assertEqual(len(history.data), 3)

    def test_a_worked_out_explanation_is_kept_until_the_score_changes(self):
        calls = []

        def build():
            calls.append(1)
            return {"parts": []}

        score = SimpleNamespace(id=7, predicted_value=0.2)
        for _ in range(2):
            self.assertEqual(_worked_out("explain", score, {"version": "v1"}, build), {"parts": []})
        self.assertEqual(len(calls), 1)
        score.predicted_value = 0.3  # rescored after new rainfall
        _worked_out("explain", score, {"version": "v1"}, build)
        self.assertEqual(len(calls), 2)
