"""The Kenya Meteorological Department's warnings: reading CAP, storing
them against regions, the API, the map's data and the alerts that name them."""

import datetime as dt
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from alerts import engine
from alerts.models import Alert
from alerts.tests import LOCMEM, score
from geodata.management.commands.fetch_kmd_warnings import covered
from geodata.models import KmdWarning, Region
from geodata.services import kmd
from riskengine.models import RiskScore
from riskengine.tests_analytics import make_region

RSS = (Path(__file__).parent / "fixtures" / "kmd" / "rss_2026-11-05.xml").read_bytes()
NOW = dt.datetime(2026, 10, 26, 12, 0, tzinfo=dt.timezone.utc)

# The square (0,0)-(1,1), written as KMD writes it: "lat,lon" pairs.
SQUARE = "0,0 0,1 1,1 1,0 0,0"


def cap(
    identifier="urn:oid:test.1",
    *,
    event="Heavy rainfall",
    severity="Moderate",
    area="Western Kenya",
    polygons=(SQUARE,),
    msg_type="Alert",
    status="Actual",
    references="",
    onset="2026-10-24T18:00:00+03:00",
    expires="2026-10-30T09:00:00+03:00",
) -> bytes:
    polys = "".join(f"<cap:polygon>{p}</cap:polygon>" for p in polygons)
    refs = f"<cap:references>{references}</cap:references>" if references else ""
    return f"""<?xml version='1.0' encoding='utf-8'?>
<cap:alert xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">
<cap:identifier>{identifier}</cap:identifier><cap:sender>director@meteo.go.ke</cap:sender>
<cap:sent>2026-10-24T18:47:00+03:00</cap:sent><cap:status>{status}</cap:status>
<cap:msgType>{msg_type}</cap:msgType><cap:scope>Public</cap:scope>{refs}
<cap:info><cap:language>en</cap:language><cap:category>Met</cap:category>
<cap:event>{event}</cap:event><cap:urgency>Expected</cap:urgency>
<cap:severity>{severity}</cap:severity><cap:certainty>Likely</cap:certainty>
<cap:onset>{onset}</cap:onset><cap:expires>{expires}</cap:expires>
<cap:headline>Rain exceeding 20 mm within 24 hours</cap:headline>
<cap:description>Heavy rain expected.</cap:description>
<cap:web>https://meteo.go.ke/weather-warnings/test/</cap:web>
<cap:area><cap:areaDesc>{area}</cap:areaDesc>{polys}</cap:area></cap:info></cap:alert>""".encode()


def feed(*urls) -> bytes:
    items = "".join(f"<item><title>t</title><link>{u}</link></item>" for u in urls)
    return f"<rss version='2.0'><channel><title>KMD</title>{items}</channel></rss>".encode()


def serve(pages: dict):
    """Patch the HTTP fetch: `pages` maps a URL to the bytes it returns."""
    return mock.patch("geodata.services.kmd._get", side_effect=lambda url: pages[url])


class ParseTests(TestCase):
    def test_the_real_feed_lists_its_alerts(self):
        links = kmd.alert_links(RSS)
        self.assertEqual(len(links), 5)
        self.assertTrue(all(link.startswith("https://meteo.go.ke/api/cap/") for link in links))

    def test_an_alert_is_read_with_lat_lon_turned_round(self):
        # A thin box at lon 36-37, lat 0-0.1: had the pairs been read as
        # (lon, lat) it would stand on its side.
        alert = kmd.parse_alert(cap(polygons=("0,36 0,37 0.1,37 0.1,36 0,36",)))
        self.assertEqual(alert.identifier, "urn:oid:test.1")
        self.assertEqual(alert.onset, dt.datetime(2026, 10, 24, 15, 0, tzinfo=dt.timezone.utc))
        self.assertTrue(alert.flood_related)
        xmin, ymin, xmax, ymax = alert.geom.extent
        self.assertEqual((xmin, xmax, ymin, ymax), (36.0, 37.0, 0.0, 0.1))

    def test_a_polygon_that_crosses_itself_is_made_valid(self):
        bow_tie = "0,0 1,1 0,1 1,0 0,0"
        alert = kmd.parse_alert(cap(polygons=(bow_tie,)))
        self.assertTrue(alert.geom.valid)
        self.assertGreater(alert.geom.area, 0)

    def test_wind_and_waves_are_not_flood_related(self):
        self.assertFalse(kmd.parse_alert(cap(event="Strong Winds")).flood_related)
        self.assertFalse(kmd.parse_alert(cap(event="Large waves")).flood_related)


