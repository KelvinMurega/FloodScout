"""The rolling outlook months, and a past month's outlook against the floods recorded in it."""

from __future__ import annotations

import datetime as dt
from io import StringIO
from unittest import mock

from django.contrib.gis.geos import Polygon
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from geodata.models import KmdWarning
from geodata.services import flood_news, gdacs
from riskengine.models import ObservedFlood, ReportedFlood, RiskOutlook, RiskScore
from riskengine.outcome import month_outcome

from .tests import make_region

COUNTIES = ["Kisumu", "Nairobi", "Tana River", "Lamu", "Busia"]


def outlook(region, month, category, issued=dt.date(2026, 10, 7)):
    return RiskOutlook.objects.create(
        region=region,
        month=month,
        flood_probability=0.2,
        probability_low=0.1,
        probability_high=0.3,
        risk_category=category,
        model_version="test",
        issued_on=issued,
    )


class HeadlineTests(SimpleTestCase):
    def test_damage_that_happened_counts(self):
        for title in (
            "Over 300 households displaced as floods hit Tana River County",
            "Coastal floods displace hundreds as Tana River and Lamu brace for more water",
            "Floods hit parts of Nairobi as Kenyans question billions spent",
            "Two swept away by floods in Kisumu",
        ):
            with self.subTest(title=title):
                self.assertTrue(flood_news.reports_damage(title))

    def test_getting_ready_does_not(self):
        for title in (
            "Nairobi braces for heavy rains and flash floods",
            "Kenya Met Issues Heavy Rainfall Alert, Lists Counties to be Affected",
            "Red Cross, Machakos fire teams conduct joint floods drill ahead of El Niño",
            "TARDA warns of flood risks in Kitui",
            "Kenya Re taps 90 university students to develop AI flood risk tool",
        ):
            with self.subTest(title=title):
                self.assertFalse(flood_news.reports_damage(title))

    def test_counties_are_named_directly_or_through_a_town(self):
        self.assertEqual(
            flood_news.counties_in("Floods hit Tana River and Lamu", COUNTIES),
            ["Lamu", "Tana River"],
        )
        self.assertEqual(flood_news.counties_in("Ahero residents marooned", COUNTIES), ["Kisumu"])
        self.assertEqual(flood_news.counties_in("Budalangi families flee", COUNTIES), ["Busia"])
        self.assertEqual(flood_news.counties_in("Floods in Senegal", COUNTIES), [])

    def test_the_feed_is_parsed_and_the_publisher_taken_off_the_title(self):
        feed = b"""<?xml version="1.0"?><rss><channel>
          <item><title>Floods hit Tana River - People Daily</title>
            <link>https://news.google.com/a</link>
            <pubDate>Fri, 30 Oct 2026 07:00:00 GMT</pubDate>
            <source url="https://pd.co.ke">People Daily</source></item>
          <item><title>No date</title><link>https://news.google.com/b</link></item>
        </channel></rss>"""
        self.assertEqual(
            flood_news.parse(feed),
            [
                flood_news.Story(
                    title="Floods hit Tana River",
                    publisher="People Daily",
                    url="https://news.google.com/a",
                    published=dt.date(2026, 10, 30),
                )
            ],
        )

    def test_a_feed_that_is_not_rss_is_an_error(self):
        with self.assertRaises(flood_news.FloodNewsError):
            flood_news.parse(b"<html>blocked")


