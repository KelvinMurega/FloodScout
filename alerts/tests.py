"""The warning system: the rules, the engine, the channels, CAP and the API."""

import datetime as dt
import json
import urllib.error
from unittest import mock
from xml.etree import ElementTree as ET

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from accounts.models import User
from riskengine.briefing import Place
from riskengine.models import RiskForecast, RiskScore
from riskengine.scoring import start_of_day
from riskengine.tests import make_region

from . import cap, channels, engine
from .models import Alert, AlertRun, Delivery, Level, Subscriber
from .rules import HEAVY_RAIN_MM, decide, wording

TODAY = dt.date(2026, 10, 20)
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


def week(*days):
    """Forecast days from (category, times, wettest mm[, rain to that day mm]), from tomorrow."""
    return [(TODAY + dt.timedelta(days=n + 1), *day) for n, day in enumerate(days)]


def place(category="high", change=0.0, days=(), **kw):
    return Place(
        1,
        "Nyando",
        category,
        kw.pop("times", 1.6),
        change_7d=change,
        week=list(days),
        county="Kisumu",
        level="sub_county",
        **kw,
    )


# Kisumu's heavy-rain line for a week in October, in place of alerts/heavy_rain_7d.json.
KISUMU_LINE = 100.0


@mock.patch("alerts.rules.heavy_week_lines", lambda: {"Kisumu": {"10": KISUMU_LINE}})
class RuleTests(SimpleTestCase):
    def level(self, p):
        return decide(p, TODAY).level

    def test_high_alone_never_alerts(self):
        self.assertIsNone(self.level(place("high", 0.0, week(("high", 1.6, 10)))))

    def test_very_high_today_is_a_watch(self):
        d = decide(place("very_high", 0.0), TODAY)
        self.assertEqual((d.level, d.basis), (Level.WATCH, "observed"))

    def test_very_high_and_rising_with_heavy_rain_for_the_county_is_a_warning(self):
        rain = week(("very_high", 2.4, 30, 60), ("very_high", 2.5, 40, 120))
        d = decide(place("very_high", 0.3, rain), TODAY)
        self.assertEqual((d.level, d.basis), (Level.WARNING, "observed"))
        self.assertIn("heavy for Kisumu in October", d.reasons[1]["text"])

    def test_very_high_and_rising_without_local_rain_is_a_watch(self):
        self.assertEqual(self.level(place("very_high", 0.3)), Level.WATCH)
        light = week(("very_high", 2.4, 20, 40), ("very_high", 2.5, 20, KISUMU_LINE))
        self.assertEqual(self.level(place("very_high", 0.3, light)), Level.WATCH)

    def test_forecast_very_high_soon_with_heavy_rain_for_the_county_is_a_warning(self):
        days = week(("high", 1.7, 20, 20), ("very_high", 2.2, HEAVY_RAIN_MM, 110))
        d = decide(place("high", 0.0, days), TODAY)
        self.assertEqual((d.level, d.basis), (Level.WARNING, "forecast"))
        self.assertEqual(d.onset, TODAY + dt.timedelta(days=2))
        self.assertEqual(
            [r["kind"] for r in d.reasons], ["forecast_very_high", "heavy_rain_local", "heavy_rain"]
        )

    def test_a_50_mm_day_that_is_ordinary_for_the_county_is_not_a_warning(self):
        days = week(("high", 1.7, 20, 20), ("very_high", 2.2, HEAVY_RAIN_MM, 70))
        self.assertEqual(self.level(place("high", 0.0, days)), Level.WATCH)

    def test_a_low_line_still_needs_30_mm_for_a_warning(self):
        showers = week(("high", 1.7, 10, 10), ("very_high", 2.2, 15, 25))
        rain = week(("high", 1.7, 10, 10), ("very_high", 2.2, 22, 32))
        with mock.patch("alerts.rules.heavy_week_lines", lambda: {"Kisumu": {"10": 18.0}}):
            self.assertEqual(self.level(place("high", 0.0, showers)), Level.WATCH)
            self.assertEqual(self.level(place("high", 0.0, rain)), Level.WARNING)

    def test_no_line_on_file_means_no_warning(self):
        days = week(("very_high", 2.2, 80, 500))
        p = place("high", 0.0, days)
        p.county = "Nowhere"
        self.assertEqual(self.level(p), Level.WATCH)

    def test_heavy_rain_too_late_is_no_warning_from_the_forecast(self):
        days = week(("very_high", 2.0, 5, 5), *[("high", 1.6, 5, 10)] * 3, ("high", 1.6, 60, 150))
        self.assertEqual(self.level(place("high", 0.0, days)), Level.WATCH)

    def test_forecast_very_high_later_in_the_week_is_a_watch(self):
        days = week(*[("high", 1.6, 5)] * 5, ("very_high", 2.1, 12))
        d = decide(place("high", 0.0, days), TODAY)
        self.assertEqual((d.level, d.onset), (Level.WATCH, TODAY + dt.timedelta(days=6)))

    def test_high_and_rising_is_an_advisory(self):
        self.assertEqual(self.level(place("high", 0.25)), Level.ADVISORY)

    def test_heavy_rain_alone_is_an_advisory_and_is_named(self):
        d = decide(place("moderate", 0.0, week(("moderate", 1.0, 5), ("moderate", 1.1, 64))), TODAY)
        self.assertEqual(d.level, Level.ADVISORY)
        self.assertIn("64 mm of rain forecast", d.reasons[0]["text"])

    def test_heavy_rain_is_mentioned_under_a_higher_level(self):
        d = decide(place("very_high", 0.0, week(("very_high", 2.0, 70, 120))), TODAY)
        self.assertEqual(d.level, Level.WARNING)  # very high soon with heavy rain for Kisumu soon
        d = decide(
            place("very_high", 0.0, week(*[("very_high", 2.0, 5)] * 4, ("very_high", 2.0, 70))),
            TODAY,
        )
        self.assertEqual(d.level, Level.WATCH)
        self.assertIn("heavy_rain", [r["kind"] for r in d.reasons])

    def test_wording_names_the_place_its_county_and_the_disclaimer(self):
        p = place("very_high", 0.0)
        words = wording(Level.WATCH, p, decide(p, TODAY))
        self.assertEqual(words["headline"], "Flood watch for Nyando (Kisumu County)")
        self.assertIn("about 1.6 times the usual", words["description"])
        self.assertIn("not the Kenya Meteorological Department", words["description"])
        self.assertIn("higher ground", words["instruction"])


