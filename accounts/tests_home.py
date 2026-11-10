"""Warnings for where a user lives (accounts/home.py), and confirming the
email with a code at sign-up and sign-in (accounts/verification.py)."""

import datetime as dt
import json
from unittest import mock

from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from alerts.engine import run_alerts
from alerts.models import Alert, Level, Subscriber
from geodata.models import Region
from riskengine.models import RiskScore
from riskengine.scoring import start_of_day

from . import home
from .models import ContactCode, User

PASSWORD = "a-long-test-passphrase"
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


def square(west, south, size=1.0):
    poly = Polygon.from_bbox((west, south, west + size, south + size))
    poly.srid = 4326
    return MultiPolygon(poly, srid=4326)


def places():
    """Kisumu county with two sub-counties side by side."""
    county = Region.objects.create(
        county_name="Kisumu", geom=square(34.0, -1.0, 2.0), area_size=2000
    )
    nyando = Region.objects.create(
        name="Nyando",
        county_name="Kisumu",
        level=Region.Level.SUB_COUNTY,
        parent=county,
        geom=square(34.0, -1.0),
        area_size=500,
    )
    seme = Region.objects.create(
        name="Seme",
        county_name="Kisumu",
        level=Region.Level.SUB_COUNTY,
        parent=county,
        geom=square(35.0, -1.0),
        area_size=500,
    )
    return county, nyando, seme


def last_code(user):
    return ContactCode.objects.get(user=user, channel="email").code


