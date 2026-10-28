"""Stopping alerts keeps a small record and the delivery totals (alerts/stopping.py),
and the administrator can see subscribers, what reached them, and who stopped."""

import datetime as dt

from django.core.cache import cache
from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from accounts.models import User
from riskengine.tests import make_region

from . import stopping
from .models import Alert, Delivery, Level, Subscriber, Unsubscription


class MaskTests(SimpleTestCase):
    def test_most_of_an_email_or_phone_is_hidden(self):
        self.assertEqual(stopping.mask_email("kevomurega@gmail.com"), "k***@gmail.com")
        self.assertEqual(stopping.mask_phone("+254712345678"), "+2547****678")
        self.assertEqual(stopping.mask_email("not-an-email"), "***")


class StoppingTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.region = make_region("Alpha")
        self.alert = Alert.objects.create(
            region=self.region,
            level=Level.WATCH,
            basis="observed",
            headline="Flood watch for Alpha County",
            description="d",
            instruction="i",
            onset=dt.date(2026, 10, 20),
            expires=timezone.now() + dt.timedelta(hours=36),
        )
        self.user = User.objects.create_user("someone", password="x", email="someone@example.com")
        self.admin = User.objects.create_user("boss", password="x", role=User.Role.ADMINISTRATOR)
        self.sub = Subscriber.objects.create(
            email="someone@example.com",
            phone="+254712345678",
            user=self.user,
            confirmed=True,
            confirmed_at=timezone.now(),
        )
        self.sub.regions.add(self.region)
        for channel, outcome in (("email", "sent"), ("sms", "failed")):
            Delivery.objects.create(
                alert=self.alert,
                subscriber=self.sub,
                channel=channel,
                level=Level.WATCH,
                outcome=outcome,
                detail="to someone@example.com",
            )

    def as_user(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")

    def test_the_unsubscribe_link_keeps_a_masked_record_and_the_totals(self):
        self.client.post(f"/api/alerts/unsubscribe/{self.sub.token}/")
        self.assertFalse(Subscriber.objects.exists())
        record = Unsubscription.objects.get()
        self.assertEqual(
            (record.how, record.username, record.contact, record.places, record.alerts_received),
            ("link", "someone", "s***@example.com, +2547****678", ["Alpha"], 1),
        )
        # Both deliveries are still counted, with nothing pointing at the person.
        self.assertEqual(Delivery.objects.count(), 2)
        self.assertFalse(Delivery.objects.exclude(subscriber=None).exists())
        self.assertFalse(Delivery.objects.exclude(detail="").exists())

    def test_removing_a_subscription_on_the_profile_is_recorded(self):
        self.as_user(self.user)
        response = self.client.delete(f"/api/auth/me/subscriptions/{self.sub.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(Unsubscription.objects.get().how, "profile")

    def test_only_an_administrator_sees_the_subscribers(self):
        self.assertEqual(
            self.client.get("/api/alerts/subscribers/").status_code, status.HTTP_401_UNAUTHORIZED
        )
        self.as_user(self.user)
        self.assertEqual(self.client.get("/api/alerts/subscribers/").status_code, status.HTTP_403_FORBIDDEN)

    def test_the_administrator_sees_who_got_what_and_who_stopped(self):
        stopper = Subscriber.objects.create(email="gone@example.com", confirmed=True)
        stopper.regions.add(self.region)
        stopping.stop(stopper, Unsubscription.How.LINK)
        self.as_user(self.admin)
        data = self.client.get("/api/alerts/subscribers/").data
        self.assertEqual(
            {k: data["totals"][k] for k in ("subscribers", "with_account", "alerts_sent", "alerts_failed", "unsubscribed")},
            {"subscribers": 1, "with_account": 1, "alerts_sent": 1, "alerts_failed": 1, "unsubscribed": 1},
        )
        [person] = data["subscribers"]
        self.assertEqual(
            (person["email"], person["username"], person["alerts_sent"], person["alerts_failed"]),
            ("someone@example.com", "someone", 1, 1),
        )
        [gone] = data["unsubscribed"]
        self.assertEqual((gone["contact"], gone["how"], gone["username"]), ("g***@example.com", "Unsubscribe link", None))

    def test_the_users_list_counts_alerts_received_and_unsubscribes(self):
        stopping.stop(
            Subscriber.objects.create(user=self.user, email="someone@example.com", confirmed=True),
            Unsubscription.How.PROFILE,
        )
        self.as_user(self.admin)
        row = next(u for u in self.client.get("/api/admin/users/").data if u["username"] == "someone")
        self.assertEqual((row["subscriptions"], row["alerts_received"], row["unsubscribed"]), (1, 1, 1))