class PhoneTests(SimpleTestCase):
    def test_the_ways_kenyans_write_numbers(self):
        for raw in ("0712 345 678", "712345678", "254712345678", "+254 712-345-678"):
            self.assertEqual(channels.normalise_phone(raw), "+254712345678")
        self.assertEqual(channels.normalise_phone("0110 123456"), "+254110123456")

    def test_rejects_what_is_not_a_kenyan_mobile(self):
        for raw in ("", "12345", "0812345678", "+1 555 123 4567"):
            with self.assertRaises(channels.PhoneError):
                channels.normalise_phone(raw)



class _Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@override_settings(AT_USERNAME="sandbox", AT_API_KEY="key", AT_SENDER_ID="")
class AfricasTalkingTests(SimpleTestCase):
    def test_posts_to_the_sandbox_with_the_key_header(self):
        body = {
            "SMSMessageData": {
                "Recipients": [{"statusCode": 101, "status": "Success", "messageId": "m1"}]
            }
        }
        with mock.patch("urllib.request.urlopen", return_value=_Response(body)) as call:
            result = channels.send_sms("+254712345678", "hello")
        request = call.call_args.args[0]
        self.assertEqual(
            request.full_url, "https://api.sandbox.africastalking.com/version1/messaging"
        )
        self.assertEqual(request.get_header("Apikey"), "key")
        self.assertIn(b"username=sandbox", request.data)
        self.assertIn(b"to=%2B254712345678", request.data)
        self.assertEqual(result.outcome, "sent")

    def test_a_rejected_number_is_a_failure(self):
        body = {
            "SMSMessageData": {"Recipients": [{"statusCode": 403, "status": "InvalidPhoneNumber"}]}
        }
        with mock.patch("urllib.request.urlopen", return_value=_Response(body)):
            self.assertEqual(channels.send_sms("+254712345678", "x").outcome, "failed")

    def test_no_connection_is_a_failure_not_a_crash(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            self.assertEqual(channels.send_sms("+254712345678", "x").outcome, "failed")

    @override_settings(AT_API_KEY="")
    def test_without_a_key_sms_is_skipped(self):
        self.assertEqual(channels.send_sms("+254712345678", "x").outcome, "skipped")

    @override_settings(AT_USERNAME="floodscout")
    def test_a_live_username_uses_the_live_endpoint(self):
        self.assertEqual(channels.sms_url(), "https://api.africastalking.com/version1/messaging")


def score(region, category, value=0.3, day=TODAY):
    return RiskScore.objects.create(
        region=region,
        prediction_date=start_of_day(day),
        predicted_value=value,
        risk_category=category,
    )


@override_settings(EMAIL_BACKEND=LOCMEM, AT_API_KEY="")
class EngineTests(TestCase):
    def setUp(self):
        self.region = make_region("Alpha")
        self.subscriber = Subscriber.objects.create(
            email="a@example.com", phone="+254712345678", confirmed=True
        )
        self.subscriber.regions.add(self.region)

    def test_issues_once_and_tells_subscribers_once(self):
        score(self.region, RiskScore.RiskCategory.VERY_HIGH)
        first = engine.run_alerts()
        second = engine.run_alerts()
        self.assertEqual((first.issued, second.issued), (1, 0))
        alert = Alert.objects.get()
        self.assertEqual((alert.level, alert.status), (Level.WATCH, Alert.Status.ACTIVE))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Flood watch for Alpha County")
        self.assertIn("/alerts/unsubscribe/", mail.outbox[0].body)
        # Alerts go by email only: the subscriber's phone gets nothing.
        self.assertEqual(
            dict(Delivery.objects.values_list("channel", "outcome")),
            {"email": "sent"},
        )

    @override_settings(
        DASHBOARD_URL="http://localhost:5173", PUBLIC_API_URL="http://127.0.0.1:8010/api"
    )
    def test_email_links_go_to_the_deployed_site_not_localhost(self):
        score(self.region, RiskScore.RiskCategory.VERY_HIGH)
        engine.run_alerts()
        body = mail.outbox[0].body
        self.assertIn(f"https://floodscout.me/#/alerts/{Alert.objects.get().id}", body)
        self.assertIn("https://api.floodscout.me/api/alerts/unsubscribe/", body)
        self.assertNotIn("localhost", body)
        self.assertNotIn("127.0.0.1", body)

    def test_the_same_level_rewrites_stale_words_without_telling_anyone(self):
        score(self.region, RiskScore.RiskCategory.VERY_HIGH)
        engine.run_alerts()
        Alert.objects.update(
            description="Forecast to reach Very High by Wednesday 28 October.", onset=TODAY
        )
        engine.run_alerts()
        alert = Alert.objects.get()
        self.assertTrue(alert.description.startswith("Very High today."), alert.description)
        self.assertEqual((alert.version, len(mail.outbox)), (1, 1))

    @mock.patch("alerts.rules.heavy_week_lines", lambda: {"Alpha": {"10": 100.0}})
    def test_a_stronger_condition_upgrades_and_tells_again(self):
        score(self.region, RiskScore.RiskCategory.VERY_HIGH)
        engine.run_alerts()
        RiskForecast.objects.create(
            region=self.region,
            issued_on=TODAY,
            target_date=TODAY + dt.timedelta(days=1),
            predicted_value=0.31,
            risk_category=RiskScore.RiskCategory.VERY_HIGH,
            rain_forecast_max_24h=80.0,
            rain_forecast_mm=120.0,
            model_version="t",
        )
        run = engine.run_alerts()
        alert = Alert.objects.get()
        self.assertEqual((run.upgraded, alert.level, alert.version), (1, Level.WARNING, 2))
        self.assertEqual(len(mail.outbox), 2)

    @mock.patch("alerts.rules.heavy_week_lines", lambda: {"Alpha": {"10": 100.0}})
    def test_a_weaker_condition_steps_down_quietly_and_back_up_tells_no_one_twice(self):
        score(self.region, RiskScore.RiskCategory.VERY_HIGH)
        heavy = dict(
            region=self.region,
            issued_on=TODAY,
            target_date=TODAY + dt.timedelta(days=1),
            predicted_value=0.31,
            risk_category=RiskScore.RiskCategory.VERY_HIGH,
            rain_forecast_max_24h=80.0,
            rain_forecast_mm=120.0,
            model_version="t",
        )
        RiskForecast.objects.create(**heavy)
        engine.run_alerts()
        self.assertEqual(Alert.objects.get().level, Level.WARNING)
        sent = len(mail.outbox)

        RiskForecast.objects.all().delete()
        engine.run_alerts()
        alert = Alert.objects.get()
        self.assertEqual((alert.level, alert.status), (Level.WATCH, Alert.Status.ACTIVE))
        self.assertIn("Flood watch", alert.headline)
        self.assertEqual(len(mail.outbox), sent)

        RiskForecast.objects.create(**heavy)
        run = engine.run_alerts()
        self.assertEqual((run.upgraded, Alert.objects.get().level), (1, Level.WARNING))
        self.assertEqual(len(mail.outbox), sent)

    def test_an_alert_ends_when_its_conditions_go(self):
        score(self.region, RiskScore.RiskCategory.VERY_HIGH)
        engine.run_alerts()
        RiskScore.objects.all().delete()
        score(self.region, RiskScore.RiskCategory.MODERATE, 0.08)
        run = engine.run_alerts()
        self.assertEqual(run.ended, 1)
        self.assertEqual(Alert.objects.get().status, Alert.Status.EXPIRED)

    def test_an_alert_left_alone_expires(self):
        score(self.region, RiskScore.RiskCategory.VERY_HIGH)
        engine.run_alerts()
        self.assertEqual(engine.expire_old(timezone.now() + dt.timedelta(days=2)), 1)

    def test_subscribers_get_only_the_levels_they_asked_for(self):
        self.subscriber.min_level = Level.WARNING
        self.subscriber.save()
        score(self.region, RiskScore.RiskCategory.VERY_HIGH)
        engine.run_alerts()
        self.assertEqual(len(mail.outbox), 0)

    def test_unconfirmed_and_other_places_get_nothing(self):
        self.subscriber.confirmed = False
        self.subscriber.save()
        other = Subscriber.objects.create(email="b@example.com", confirmed=True)
        other.regions.add(make_region("Beta", offset=2.0))
        score(self.region, RiskScore.RiskCategory.VERY_HIGH)
        engine.run_alerts()
        self.assertEqual(len(mail.outbox), 0)

    def test_manual_alerts_are_left_to_the_administrator_and_cancel_tells_those_told(self):
        admin = User.objects.create_user("boss", password="x", role=User.Role.ADMINISTRATOR)
        alert = engine.issue_manual(
            self.region, Level.WARNING, "River Alpha has burst its banks at the bridge.", admin
        )
        score(self.region, RiskScore.RiskCategory.LOW, 0.01)
        engine.run_alerts()
        alert.refresh_from_db()
        self.assertEqual(alert.status, Alert.Status.ACTIVE)
        engine.cancel(alert)
        self.assertEqual(alert.status, Alert.Status.CANCELLED)
        self.assertEqual(
            [m.subject.split(":")[0] for m in mail.outbox],
            ["Flood warning for Alpha County", "Cancelled"],
        )

    def test_the_command_reports_a_run(self):
        score(self.region, RiskScore.RiskCategory.VERY_HIGH)
        from io import StringIO

        out = StringIO()
        call_command("issue_alerts", stdout=out)
        self.assertIn("1 issued", out.getvalue())
        self.assertEqual(AlertRun.objects.count(), 1)


class CapTests(TestCase):
    def setUp(self):
        self.alert = Alert.objects.create(
            region=make_region("Alpha"),
            level=Level.WARNING,
            basis="forecast",
            headline="Flood warning for Alpha County",
            description="d",
            instruction="i",
            onset=TODAY,
            expires=timezone.now() + dt.timedelta(hours=36),
            times_usual=2.4,
        )

    def parse(self):
        return ET.fromstring(cap.cap_xml(self.alert, "http://x/#/alerts/1"))

    def test_a_cap_message_with_the_elements_in_schema_order(self):
        root = self.parse()
        ns = {"c": cap.NS}
        self.assertEqual(root.tag, f"{{{cap.NS}}}alert")
        top = [child.tag.split("}")[1] for child in root]
        self.assertEqual(
            top, ["identifier", "sender", "sent", "status", "msgType", "scope", "info"]
        )
        info = [child.tag.split("}")[1] for child in root.find("c:info", ns)]
        order = [
            "language",
            "category",
            "event",
            "responseType",
            "urgency",
            "severity",
            "certainty",
            "onset",
            "expires",
            "senderName",
            "headline",
            "description",
            "instruction",
            "web",
            "parameter",
            "parameter",
            "area",
        ]
        self.assertEqual(info, order)
        self.assertEqual(root.findtext("c:status", namespaces=ns), "Exercise")
        self.assertEqual(root.findtext("c:info/c:severity", namespaces=ns), "Severe")
        # To the second, with the offset: no fractions.
        self.assertRegex(
            root.findtext("c:sent", namespaces=ns), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+03:00$"
        )

    def test_the_area_polygon_is_closed_lat_lon_pairs(self):
        shape = self.parse().findtext(f".//{{{cap.NS}}}polygon").split()
        self.assertGreaterEqual(len(shape), 4)
        self.assertEqual(shape[0], shape[-1])
        lat, lon = map(float, shape[1].split(","))
        self.assertTrue(-90 <= lat <= 90 and -180 <= lon <= 180)

    def test_an_upgrade_is_an_update_and_a_cancel_a_cancel(self):
        self.alert.version = 2
        self.assertEqual(cap.msg_type(self.alert), "Update")
        self.assertIsNotNone(self.parse().find(f"{{{cap.NS}}}references"))
        self.alert.status = Alert.Status.CANCELLED
        self.assertEqual(cap.msg_type(self.alert), "Cancel")


@override_settings(EMAIL_BACKEND=LOCMEM, AT_API_KEY="")
class AlertAPITests(APITestCase):
    def setUp(self):
        cache.clear()  # the alerts throttle and the daily code limit count across tests
        self.region = make_region("Alpha")
        self.alert = Alert.objects.create(
            region=self.region,
            level=Level.WATCH,
            basis="observed",
            headline="Flood watch for Alpha County",
            description="d",
            instruction="i",
            onset=TODAY,
            expires=timezone.now() + dt.timedelta(hours=36),
        )
        self.admin = User.objects.create_user("boss", password="x", role=User.Role.ADMINISTRATOR)
        self.user = User.objects.create_user("someone", password="x")

    def as_user(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")

    def test_anyone_can_read_alerts_cap_and_the_feed(self):
        listed = self.client.get("/api/alerts/")
        self.assertEqual([a["id"] for a in listed.data], [self.alert.id])
        self.assertEqual(len(listed.data[0]["point"]), 2)
        cap_response = self.client.get(f"/api/alerts/{self.alert.id}/cap/")
        self.assertEqual(cap_response["Content-Type"], "application/cap+xml")
        feed = self.client.get("/api/alerts/feed/")
        self.assertEqual(feed["Content-Type"], "application/atom+xml")
        self.assertIn(f"/api/alerts/{self.alert.id}/cap/".encode(), feed.content)

    def test_ended_alerts_are_listed_only_when_asked(self):
        self.alert.status = Alert.Status.EXPIRED
        self.alert.save()
        self.assertEqual(self.client.get("/api/alerts/").data, [])
        self.assertEqual(len(self.client.get("/api/alerts/", {"status": "all"}).data), 1)

    def test_subscribe_confirm_and_unsubscribe(self):
        response = self.client.post(
            "/api/alerts/subscribe/",
            {"email": "me@example.com", "regions": [self.region.id], "min_level": "watch"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["code_sent_by"], ["email"])
        subscriber = Subscriber.objects.get()
        self.assertFalse(subscriber.confirmed)
        self.assertIn(subscriber.confirm_code, mail.outbox[0].subject)

        wrong = self.client.post(
            "/api/alerts/confirm/", {"subscription": subscriber.token, "code": "x"}
        )
        self.assertEqual(wrong.status_code, status.HTTP_400_BAD_REQUEST)
        right = self.client.post(
            "/api/alerts/confirm/",
            {"subscription": subscriber.token, "code": subscriber.confirm_code},
        )
        self.assertTrue(right.data["confirmed"])
        # The watch already active for Alpha is sent at once.
        self.assertEqual(mail.outbox[-1].subject, "Flood watch for Alpha County")

        url = f"/api/alerts/unsubscribe/{subscriber.token}/"
        # Opening the link (as a mail scanner does) only asks.
        page = self.client.get(url)
        self.assertContains(page, "<form method='post'>")
        self.assertTrue(Subscriber.objects.exists())
        page = self.client.post(url)
        self.assertContains(page, "no more FloodScout alerts")
        self.assertFalse(Subscriber.objects.exists())
        self.assertContains(self.client.get(url), "already been used")

    def test_one_address_gets_at_most_five_codes_a_day(self):
        codes = [
            self.client.post(
                "/api/alerts/subscribe/",
                {"email": "victim@example.com", "regions": [self.region.id]},
                format="json",
            ).status_code
            for _ in range(6)
        ]
        self.assertEqual(codes, [status.HTTP_201_CREATED] * 5 + [status.HTTP_429_TOO_MANY_REQUESTS])
        self.assertEqual(len(mail.outbox), 5)
        self.assertEqual(Subscriber.objects.count(), 5)  # the refused one is not kept

    def test_too_many_wrong_codes_end_the_sign_up(self):
        subscriber = Subscriber.objects.create(email="me@example.com", confirm_attempts=5)
        response = self.client.post(
            "/api/alerts/confirm/", {"subscription": subscriber.token, "code": "1"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Subscriber.objects.exists())

    def test_a_phone_is_not_a_way_to_sign_up(self):
        response = self.client.post(
            "/api/alerts/subscribe/",
            {"phone": "0712345678", "regions": [self.region.id]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_bad_sign_ups(self):
        for body in (
            {"regions": [self.region.id]},
            {"email": "me@example.com", "regions": []},
            {"email": "me@example.com", "regions": [999999]},
        ):
            response = self.client.post("/api/alerts/subscribe/", body, format="json")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, body)

    def test_only_an_administrator_may_issue_cancel_run_or_see_stats(self):
        body = {
            "region": self.region.id,
            "level": "warning",
            "description": "The river is over the road.",
        }
        self.assertEqual(
            self.client.post("/api/alerts/issue/", body).status_code, status.HTTP_401_UNAUTHORIZED
        )
        self.as_user(self.user)
        self.assertEqual(
            self.client.post("/api/alerts/issue/", body).status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.get("/api/alerts/stats/").status_code, status.HTTP_403_FORBIDDEN
        )

    def test_administrator_controls(self):
        self.as_user(self.admin)
        issued = self.client.post(
            "/api/alerts/issue/",
            {
                "region": self.region.id,
                "level": "warning",
                "description": "The river is over the road.",
            },
        )
        self.assertEqual(issued.status_code, status.HTTP_201_CREATED)
        self.assertEqual(issued.data["issued_by"], "boss")
        # The rules' watch for the same place was replaced.
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, Alert.Status.CANCELLED)

        cancelled = self.client.post(f"/api/alerts/{issued.data['id']}/cancel/")
        self.assertEqual(cancelled.data["status"], "cancelled")
        again = self.client.post(f"/api/alerts/{issued.data['id']}/cancel/")
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)

        stats = self.client.get("/api/alerts/stats/")
        self.assertNotIn("sms_configured", stats.data)
        self.assertIn("subscribers", stats.data)

        test = self.client.post("/api/alerts/test/", {"email": "boss@example.com"})
        self.assertEqual(test.data["email"]["outcome"], "sent")
        self.assertEqual(self.client.post("/api/alerts/run/").status_code, status.HTTP_200_OK)
