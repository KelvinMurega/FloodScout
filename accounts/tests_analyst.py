"""The Analyst role: reads the analytics, changes nothing (accounts/models.py)."""

from unittest import mock

from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import User


class AnalystTests(APITestCase):
    def setUp(self):
        self.analyst = User.objects.create_user("officer", password="x", role=User.Role.ANALYST)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=self.analyst).key}")

    def test_an_analyst_reads_the_analytics(self):
        with mock.patch("riskengine.views.system_health", return_value={}):
            response = self.client.get("/api/admin/analytics/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_an_analyst_changes_nothing(self):
        for method, url in (
            ("get", "/api/admin/users/"),
            ("get", "/api/admin/activity/"),
            ("get", "/api/alerts/subscribers/"),
            ("post", "/api/risk-scores/rescore/"),
            ("post", "/api/alerts/run/"),
        ):
            with self.subTest(url=url):
                self.assertEqual(getattr(self.client, method)(url).status_code, status.HTTP_403_FORBIDDEN)

    def test_me_says_what_an_analyst_and_an_end_user_may_see(self):
        me = self.client.get("/api/auth/me/").data
        self.assertEqual((me["role"], me["can_see_analytics"], me["is_administrator"]), ("analyst", True, False))
        user = User.objects.create_user("someone", password="x")
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
        self.assertFalse(self.client.get("/api/auth/me/").data["can_see_analytics"])

    def test_an_end_user_cannot_read_the_analytics(self):
        user = User.objects.create_user("someone", password="x")
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
        self.assertEqual(self.client.get("/api/admin/analytics/").status_code, status.HTTP_403_FORBIDDEN)
