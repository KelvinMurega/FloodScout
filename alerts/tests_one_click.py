"""A signed-in user's one-click "Get alerts" (alerts/views.py subscribe_me)."""

import datetime as dt

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from accounts.models import User
from riskengine.tests import make_region

from .models import Alert, Level, Subscriber

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
URL = "/api/alerts/subscribe/me/"


@override_settings(EMAIL_BACKEND=LOCMEM, AT_API_KEY="")
class OneClickTests(APITestCase):
    def setUp(self):
        cache.clear()  # the alerts throttle counts across tests otherwise
        self.alpha = make_region("Alpha")
        self.beta = make_region("Beta", offset=2.0)
        self.user = User.objects.create_user("wanjiku", email="w@example.com", password="x")

    def sign_in(self, user=None):
        user = user or self.user
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def press(self, *regions, **extra):
        return self.client.post(URL, {"regions": [r.id for r in regions], **extra}, format="json")

    def test_needs_a_sign_in(self):
        self.assertEqual(self.press(self.alpha).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_verified_email_starts_at_once_and_sends_what_is_in_force(self):
        self.user.email_verified = True
        self.user.save()
        Alert.objects.create(
            region=self.alpha,
            level=Level.WATCH,
            basis="observed",
            headline="Flood watch for Alpha County",
            description="d",
            instruction="i",
            onset=dt.date(2026, 10, 21),
            expires=timezone.now() + dt.timedelta(hours=36),
        )
        self.sign_in()
        response = self.press(self.alpha)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["confirmed"])
        self.assertEqual(response.data["alerts_sent_now"], 1)
        self.assertEqual(mail.outbox[-1].subject, "Flood watch for Alpha County")
        self.assertEqual(Subscriber.objects.get().user, self.user)

    def test_an_unverified_email_gets_one_code_then_never_again(self):
        self.sign_in()
        first = self.press(self.alpha)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertFalse(first.data["confirmed"])
        self.assertEqual(first.data["code_sent_by"], ["email"])
        subscriber = Subscriber.objects.get()
        done = self.client.post(
            "/api/alerts/confirm/",
            {"subscription": subscriber.token, "code": subscriber.confirm_code},
        )
        self.assertTrue(done.data["confirmed"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

        # The next press, for another place, needs no code and adds to the same subscription.
        sent_before = len(mail.outbox)
        second = self.press(self.beta, min_level="advisory")
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertTrue(second.data["confirmed"])
        self.assertEqual(len(mail.outbox), sent_before)
        subscriber = Subscriber.objects.get()
        self.assertEqual(subscriber.min_level, "advisory")
        self.assertCountEqual(subscriber.regions.all(), [self.alpha, self.beta])

    def test_changing_the_email_needs_confirming_again(self):
        self.user.email_verified = True
        self.user.save()
        self.sign_in()
        self.press(self.alpha)
        self.client.patch("/api/auth/me/", {"email": "new@example.com"}, format="json")
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)
        again = self.press(self.alpha)
        self.assertEqual(again.status_code, status.HTTP_201_CREATED)
        subscriber = Subscriber.objects.get()
        self.assertEqual((subscriber.email, subscriber.confirmed), ("new@example.com", False))

    def test_no_contact_on_the_profile(self):
        bare = User.objects.create_user("bare", password="x")
        self.sign_in(bare)
        response = self.press(self.alpha)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Add an email or a phone number", str(response.data))

    def test_a_phone_alone_waits_for_sms_to_be_set_up(self):
        phone_only = User.objects.create_user("phone", password="x", phone="+254712345678")
        self.sign_in(phone_only)
        response = self.press(self.alpha)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Text messages are not set up", str(response.data))

    @override_settings(AT_API_KEY="key", AT_USERNAME="sandbox")
    def test_with_sms_set_up_a_verified_phone_is_used_too(self):
        self.user.phone = "+254712345678"
        self.user.email_verified = self.user.phone_verified = True
        self.user.save()
        self.sign_in()
        response = self.press(self.alpha)
        self.assertEqual(response.data["phone"], "+254712345678")
        self.assertTrue(response.data["confirmed"])

    def test_unknown_places_are_refused(self):
        self.sign_in()
        response = self.client.post(URL, {"regions": [999999]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