class FetchCommandTests(TestCase):
    def setUp(self):
        self.alpha = make_region("Alpha")  # the square (0,0)-(1,1)
        self.beta = make_region("Beta", offset=1.0)  # touches Alpha's corner only
        self.gamma = make_region("Gamma", offset=3.0)
        self.alpha_sub = make_region("Alpha East", level=Region.Level.SUB_COUNTY)
        self.alpha_sub.county_name = "Alpha"
        self.alpha_sub.save()

    def fetch(self, pages):
        with serve(pages):
            call_command("fetch_kmd_warnings", stdout=StringIO(), stderr=StringIO())

    def test_a_warning_covers_the_regions_inside_its_outline_not_those_touching_it(self):
        self.fetch({kmd.RSS_URL: feed("https://kmd/1.xml"), "https://kmd/1.xml": cap()})
        warning = KmdWarning.objects.get()
        self.assertEqual(
            set(warning.regions.values_list("name", flat=True)), {"Alpha", "Alpha East"}
        )

    def test_counties_named_in_the_area_text_win_over_a_larger_outline(self):
        everywhere = "-5,30 -5,45 5,45 5,30 -5,30"
        self.fetch(
            {
                kmd.RSS_URL: feed("https://kmd/1.xml"),
                "https://kmd/1.xml": cap(area="Gamma, Beta,", polygons=(everywhere,)),
            }
        )
        names = set(KmdWarning.objects.get().regions.values_list("name", flat=True))
        self.assertEqual(names, {"Beta", "Gamma"})

    def test_an_update_replaces_the_warning_it_references(self):
        first = cap("urn:oid:old")
        update = cap(
            "urn:oid:new",
            msg_type="Update",
            references="director@meteo.go.ke,urn:oid:old,2026-10-24T18:47:00+03:00",
        )
        self.fetch({kmd.RSS_URL: feed("https://kmd/1.xml"), "https://kmd/1.xml": first})
        self.fetch({kmd.RSS_URL: feed("https://kmd/2.xml"), "https://kmd/2.xml": update})
        self.assertEqual(
            dict(KmdWarning.objects.values_list("identifier", "cancelled")),
            {"urn:oid:old": True, "urn:oid:new": False},
        )

    def test_exercises_are_not_stored_and_stored_alerts_are_not_fetched_again(self):
        pages = {
            kmd.RSS_URL: feed("https://kmd/1.xml", "https://kmd/2.xml"),
            "https://kmd/1.xml": cap(),
            "https://kmd/2.xml": cap("urn:oid:drill", status="Exercise"),
        }
        self.fetch(pages)
        self.assertEqual(KmdWarning.objects.count(), 1)
        with serve(pages) as get:
            call_command("fetch_kmd_warnings", stdout=StringIO())
        self.assertNotIn(mock.call("https://kmd/1.xml"), get.call_args_list)

    def test_one_unreadable_alert_does_not_stop_the_others(self):
        self.fetch(
            {
                kmd.RSS_URL: feed("https://kmd/bad.xml", "https://kmd/1.xml"),
                "https://kmd/bad.xml": b"<not xml",
                "https://kmd/1.xml": cap(),
            }
        )
        self.assertEqual(KmdWarning.objects.count(), 1)

    def test_with_no_outline_and_no_county_named_nothing_is_covered(self):
        alert = kmd.parse_alert(cap(polygons=()))
        self.assertFalse(covered(alert).exists())


class InForceTests(TestCase):
    def warning(self, identifier, **fields):
        defaults = {
            "url": f"https://kmd/{identifier}.xml",
            "sent": NOW - dt.timedelta(days=2),
            "event": "Heavy rainfall",
            "onset": NOW - dt.timedelta(days=1),
            "expires": NOW + dt.timedelta(days=1),
            "flood_related": True,
        }
        return KmdWarning.objects.create(identifier=identifier, **{**defaults, **fields})

    def test_what_is_in_force(self):
        self.warning("now")
        self.warning("ahead", onset=NOW + dt.timedelta(days=1), expires=NOW + dt.timedelta(days=3))
        self.warning("over", expires=NOW - dt.timedelta(hours=1))
        self.warning("withdrawn", cancelled=True)
        self.warning("open", expires=None, onset=NOW - dt.timedelta(days=2))
        self.warning("open-stale", expires=None, onset=NOW - dt.timedelta(days=30))
        self.assertEqual(
            set(KmdWarning.in_force(NOW).values_list("identifier", flat=True)),
            {"now", "ahead", "open"},
        )


