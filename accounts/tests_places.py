"""Warnings for saved places besides home (accounts/places.py)."""

import datetime as dt

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from alerts.engine import run_alerts
from alerts.models import Alert, Level, Subscriber, Unsubscription
from riskengine.models import RiskScore
from riskengine.scoring import start_of_day

from .models import SavedPlace, User
from .tests_home import LOCMEM, PASSWORD, last_code, places

URL = "/api/auth/me/places/"


@override_settings(EMAIL_BACKEND=LOCMEM, AT_API_KEY="")
class SavedPlaceTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.county, self.nyando, self.seme = places()
        self.user = User.objects.create_user("akinyi", email="a@example.com", password=PASSWORD)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=self.user).key}")

    def verified(self):
        User.objects.filter(pk=self.user.pk).update(email_verified=True)

    def save(self, **body):
        return self.client.post(URL, {"label": "School", **body}, format="json")

    def test_a_pin_on_the_map_finds_the_sub_county_and_starts_warnings(self):
        self.verified()
        response = self.save(lat=-0.5123, lon=35.5123)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual((response.data["region_name"], response.data["receiving"]), ("Seme", True))
        sub = Subscriber.objects.get(saved_place_id=response.data["id"])
        self.assertCountEqual(sub.regions.all(), [self.seme, self.county])
        self.assertFalse(sub.is_home)
        self.assertEqual(self.client.get(URL).data[0]["label"], "School")

    def test_a_spot_outside_kenya_or_no_name_is_refused(self):
        self.assertEqual(self.save(lat=10.0, lon=10.0).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            self.save(label=" ", lat=-0.5, lon=34.5).status_code, status.HTTP_400_BAD_REQUEST
        )

    def test_at_most_five_places(self):
        for n in range(SavedPlace.LIMIT):
            self.assertEqual(self.save(label=f"P{n}", lat=-0.5, lon=34.5).status_code, 201)
        self.assertEqual(self.save(label="One more", lat=-0.5, lon=34.5).status_code, 400)

    def test_warnings_wait_for_the_email_and_start_once_it_is_confirmed(self):
        place = self.save(lat=-0.5, lon=34.5).data
        self.assertFalse(place["receiving"])
        self.client.post("/api/auth/verify/", {"channel": "email"})
        self.client.post("/api/auth/verify/", {"channel": "email", "code": last_code(self.user)})
        self.assertTrue(Subscriber.objects.get(saved_place_id=place["id"]).confirmed)

    def test_the_rules_warn_about_a_saved_place_by_its_name(self):
        self.verified()
        self.save(lat=-0.5, lon=34.5)
        RiskScore.objects.create(
            region=self.nyando,
            prediction_date=start_of_day(timezone.localdate()),
            predicted_value=0.3,
            risk_category=RiskScore.RiskCategory.VERY_HIGH,
        )
        run_alerts()
        self.assertEqual(mail.outbox[0].subject, "School: Flood watch for Nyando (Kisumu County)")
        self.assertIn('your saved place "School"', mail.outbox[0].body)

    def test_an_alert_in_force_is_sent_when_the_place_is_saved(self):
        self.verified()
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
        self.save(lat=-0.5, lon=34.5)
        self.assertEqual(mail.outbox[0].subject, "School: Flood warning for Kisumu County")

    def test_turning_off_changing_and_removing(self):
        self.verified()
        place = self.save(lat=-0.5, lon=34.5).data
        url = f"{URL}{place['id']}/"
        self.client.patch(url, {"alert_level": "warning"}, format="json")
        self.assertEqual(Subscriber.objects.get(saved_place_id=place["id"]).min_level, "warning")
        self.client.patch(url, {"alerts_on": False}, format="json")
        self.assertFalse(Subscriber.objects.filter(saved_place_id=place["id"]).exists())
        self.client.patch(url, {"alerts_on": True, "region": self.seme.id}, format="json")
        sub = Subscriber.objects.get(saved_place_id=place["id"])
        self.assertCountEqual(sub.regions.all(), [self.seme, self.county])
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(SavedPlace.objects.exists())
        self.assertFalse(Subscriber.objects.filter(user=self.user).exists())
        self.assertTrue(
            Unsubscription.objects.filter(user=self.user, how=Unsubscription.How.PLACE_OFF).exists()
        )

    def test_stopping_by_the_email_link_turns_the_place_off_for_good(self):
        self.verified()
        place = self.save(lat=-0.5, lon=34.5).data
        token = Subscriber.objects.get(saved_place_id=place["id"]).token
        self.client.post(f"/api/alerts/unsubscribe/{token}/")
        self.assertFalse(SavedPlace.objects.get(pk=place["id"]).alerts_on)
        # A later sync (here, saving the home) does not bring it back.
        self.client.put("/api/auth/me/home/", {"lat": -0.5, "lon": 34.5}, format="json")
        self.assertFalse(Subscriber.objects.filter(saved_place_id=place["id"]).exists())

    def test_one_click_alerts_leave_saved_places_alone(self):
        self.verified()
        place = self.save(lat=-0.5, lon=34.5).data
        self.client.post("/api/alerts/subscribe/me/", {"regions": [self.seme.id]}, format="json")
        sub = Subscriber.objects.get(saved_place_id=place["id"])
        self.assertCountEqual(sub.regions.all(), [self.nyando, self.county])

    def test_someone_elses_place_is_not_found(self):
        other = User.objects.create_user("other", password=PASSWORD)
        theirs = SavedPlace.objects.create(user=other, label="Farm", region=self.nyando)
        response = self.client.delete(f"{URL}{theirs.pk}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
