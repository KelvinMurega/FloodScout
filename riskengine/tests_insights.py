"""The "in use" analytics: forecast skill, KMD agreement, flips, rain, quota, labels."""

import datetime as dt
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from alerts.analytics import alert_activity
from alerts.models import Alert, AlertRun, Delivery, Subscriber, Unsubscription
from geodata.models import ApiUsage, EnvironmentalData, KmdWarning, Region
from geodata.services import open_meteo
from riskengine import insights
from riskengine.analytics import satellite_check
from riskengine.models import RiskForecast
from riskengine.tests_analytics import make_region, score

TODAY = dt.date(2026, 10, 19)


def forecast(region, issued, target, category, value=0.15):
    return RiskForecast.objects.create(
        region=region,
        issued_on=issued,
        target_date=target,
        predicted_value=value,
        risk_category=category,
        model_version="test",
    )


class ForecastSkillTests(TestCase):
    def setUp(self):
        self.county = make_region("Kisumu")

    def test_each_forecast_is_set_against_the_score_the_day_got(self):
        issued = TODAY - dt.timedelta(days=5)
        score(self.county, issued, "moderate")
        score(self.county, TODAY - dt.timedelta(days=4), "high")  # 1 day ahead
        score(self.county, TODAY - dt.timedelta(days=3), "moderate")  # 2 days ahead
        forecast(self.county, issued, TODAY - dt.timedelta(days=4), "high")
        forecast(self.county, issued, TODAY - dt.timedelta(days=3), "very_high")

        skill = insights.forecast_skill(TODAY)

        one, two = skill["leads"]
        self.assertEqual((one["lead_days"], one["pairs"], one["same_category"]), (1, 1, 1.0))
        self.assertEqual((one["hits"], one["misses"], one["false_alarms"]), (1, 0, 0))
        # Persistence said "moderate" all week: wrong on day 1, right on day 2.
        self.assertEqual(one["persistence_same"], 0.0)
        self.assertEqual(two["persistence_same"], 1.0)
        # Very high against moderate: two categories out, and a false alarm.
        self.assertEqual((two["same_category"], two["within_one"], two["false_alarms"]), (0.0, 0.0, 1))
        self.assertEqual(skill["issues_checked"], 1)

    def test_a_day_still_to_come_is_not_checked(self):
        forecast(self.county, TODAY, TODAY + dt.timedelta(days=2), "high")
        self.assertEqual(insights.forecast_skill(TODAY)["leads"], [])


def alert(region, issued, ended=None, level="watch", status=Alert.Status.EXPIRED, version=1):
    made = Alert.objects.create(
        region=region,
        level=level,
        status=status,
        basis=Alert.Basis.OBSERVED,
        headline="h",
        description="d",
        instruction="i",
        onset=issued.date(),
        expires=issued + dt.timedelta(days=3),
        ended_at=ended,
        version=version,
    )
    # issued_at is auto_now_add, so it is set after the row exists.
    Alert.objects.filter(pk=made.pk).update(issued_at=issued)
    made.refresh_from_db()
    return made


def moment(day: dt.date, hour: int = 9) -> dt.datetime:
    return timezone.make_aware(dt.datetime.combine(day, dt.time(hour)))


class KmdAgreementTests(TestCase):
    def test_county_days_are_split_into_both_ours_only_and_kmd_only(self):
        kisumu = make_region("Kisumu")
        siaya = make_region("Siaya", offset=2)
        sub = Region.objects.create(
            name="Nyando", county_name="Kisumu", level=Region.Level.SUB_COUNTY,
            geom=kisumu.geom, area_size=10.0,
        )
        # Ours: a sub-county alert counts for Kisumu, 15 to 17 Oct.
        alert(sub, moment(dt.date(2026, 10, 15)), ended=moment(dt.date(2026, 10, 17)))
        # KMD: Kisumu and Siaya, 16 to 18 Oct.
        warning = KmdWarning.objects.create(
            identifier="x", url="https://example.org/x", sent=moment(dt.date(2026, 10, 15)),
            event="Heavy Rain", onset=moment(dt.date(2026, 10, 16)),
            expires=moment(dt.date(2026, 10, 18)), flood_related=True,
        )
        warning.regions.add(kisumu, siaya)

        result = insights.kmd_agreement(TODAY)

        self.assertEqual(result["both"], 2)  # Kisumu 16 and 17
        self.assertEqual(result["ours_only"], 1)  # Kisumu 15
        self.assertEqual(result["kmd_only"], 4)  # Kisumu 18, Siaya 16 to 18
        self.assertEqual(result["agreement"], round(2 / 7, 4))
        self.assertEqual(result["warnings"][0]["counties_with_our_alert"], 1)
        self.assertEqual(result["kmd_first_stored"], "2026-10-15")

    def test_withdrawn_and_non_flood_warnings_are_left_out(self):
        county = make_region("Kisumu")
        for n, (flood, cancelled) in enumerate([(False, False), (True, True)]):
            w = KmdWarning.objects.create(
                identifier=str(n), url="https://example.org", sent=moment(TODAY),
                event="Strong Winds", onset=moment(TODAY - dt.timedelta(days=1)),
                flood_related=flood, cancelled=cancelled,
            )
            w.regions.add(county)
        self.assertEqual(insights.kmd_agreement(TODAY)["kmd_only"], 0)