@mock.patch("geodata.models.timezone.now", return_value=NOW)
class ApiTests(TestCase):
    def setUp(self):
        self.alpha = make_region("Alpha")
        self.gamma = make_region("Gamma", offset=3.0)
        with serve(
            {
                kmd.RSS_URL: feed("https://kmd/1.xml", "https://kmd/2.xml"),
                "https://kmd/1.xml": cap(),
                "https://kmd/2.xml": cap("urn:oid:wind", event="Strong Winds"),
            }
        ):
            call_command("fetch_kmd_warnings", stdout=StringIO())

    def test_the_map_gets_the_flood_related_warnings_as_geojson(self, _now):
        data = self.client.get(reverse("kmd-warning-list")).json()
        self.assertEqual(data["type"], "FeatureCollection")
        self.assertEqual([f["properties"]["event"] for f in data["features"]], ["Heavy rainfall"])
        self.assertIn(data["features"][0]["geometry"]["type"], ("Polygon", "MultiPolygon"))

    def test_a_region_gets_only_the_warnings_covering_it(self, _now):
        url = reverse("kmd-warning-list")
        self.assertEqual(len(self.client.get(url, {"region": self.alpha.pk}).json()["features"]), 1)
        self.assertEqual(len(self.client.get(url, {"region": self.gamma.pk}).json()["features"]), 0)
        self.assertEqual(self.client.get(url, {"region": "x"}).status_code, 400)


@override_settings(EMAIL_BACKEND=LOCMEM, AT_API_KEY="")
@mock.patch("alerts.rules.heavy_week_lines", lambda: {})
class AlertWordingTests(TestCase):
    def setUp(self):
        self.alpha = make_region("Alpha")
        with serve({kmd.RSS_URL: feed("https://kmd/1.xml"), "https://kmd/1.xml": cap()}):
            call_command("fetch_kmd_warnings", stdout=StringIO())

    def test_an_alert_names_the_kmd_warning_and_keeps_its_own_level(self):
        score(self.alpha, RiskScore.RiskCategory.VERY_HIGH)
        engine.run_alerts(now=NOW)
        alert = Alert.objects.get()
        self.assertEqual(alert.level, "watch")  # Very High alone, as without KMD
        kmd_reason = next(r for r in alert.reasons if r["kind"] == "kmd")
        self.assertEqual(
            kmd_reason["text"], "KMD heavy rainfall advisory in force until Friday 30 October"
        )
        self.assertIn("KMD heavy rainfall advisory", alert.description)

    def test_no_alert_no_kmd_mention(self):
        score(self.alpha, RiskScore.RiskCategory.LOW)
        engine.run_alerts(now=NOW)
        self.assertFalse(Alert.objects.exists())

    def test_a_warning_still_to_start_says_from_when(self):
        warning = KmdWarning.objects.get()
        warning.onset = NOW + dt.timedelta(days=1)
        self.assertEqual(
            engine.kmd_reason(warning, NOW)["text"],
            "KMD heavy rainfall advisory from Tuesday 27 October until Friday 30 October",
        )


class SchedulerTests(TestCase):
    COMMANDS = "riskengine.management.commands.refresh_scores"

    def test_a_refresh_reads_kmd_before_issuing_alerts(self):
        with mock.patch(f"{self.COMMANDS}.call_command") as run, mock.patch(
            f"{self.COMMANDS}.connections"
        ):
            call_command("refresh_scores", stdout=StringIO())
        names = [c.args[0] for c in run.call_args_list]
        self.assertLess(names.index("fetch_kmd_warnings"), names.index("issue_alerts"))

    def test_kmd_is_read_hourly_between_daily_refreshes(self):
        from riskengine.management.commands import refresh_scores

        class Stop(Exception):
            pass

        hours = iter([0, 0, 3600, 7200, 7200])  # the KMD clock at each check
        with mock.patch(f"{self.COMMANDS}.call_command") as run, mock.patch(
            f"{self.COMMANDS}.connections"
        ), mock.patch(f"{self.COMMANDS}._now", return_value=0), mock.patch(
            f"{self.COMMANDS}._kmd_clock", side_effect=lambda: next(hours)
        ), mock.patch(f"{self.COMMANDS}._sleep", side_effect=[None, Stop]):
            with self.assertRaises(Stop):
                call_command("refresh_scores", "--every-hours", "24", stdout=StringIO())
        names = [c.args[0] for c in run.call_args_list]
        # Once in the refresh at the start, once more an hour later.
        self.assertEqual(names.count("fetch_kmd_warnings"), 2)
        self.assertEqual(refresh_scores.KMD_SECONDS, 3600)

    def test_a_failed_kmd_read_does_not_fail_the_refresh(self):
        from django.core.management.base import CommandError

        def fail(name, *args, **kwargs):
            if name == "fetch_kmd_warnings":
                raise CommandError("KMD could not be read")

        stderr = StringIO()
        with mock.patch(f"{self.COMMANDS}.call_command", side_effect=fail) as run, mock.patch(
            f"{self.COMMANDS}.connections"
        ):
            call_command("refresh_scores", stdout=StringIO(), stderr=stderr)
        self.assertIn("KMD warnings skipped", stderr.getvalue())
        self.assertIn("issue_alerts", [c.args[0] for c in run.call_args_list])