@override_settings(EMAIL_BACKEND=LOCMEM, AT_API_KEY="")
class SignUpCodeTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_the_code_comes_with_the_account_and_confirms_the_email(self):
        response = self.client.post(
            "/api/auth/register/",
            {"username": "akinyi", "email": "a@example.com", "password": PASSWORD},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            (response.data["email_verified"], response.data["code_sent"]), (False, True)
        )
        user = User.objects.get(username="akinyi")
        self.assertIn(last_code(user), mail.outbox[0].subject)

        self.client.credentials(HTTP_AUTHORIZATION=f"Token {response.data['token']}")
        wrong = self.client.post("/api/auth/verify/", {"channel": "email", "code": "000000"})
        self.assertEqual(wrong.status_code, status.HTTP_400_BAD_REQUEST)
        right = self.client.post("/api/auth/verify/", {"channel": "email", "code": last_code(user)})
        self.assertEqual(right.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_signing_in_unconfirmed_sends_a_code_but_not_twice_a_minute(self):
        User.objects.create_user("akinyi", email="a@example.com", password=PASSWORD)
        for _ in range(2):
            response = self.client.post(
                "/api/auth/token/", {"username": "akinyi", "password": PASSWORD}
            )
            self.assertTrue(response.data["code_sent"])
        self.assertEqual(len(mail.outbox), 1)

    def test_a_confirmed_email_gets_no_code(self):
        User.objects.create_user(
            "akinyi", email="a@example.com", password=PASSWORD, email_verified=True
        )
        response = self.client.post(
            "/api/auth/token/", {"username": "akinyi", "password": PASSWORD}
        )
        self.assertEqual(
            (response.data["email_verified"], response.data["code_sent"]), (True, False)
        )
        self.assertEqual(mail.outbox, [])

    def test_changing_the_email_sends_a_code_to_the_new_one(self):
        user = User.objects.create_user(
            "akinyi", email="a@example.com", password=PASSWORD, email_verified=True
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
        response = self.client.patch("/api/auth/me/", {"email": "new@example.com"}, format="json")
        self.assertTrue(response.data["code_sent"])
        self.assertEqual(mail.outbox[0].to, ["new@example.com"])

    def test_a_code_for_an_old_email_does_not_confirm_a_new_one(self):
        user = User.objects.create_user("akinyi", email="a@example.com", password=PASSWORD)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
        self.client.post("/api/auth/verify/", {"channel": "email"})
        code = last_code(user)
        User.objects.filter(pk=user.pk).update(email="other@example.com")
        response = self.client.post("/api/auth/verify/", {"channel": "email", "code": code})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        user.refresh_from_db()
        self.assertFalse(user.email_verified)

    def test_asking_for_a_new_code_keeps_the_wrong_tries(self):
        user = User.objects.create_user("akinyi", email="a@example.com", password=PASSWORD)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
        self.client.post("/api/auth/verify/", {"channel": "email"})
        for _ in range(3):
            self.client.post("/api/auth/verify/", {"channel": "email", "code": "000000"})
        self.client.post("/api/auth/verify/", {"channel": "email"})
        self.assertEqual(ContactCode.objects.get(user=user).attempts, 3)

    def test_one_address_gets_at_most_five_codes_a_day(self):
        # Six accounts, all naming the same stranger's address.
        for i in range(6):
            user = User.objects.create_user(f"u{i}", email="victim@example.com", password=PASSWORD)
            self.client.credentials(
                HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}"
            )
            response = self.client.post("/api/auth/verify/", {"channel": "email"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("codes it can get today", response.data["detail"])
        self.assertEqual(len(mail.outbox), 5)


@override_settings(EMAIL_BACKEND=LOCMEM, AT_API_KEY="")
class HomeAlertTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.county, self.nyando, self.seme = places()
        self.user = User.objects.create_user("akinyi", email="a@example.com", password=PASSWORD)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=self.user).key}"
        )

    def set_home(self, **body):
        return self.client.put("/api/auth/me/home/", body, format="json")

    def test_a_point_finds_the_sub_county_rounded_to_about_100_m(self):
        response = self.set_home(address="Ahero", lat=-0.5123456, lon=34.5123456)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            (response.data["region_name"], response.data["county_name"]), ("Nyando", "Kisumu")
        )
        self.user.refresh_from_db()
        self.assertEqual((self.user.home_point.y, self.user.home_point.x), (-0.512, 34.512))

    def test_a_place_outside_the_sub_counties_is_refused(self):
        response = self.set_home(lat=10.0, lon=10.0)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_choosing_the_sub_county_corrects_the_point(self):
        response = self.set_home(lat=-0.5, lon=34.5, region=self.seme.id)
        self.assertEqual(response.data["region_name"], "Seme")

    def test_an_unconfirmed_email_waits_and_confirming_it_starts_the_warnings(self):
        summary = self.set_home(lat=-0.5, lon=34.5).data
        self.assertEqual((summary["receiving"], summary["email_waiting"]), (False, True))
        sub = Subscriber.objects.get(user=self.user, is_home=True)
        self.assertFalse(sub.confirmed)

        self.client.post("/api/auth/verify/", {"channel": "email"})
        done = self.client.post(
            "/api/auth/verify/", {"channel": "email", "code": last_code(self.user)}
        )
        self.assertTrue(done.data["home"]["receiving"])
        sub.refresh_from_db()
        self.assertTrue(sub.confirmed)
        self.assertCountEqual(sub.regions.all(), [self.nyando, self.county])

    def test_the_rules_warn_the_person_where_they_live_without_asking(self):
        User.objects.filter(pk=self.user.pk).update(email_verified=True)
        self.set_home(lat=-0.5, lon=34.5)
        RiskScore.objects.create(
            region=self.nyando,
            prediction_date=start_of_day(timezone.localdate()),
            predicted_value=0.3,
            risk_category=RiskScore.RiskCategory.VERY_HIGH,
        )
        run_alerts()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject, "Where you live: Flood watch for Nyando (Kisumu County)"
        )
        self.assertIn("is where you live", mail.outbox[0].body)

    def test_an_alert_already_in_force_is_sent_when_the_home_is_set(self):
        User.objects.filter(pk=self.user.pk).update(email_verified=True)
        Alert.objects.create(
            region=self.county,
            level=Level.WARNING,
            basis="observed",
            headline="Flood warning for Kisumu County",
            description="d",
            instruction="i",
            onset=dt.date.today(),
            expires=timezone.now() + dt.timedelta(hours=36),
        )
        self.set_home(lat=-0.5, lon=34.5)
        self.assertEqual(mail.outbox[0].subject, "Where you live: Flood warning for Kisumu County")

    def test_turning_warnings_off_or_clearing_the_home_removes_the_subscription(self):
        User.objects.filter(pk=self.user.pk).update(email_verified=True)
        self.set_home(lat=-0.5, lon=34.5)
        self.assertTrue(Subscriber.objects.filter(is_home=True).exists())
        self.set_home(alerts_on=False)
        self.assertFalse(Subscriber.objects.filter(is_home=True).exists())
        self.set_home(alerts_on=True)
        self.assertTrue(Subscriber.objects.filter(is_home=True).exists())
        self.client.delete("/api/auth/me/home/")
        self.assertFalse(Subscriber.objects.filter(is_home=True).exists())

    def test_the_level_and_a_new_email_follow_the_profile(self):
        User.objects.filter(pk=self.user.pk).update(email_verified=True)
        self.set_home(lat=-0.5, lon=34.5, alert_level="warning")
        self.assertEqual(Subscriber.objects.get(is_home=True).min_level, "warning")
        self.client.patch("/api/auth/me/", {"email": "new@example.com"}, format="json")
        sub = Subscriber.objects.get(is_home=True)
        # The new email is not confirmed yet: nothing goes to it until it is.
        self.assertEqual((sub.email, sub.confirmed), ("", False))

    def test_stopping_the_home_subscription_turns_automatic_warnings_off(self):
        User.objects.filter(pk=self.user.pk).update(email_verified=True)
        self.set_home(lat=-0.5, lon=34.5)
        sub = Subscriber.objects.get(is_home=True)
        listed = self.client.get("/api/auth/me/subscriptions/")
        self.assertTrue(listed.data[0]["is_home"])
        self.client.delete(f"/api/auth/me/subscriptions/{sub.id}/")
        self.user.refresh_from_db()
        self.assertFalse(self.user.home_alerts)

    def test_one_click_alerts_do_not_touch_the_home_subscription(self):
        User.objects.filter(pk=self.user.pk).update(email_verified=True)
        self.set_home(lat=-0.5, lon=34.5)
        self.client.post("/api/alerts/subscribe/me/", {"regions": [self.seme.id]}, format="json")
        home_sub = Subscriber.objects.get(is_home=True)
        self.assertCountEqual(home_sub.regions.all(), [self.nyando, self.county])
        self.assertEqual(Subscriber.objects.filter(user=self.user).count(), 2)