class FetchObservedFloodsTests(TestCase):
    COMMAND = "riskengine.management.commands.fetch_observed_floods"

    def setUp(self):
        self.tana = make_region("Tana River", offset=0.0)
        self.lamu = make_region("Lamu", offset=5.0)
        patcher = mock.patch(
            f"{self.COMMAND}.timezone.localdate", return_value=dt.date(2026, 11, 15)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, stories=(), events=(), **options):
        with (
            mock.patch.object(flood_news, "fetch_month", return_value=list(stories)) as news,
            mock.patch.object(gdacs, "fetch_events", return_value=list(events)),
        ):
            call_command("fetch_observed_floods", stdout=StringIO(), **options)
        return news

    def test_by_default_last_month_and_this_one(self):
        news = self._run()
        self.assertEqual(
            [call.args for call in news.call_args_list],
            [
                (dt.date(2026, 10, 1), dt.date(2026, 10, 31)),
                # This month only up to today.
                (dt.date(2026, 11, 1), dt.date(2026, 11, 15)),
            ],
        )

    def test_stores_damage_headlines_once_per_county_named(self):
        stories = [
            flood_news.Story(
                "Floods displace hundreds in Tana River and Lamu",
                "Nation",
                "https://n/1",
                dt.date(2026, 10, 30),
            ),
            flood_news.Story(
                "Lamu braces for El Niño", "Star", "https://n/2", dt.date(2026, 10, 5)
            ),
        ]
        self._run(stories, month=[dt.date(2026, 10, 1)])
        self._run(stories, month=[dt.date(2026, 10, 1)])  # twice: no duplicates
        self.assertEqual(
            sorted(ObservedFlood.objects.values_list("county__name", "url")),
            [("Lamu", "https://n/1"), ("Tana River", "https://n/1")],
        )

    def test_a_gdacs_outline_names_the_counties_it_reaches(self):
        area = Polygon.from_bbox((0.2, 0.2, 0.4, 0.4))
        area.srid = 4326
        event = gdacs.Event(
            1,
            1,
            "Flood in Kenya",
            "Orange",
            dt.date(2026, 10, 20),
            dt.date(2026, 11, 3),
            "https://g/1",
            [area],
        )
        point_only = gdacs.Event(
            2,
            1,
            "Flood in Kenya",
            "Orange",
            dt.date(2026, 10, 1),
            dt.date(2026, 10, 9),
            "https://g/2",
            [],
        )
        self._run(events=[event, point_only], month=[dt.date(2026, 10, 1)])
        self.assertEqual(
            sorted(ObservedFlood.objects.values_list("url", "county__name"), key=str),
            [("https://g/1", "Tana River"), ("https://g/2", None)],
        )

    def test_one_failed_source_does_not_lose_the_other(self):
        stories = [
            flood_news.Story(
                "Floods displace 300 in Lamu", "Nation", "https://n/1", dt.date(2026, 10, 30)
            )
        ]
        with (
            mock.patch.object(flood_news, "fetch_month", return_value=stories),
            mock.patch.object(
                gdacs, "fetch_events", side_effect=gdacs.GdacsError("GDACS answered 500")
            ),
        ):
            with self.assertRaisesMessage(CommandError, "GDACS October 2026: GDACS answered 500"):
                call_command("fetch_observed_floods", "--month", "2026-10", stdout=StringIO())
        self.assertTrue(ObservedFlood.objects.filter(county=self.lamu).exists())


