"""The live record: ReliefWeb flood reports, the counties they name, and the
model's daily scores counted against them."""

import datetime as dt
import io
import json
import urllib.error
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from geodata.models import Region
from geodata.services import reliefweb
from geodata.services.reliefweb import ReliefWebError, counties_named, fetch_reports
from riskengine.live import live_record
from riskengine.ml.infer import MODEL_VERSION
from riskengine.models import ReportedFlood, RiskScore
from riskengine.scoring import start_of_day
from riskengine.tests_analytics import make_region

TODAY = dt.date(2026, 11, 5)
COUNTIES = ["Elgeyo-Marakwet", "Embu", "Murang'a", "Nairobi", "Tana River"]


def _item(id_, title, body="", published="2026-10-12", sources=("KRCS",)):
    return {
        "id": str(id_),
        "fields": {
            "title": title,
            "body": body,
            "url_alias": f"https://reliefweb.int/report/kenya/{id_}",
            "date": {"original": f"{published}T00:00:00+00:00"},
            "source": [{"shortname": name} for name in sources],
        },
    }


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _answer(*pages):
    return [_Response(json.dumps({"data": page}).encode()) for page in pages]


class CountiesNamedTests(TestCase):
    def test_names_are_found_in_their_usual_spellings(self):
        text = "Floods hit Muranga and Elgeyo Marakwet; the TANA RIVER burst its banks."
        self.assertEqual(counties_named(text, COUNTIES), ["Elgeyo-Marakwet", "Murang'a", "Tana River"])

    def test_a_name_inside_another_word_is_not_a_match(self):
        self.assertEqual(counties_named("Embuildings and Nairobian roads", COUNTIES), [])


class FetchReportsTests(TestCase):
    def test_reports_are_read_and_pages_followed(self):
        first = [_item(n, f"Report {n}") for n in range(reliefweb.PAGE_SIZE)]
        second = [_item(999, "Last", published="2026-10-30", sources=("OCHA", "NDMA"))]
        with mock.patch("urllib.request.urlopen", side_effect=_answer(first, second)) as call:
            reports = fetch_reports("floodscout-test", dt.date(2026, 10, 1))
        self.assertEqual(len(reports), reliefweb.PAGE_SIZE + 1)
        self.assertEqual(call.call_count, 2)
        last = reports[-1]
        self.assertEqual((last.id, last.published, last.source), (999, dt.date(2026, 10, 30), "OCHA, NDMA"))
        request = call.call_args_list[1].args[0]
        self.assertIn("appname=floodscout-test", request.full_url)
        body = json.loads(request.data)
        self.assertEqual(body["offset"], reliefweb.PAGE_SIZE)
        self.assertIn(
            {"field": "primary_country.iso3", "value": "ken"}, body["filter"]["conditions"]
        )

    def test_a_refused_appname_is_an_error(self):
        refused = urllib.error.HTTPError(
            reliefweb.API_URL, 403, "Forbidden", {}, io.BytesIO(b'{"error": "not approved"}')
        )
        with mock.patch("urllib.request.urlopen", side_effect=refused):
            with self.assertRaisesRegex(ReliefWebError, "403"):
                fetch_reports("unapproved", dt.date(2026, 10, 1))