class GeocodeTests(APITestCase):
    def setUp(self):
        cache.clear()
        _county, self.nyando, _seme = places()
        user = User.objects.create_user("akinyi", password=PASSWORD)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")

    def test_search_asks_nominatim_for_kenya_and_names_the_sub_county(self):
        answer = [{"display_name": "Ahero, Kisumu, Kenya", "lat": "-0.5", "lon": "34.5"}]

        class Reply:
            def read(self):
                return json.dumps(answer).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with (
            mock.patch("urllib.request.urlopen", return_value=Reply()) as call,
            mock.patch.object(home, "NOMINATIM_SPACING", 0),
        ):
            response = self.client.get("/api/auth/me/home/search/", {"q": "Ahero"})
            again = self.client.get("/api/auth/me/home/search/", {"q": "ahero"})
        request = call.call_args.args[0]
        self.assertIn("countrycodes=ke", request.full_url)
        self.assertIn("FloodScout", request.get_header("User-agent"))
        self.assertEqual(response.data[0]["region_name"], "Nyando")
        self.assertEqual(call.call_count, 1)  # the second came from the cache
        self.assertEqual(again.data, response.data)

    def test_too_short_a_search(self):
        self.assertEqual(
            self.client.get("/api/auth/me/home/search/", {"q": "a"}).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_locate_names_the_sub_county_of_a_point(self):
        response = self.client.post("/api/auth/me/home/locate/", {"lat": -0.5, "lon": 34.5})
        self.assertEqual(response.data["region_name"], "Nyando")