class CategoryFlipTests(TestCase):
    def test_changes_from_the_day_before_are_counted_up_and_down(self):
        a, b = make_region("A"), make_region("B", offset=2)
        days = [TODAY - dt.timedelta(days=n) for n in (2, 1, 0)]
        for d, (ca, cb) in zip(days, [("low", "high"), ("moderate", "high"), ("low", "high")]):
            score(a, d, ca)
            score(b, d, cb)

        flips = insights.category_flips(TODAY)

        self.assertEqual([(r["up"], r["down"]) for r in flips["daily"]], [(1, 0), (0, 1)])
        self.assertEqual(flips["jumpiest"], [{"county": "A", "changes": 2}])
        self.assertEqual(flips["never_changed"], 1)

    def test_a_change_of_model_is_flagged(self):
        a = make_region("A")
        score(a, TODAY - dt.timedelta(days=1), "low", version="old")
        score(a, TODAY, "high", version="new")
        self.assertTrue(insights.category_flips(TODAY)["daily"][0]["model_changed"])


class RainTests(TestCase):
    def test_a_region_missing_days_is_listed_with_how_many(self):
        full, patchy = make_region("Full"), make_region("Patchy", offset=2)
        last = TODAY - dt.timedelta(days=1)
        for n in range(insights.RECENT_DAYS):
            EnvironmentalData.objects.create(region=full, record_date=last - dt.timedelta(days=n), rainfall_intensity=1)
        for n in range(20):
            EnvironmentalData.objects.create(region=patchy, record_date=last - dt.timedelta(days=n), rainfall_intensity=1)

        gaps = insights.rain_gaps()

        self.assertEqual((gaps["complete"], gaps["with_gaps"], gaps["missing_days"]), (1, 1, 10))
        self.assertEqual(gaps["worst"][0]["region"], "Patchy")
        self.assertEqual(gaps["daily"][0]["regions"], 1)
        self.assertEqual(gaps["daily"][-1]["regions"], 2)

    def test_rain_is_compared_with_the_county_normal_for_the_month(self):
        county = make_region("Kisumu")
        last = TODAY
        for n in range(30):
            EnvironmentalData.objects.create(region=county, record_date=last - dt.timedelta(days=n), rainfall_intensity=5)
        model = {"normals": {"Kisumu": {last.month: 100.0}}}
        with mock.patch("riskengine.insights.scorer._MODEL", model):
            result = insights.rain_vs_normal()
        self.assertEqual(result["counties"], [{"county": "Kisumu", "rain_30d": 150.0, "normal": 100.0, "ratio": 1.5}])
        self.assertEqual(result["above_normal"], 1)

    def test_there_is_no_comparison_without_a_trained_model(self):
        with mock.patch("riskengine.insights.scorer._MODEL", None):
            self.assertIsNone(insights.rain_vs_normal())