class FetchFloodReportsCommandTests(TestCase):
    def setUp(self):
        for offset, name in enumerate(COUNTIES):
            make_region(name, offset=offset * 2.0)

    @override_settings(RELIEFWEB_APPNAME="")
    def test_without_an_appname_nothing_is_fetched(self):
        out = StringIO()
        with mock.patch("urllib.request.urlopen") as call:
            call_command("fetch_flood_reports", stdout=out)
        call.assert_not_called()
        self.assertIn("RELIEFWEB_APPNAME is not set", out.getvalue())

    @override_settings(RELIEFWEB_APPNAME="floodscout-test")
    def test_each_county_named_is_stored_once(self):
        page = [
            _item(1, "Kenya: Floods - Flash Update 3", "Nairobi and Murang'a were hit."),
            _item(2, "Rains ease", "No county is named here."),
        ]
        for _ in range(2):  # a second run updates the rows
            with mock.patch("urllib.request.urlopen", side_effect=_answer(page)):
                call_command("fetch_flood_reports", "--since", "2026-10-01", stdout=StringIO())
        self.assertEqual(
            sorted(ReportedFlood.objects.values_list("reliefweb_id", "county__name")),
            [(1, "Murang'a"), (1, "Nairobi")],
        )

    @override_settings(RELIEFWEB_APPNAME="floodscout-test")
    def test_a_first_run_starts_from_the_first_score(self):
        RiskScore.objects.create(
            region=Region.objects.first(),
            prediction_date=start_of_day(dt.date(2026, 9, 20)),
            predicted_value=0.2,
            risk_category="high",
        )
        with mock.patch("urllib.request.urlopen", side_effect=_answer([])) as call:
            call_command("fetch_flood_reports", stdout=StringIO())
        body = json.loads(call.call_args.args[0].data)
        self.assertIn(
            {"field": "date.original", "value": {"from": "2026-09-20T00:00:00+00:00"}},
            body["filter"]["conditions"],
        )

    @override_settings(RELIEFWEB_APPNAME="floodscout-test")
    def test_a_refusal_fails_the_command(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaises(CommandError):
                call_command("fetch_flood_reports", stdout=StringIO())


@mock.patch("riskengine.live.timezone.localdate", return_value=TODAY)
@override_settings(RELIEFWEB_APPNAME="floodscout-test")
class LiveRecordTests(TestCase):
    ERRORS = {
        "threshold": 0.1,
        "by_month": [{"month": 4, "floods": 10, "caught": 8, "false_alarms": 24}],
    }

    def setUp(self):
        self.nairobi = make_region("Nairobi")
        self.embu = make_region("Embu", offset=2.0)

    def _score(self, region, day, value, version=MODEL_VERSION):
        RiskScore.objects.create(
            region=region,
            prediction_date=start_of_day(day),
            predicted_value=value,
            risk_category="high",
            model_version=version,
        )

    def _report(self, region, day, id_=1):
        ReportedFlood.objects.create(
            reliefweb_id=id_, county=region, published=day, title="Floods", url="https://reliefweb.int/x"
        )

    def test_finished_county_months_are_counted_as_in_the_evaluation(self, _today):
        # October: Nairobi warned (its last score is over 10%) and flooded,
        # Embu flooded unwarned. September: Embu warned, nothing reported.
        self._score(self.nairobi, dt.date(2026, 10, 3), 0.05)
        self._score(self.nairobi, dt.date(2026, 10, 31), 0.30)
        self._score(self.embu, dt.date(2026, 10, 4), 0.30)
        self._score(self.embu, dt.date(2026, 10, 31), 0.05)
        self._score(self.embu, dt.date(2026, 9, 28), 0.20)
        self._report(self.nairobi, dt.date(2026, 10, 10))
        self._report(self.embu, dt.date(2026, 10, 12), id_=2)

        record = live_record(self.ERRORS)

        self.assertEqual(
            record["totals"],
            {
                "county_months": 3,
                "floods": 2,
                "caught": 1,
                "missed": 1,
                "false_alarms": 1,
                "recall": 0.5,
                "precision": 0.5,
            },
        )
        self.assertEqual(record["first_month"], "2026-09-01")
        self.assertEqual(record["training"], {"recall": 0.8, "precision": 0.25})
        self.assertEqual([r["county"] for r in record["reports"]], ["Embu", "Nairobi"])

    def test_scores_from_another_model_are_not_counted(self, _today):
        self._score(self.nairobi, dt.date(2026, 10, 31), 0.5, version="heuristic-stub-v0")
        self.assertEqual(live_record(self.ERRORS)["totals"]["county_months"], 0)

    def test_the_month_under_way_is_left_out(self, _today):
        self._score(self.nairobi, TODAY, 0.9)
        self._report(self.nairobi, TODAY)
        self.assertEqual(live_record(self.ERRORS)["totals"]["county_months"], 0)

    def test_sub_county_scores_are_not_counted(self, _today):
        ward = make_region("Westlands", level=Region.Level.SUB_COUNTY, offset=4.0)
        self._score(ward, dt.date(2026, 10, 4), 0.9)
        self.assertEqual(live_record(self.ERRORS)["totals"]["county_months"], 0)

    @override_settings(RELIEFWEB_APPNAME="")
    def test_it_says_when_it_is_switched_off(self, _today):
        self.assertFalse(live_record(None)["switched_on"])