class MonthOutcomeTests(TestCase):
    OCTOBER = dt.date(2026, 10, 1)

    def setUp(self):
        self.tana = make_region("Tana River", offset=0.0)
        self.lamu = make_region("Lamu", offset=2.0)
        self.kisumu = make_region("Kisumu", offset=4.0)
        outlook(self.tana, self.OCTOBER, RiskScore.RiskCategory.HIGH)
        outlook(self.lamu, self.OCTOBER, RiskScore.RiskCategory.LOW)
        outlook(self.kisumu, self.OCTOBER, RiskScore.RiskCategory.VERY_HIGH)
        ObservedFlood.objects.create(
            source="news",
            county=self.tana,
            date=dt.date(2026, 10, 30),
            title="Floods displace 302",
            url="https://n/1",
        )
        ObservedFlood.objects.create(
            source="news",
            county=self.lamu,
            date=dt.date(2026, 10, 31),
            title="Lamu submerged",
            url="https://n/2",
        )
        # November's flood is not October's.
        ObservedFlood.objects.create(
            source="news",
            county=self.kisumu,
            date=dt.date(2026, 11, 2),
            title="Kisumu",
            url="https://n/3",
        )
        ObservedFlood.objects.create(
            source="gdacs",
            date=dt.date(2026, 9, 25),
            ended=dt.date(2026, 10, 4),
            title="Orange flood",
            url="https://g/1",
        )
        ReportedFlood.objects.create(
            reliefweb_id=9,
            county=self.tana,
            published=dt.date(2026, 10, 29),
            title="Flash update",
            url="https://rw/9",
        )
        warning = KmdWarning.objects.create(
            identifier="kmd-1",
            url="https://kmd/1",
            sent=timezone.make_aware(dt.datetime(2026, 10, 20)),
            event="Heavy rainfall",
            onset=timezone.make_aware(dt.datetime(2026, 10, 21)),
            expires=timezone.make_aware(dt.datetime(2026, 10, 25)),
            flood_related=True,
        )
        warning.regions.set([self.kisumu])

    def test_each_county_has_its_outlook_and_what_was_reported(self):
        result = month_outcome(self.OCTOBER, dt.date(2026, 11, 15))
        rows = {row["region_name"]: row for row in result["counties"]}

        self.assertTrue(result["complete"])
        self.assertEqual(result["issued_on"], "2026-10-07")
        self.assertEqual(
            [r["source"] for r in rows["Tana River"]["records"]], ["reliefweb", "news"]
        )
        self.assertTrue(rows["Lamu"]["flooded"])
        self.assertFalse(rows["Kisumu"]["flooded"])
        self.assertEqual(rows["Kisumu"]["kmd_warnings"], 1)
        # A GDACS event that ran into October, placed nowhere in particular.
        self.assertEqual([r["url"] for r in result["national"]], ["https://g/1"])
        summary = result["summary"]
        self.assertEqual(summary["counties"], 3)
        self.assertEqual(summary["flooded"], 2)
        self.assertEqual(summary["by_category"]["high"], {"rated": 1, "flooded": 1})
        self.assertEqual(summary["by_category"]["low"], {"rated": 1, "flooded": 1})
        self.assertEqual(summary["by_category"]["very_high"], {"rated": 1, "flooded": 0})
        # All three were given 0.2, so ranks follow the order they were stored.
        self.assertEqual(len(summary["flooded_ranks"]), 2)
        # Flooded counties first.
        self.assertEqual([r["flooded"] for r in result["counties"]], [True, True, False])

    def test_the_month_in_progress_is_not_complete(self):
        self.assertFalse(month_outcome(self.OCTOBER, dt.date(2026, 10, 20))["complete"])


class OutlookMonthsApiTests(APITestCase):
    def setUp(self):
        region = make_region("Nairobi")
        for month in (8, 9, 10, 11, 12):
            outlook(region, dt.date(2026, month, 1), RiskScore.RiskCategory.LOW)
        outlook(region, dt.date(2027, 1, 1), RiskScore.RiskCategory.LOW)
        outlook(region, dt.date(2027, 2, 1), RiskScore.RiskCategory.LOW)
        patcher = mock.patch(
            "riskengine.views.timezone.localdate", return_value=dt.date(2026, 11, 15)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_this_month_and_the_two_after_are_upcoming_and_earlier_ones_past(self):
        response = self.client.get(reverse("risk-outlook-months"))
        self.assertEqual(
            [(row["month"].isoformat(), row["kind"]) for row in response.data],
            [
                ("2026-08-01", "past"),
                ("2026-09-01", "past"),
                ("2026-10-01", "past"),
                ("2026-11-01", "upcoming"),
                ("2026-12-01", "upcoming"),
                ("2027-01-01", "upcoming"),
            ],
        )

    def test_outcome_needs_a_month(self):
        self.assertEqual(self.client.get(reverse("risk-outlook-outcome")).status_code, 400)
        self.assertEqual(
            self.client.get(reverse("risk-outlook-outcome"), {"month": "October"}).status_code, 400
        )

    def test_outcome_for_a_past_month(self):
        response = self.client.get(reverse("risk-outlook-outcome"), {"month": "2026-10-01"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["month"], "2026-10-01")
        self.assertEqual(len(response.data["counties"]), 1)