class ApiUsageTests(TestCase):
    def test_a_request_is_counted_as_open_meteo_counts_calls(self):
        params = {"latitude": "1,2,3", "longitude": "1,2,3", "past_days": "28", "forecast_days": "0"}
        open_meteo._record(params)
        open_meteo._record(None, rate_limited=True)
        row = ApiUsage.objects.get()
        # Three locations, 28 days each: two calls apiece.
        self.assertEqual((row.requests, row.calls, row.rate_limited), (1, 6.0, 1))

    def test_a_date_range_counts_its_days(self):
        params = {"latitude": "1", "start_date": "2026-01-01", "end_date": "2026-01-28"}
        self.assertEqual(open_meteo.calls_in(params), 2.0)

    def test_every_day_of_the_window_appears_even_without_requests(self):
        ApiUsage.objects.create(service=open_meteo.SERVICE, day=TODAY, requests=4, calls=9000)
        usage = insights.api_usage(TODAY)
        self.assertEqual(len(usage["daily"]), insights.RECENT_DAYS)
        self.assertEqual((usage["today"], usage["days_over_80_percent"]), (9000, 1))
        self.assertEqual(usage["counting_since"], TODAY.isoformat())

    def test_counting_never_breaks_a_download(self):
        with mock.patch("geodata.models.ApiUsage.objects.get_or_create", side_effect=RuntimeError):
            open_meteo._record({"latitude": "1"})  # no exception


class LabelCheckTests(TestCase):
    def test_labels_and_mapped_water_are_counted_per_event(self):
        rows = "event,began,county,under_circle,mapped_km2,mapped_wet\n" + "\n".join(
            [
                "1,2002-04-26,A,1,5.0,1",
                "1,2002-04-26,B,1,0.0,0",
                "1,2002-04-26,C,0,3.0,1",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "check.csv"
            path.write_text(rows, encoding="utf-8")
            result = insights.label_check(path)
        self.assertEqual(result["events"], [{"event": "1", "began": "2002-04-26", "labelled": 2, "wet": 2, "both": 1}])
        self.assertEqual((result["labels_with_water"], result["water_labelled"]), (0.5, 0.5))

    def test_a_missing_file_is_none(self):
        self.assertIsNone(insights.label_check(Path("does-not-exist.csv")))

    def test_the_sub_county_satellite_file_keeps_each_region_and_its_county(self):
        rows = (
            "event,date,sub_county,county,flooded_km2,flooded,probability,risk_category\n"
            "E,2023-11-28,Nyando,Kisumu,4.0,1,0.3,high\n"
            "E,2023-11-28,Ainabkoi,Uasin Gishu,0.0,0,0.1,moderate\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub.csv"
            path.write_text(rows, encoding="utf-8")
            (event,) = satellite_check(path, unit="sub_county")
        self.assertEqual(event["counties"][0]["name"], "Nyando")
        self.assertEqual(event["counties"][0]["county"], "Kisumu")
        self.assertEqual(event["roc_auc"], 1.0)


class AlertActivityTests(TestCase):
    def test_weeks_alerts_deliveries_and_subscribers_are_counted(self):
        county = make_region("Kisumu")
        now = timezone.now()
        AlertRun.objects.create(issued=3, upgraded=1, ended=2)
        quick = alert(county, now - dt.timedelta(days=3), ended=now - dt.timedelta(days=2))
        alert(county, now - dt.timedelta(days=20), ended=now - dt.timedelta(days=5), version=2)
        Delivery.objects.create(alert=quick, channel="email", level="watch", outcome="sent")
        Delivery.objects.create(alert=quick, channel="email", level="advisory", outcome="failed")
        Subscriber.objects.create(email="a@example.org", confirmed=True, confirmed_at=now)
        Unsubscription.objects.create(how=Unsubscription.How.LINK, subscribed_at=now - dt.timedelta(days=30))

        result = alert_activity()

        week = result["weekly"][-1]
        self.assertEqual((week["issued"], week["upgraded"], week["ended"]), (3, 1, 2))
        self.assertEqual(result["delivery"], {"sent": 1, "failed": 1, "rate": 0.5})
        self.assertEqual(result["alerts"]["short_lived"], 1)
        self.assertEqual(result["alerts"]["upgraded"], 1)
        self.assertEqual(result["alerts"]["ended"], 2)
        self.assertEqual(result["subscribers"]["stops_by_how"][0]["count"], 1)
        self.assertEqual(result["subscribers"]["now"], 1)
        # A month ago the one who later stopped was signed up; now only the new one is.
        self.assertEqual(result["weekly"][-1]["signed_up"], 1)
        self.assertEqual(result["weekly"][-3]["signed_up"], 1)

    def test_nothing_names_a_subscriber(self):
        Subscriber.objects.create(email="secret@example.org", confirmed=True, confirmed_at=timezone.now())
        self.assertNotIn("secret", str(alert_activity()))
