"""The administrator's analytics endpoint: who may read it, and what it says."""

import datetime as dt
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.gis.geos import MultiPolygon, Polygon
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from geodata.models import EnvironmentalData, Region
from riskengine.analytics import _rank_auc, satellite_check, system_health
from riskengine.ml.infer import METRICS_PATH, MODEL_VERSION
from riskengine.models import RiskScore
from riskengine.scoring import start_of_day

TODAY = dt.date(2026, 10, 19)


def make_region(name: str, *, level=Region.Level.COUNTY, offset: float = 0.0) -> Region:
    square = Polygon.from_bbox((offset, offset, offset + 1.0, offset + 1.0))
    square.srid = 4326
    return Region.objects.create(
        name=name,
        county_name=name,
        level=level,
        geom=MultiPolygon(square, srid=4326),
        area_size=1000.0,
    )


def score(region, day, category="high", version=MODEL_VERSION):
    return RiskScore.objects.create(
        region=region,
        prediction_date=start_of_day(day),
        predicted_value=0.15,
        risk_category=category,
        model_version=version,
    )


class AnalyticsPermissionTests(APITestCase):
    def setUp(self):
        self.url = reverse("admin-analytics")

    def test_an_anonymous_request_is_refused_with_401(self):
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_an_end_user_is_refused_with_403(self):
        user = User.objects.create_user("reader", password="x" * 20)
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)

    def test_an_administrator_reads_it(self):
        admin = User.objects.create_user(
            "admin", password="x" * 20, role=User.Role.ADMINISTRATOR
        )
        self.client.force_authenticate(admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data), {"model", "satellite_check", "health"})


class AnalyticsModelTests(APITestCase):
    """The `model` section is metrics.json, reordered and nothing more."""

    def setUp(self):
        admin = User.objects.create_user(
            "admin", password="x" * 20, role=User.Role.ADMINISTRATOR
        )
        self.client.force_authenticate(admin)
        self.url = reverse("admin-analytics")
        self.metrics = json.loads(METRICS_PATH.read_text())

    def model(self):
        return self.client.get(self.url).data["model"]

    def test_names_the_model_and_what_it_was_trained_on(self):
        model = self.model()
        self.assertEqual(model["model_version"], MODEL_VERSION)
        self.assertEqual(model["selected"], self.metrics["selected"])
        self.assertEqual(model["training"]["rows"], self.metrics["rows"])
        self.assertEqual(model["training"]["floods"], self.metrics["positives"])

    def test_the_comparison_holds_every_model_and_both_baselines(self):
        rows = self.model()["comparison"]
        self.assertEqual(
            {row["key"] for row in rows},
            set(self.metrics["comparison"]) | set(self.metrics["baselines"]),
        )
        self.assertEqual(
            [row["key"] for row in rows if row["selected"]], [self.metrics["selected"]]
        )
        models = [row for row in rows if not row["baseline"]]
        self.assertEqual(rows[: len(models)], models)

    def test_each_warning_threshold_counts_every_flood_once(self):
        for row in self.model()["warnings"]:
            self.assertEqual(row["caught"] + row["missed"], self.metrics["positives"])

    def test_importance_is_ordered_most_important_first(self):
        means = [row["mean"] for row in self.model()["importance"]]
        self.assertEqual(means, sorted(means, reverse=True))
        self.assertEqual(len(means), len(self.metrics["permutation_importance"]))

    def test_every_fold_scores_every_model(self):
        cv = self.model()["cross_validation"]
        self.assertEqual(len(cv["folds"]), len(self.metrics["folds"]))
        keys = {model["key"] for model in cv["models"]}
        for fold in cv["folds"]:
            self.assertLessEqual(set(fold["scores"]), keys)

    def test_the_final_test_reports_the_model_the_earlier_years_chose(self):
        test = self.model()["final_test"]
        self.assertEqual(test["selected"], self.metrics["final_test"]["selected"])
        self.assertEqual(
            test["lift_over_season_only"], self.metrics["final_test"]["lift_over_season_only"]
        )

    def test_the_placeholder_rule_has_no_evaluation(self):
        with mock.patch("riskengine.ml.infer._MODEL", None):
            data = self.client.get(self.url).data
        self.assertIsNone(data["model"])
        self.assertIsNone(data["satellite_check"])
        self.assertIsNotNone(data["health"])


@mock.patch("riskengine.analytics.timezone.localdate", return_value=TODAY)
class SystemHealthTests(TestCase):
    def setUp(self):
        self.kisumu = make_region("Kisumu")
        self.lamu = make_region("Lamu", offset=2.0)

    def checks(self, health):
        return {check["name"]: check["status"] for check in health["checks"]}

    def test_with_nothing_stored_everything_is_degraded(self, _today):
        health = system_health()
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(self.checks(health)["Rainfall feed"], "degraded")
        self.assertEqual(self.checks(health)["Risk scores"], "degraded")
        self.assertEqual(self.checks(health)["Coverage"], "degraded")

    def test_fresh_complete_scores_from_the_loaded_model_are_ok(self, _today):
        for region in (self.kisumu, self.lamu):
            score(region, TODAY)
            EnvironmentalData.objects.create(
                region=region, record_date=TODAY, rainfall_intensity=3.0
            )
        health = system_health()
        self.assertEqual(health["status"], "ok", health["checks"])
        self.assertEqual(health["latest_scores"]["categories"]["county"]["high"], 2)
        self.assertEqual(health["rainfall"]["regions_on_last_day"], 2)

    def test_scores_two_days_old_are_a_warning_and_a_week_old_degraded(self, _today):
        for region in (self.kisumu, self.lamu):
            score(region, TODAY - dt.timedelta(days=2))
        self.assertEqual(self.checks(system_health())["Risk scores"], "warning")
        RiskScore.objects.all().delete()
        for region in (self.kisumu, self.lamu):
            score(region, TODAY - dt.timedelta(days=7))
        self.assertEqual(self.checks(system_health())["Risk scores"], "degraded")

    def test_a_missing_region_lowers_coverage(self, _today):
        score(self.kisumu, TODAY)
        self.assertEqual(self.checks(system_health())["Coverage"], "degraded")

    def test_scores_from_another_model_version_are_a_warning(self, _today):
        for region in (self.kisumu, self.lamu):
            score(region, TODAY, version="flood-old-model")
        self.assertEqual(self.checks(system_health())["Model"], "warning")

    def test_the_trend_counts_county_categories_per_day(self, _today):
        yesterday = TODAY - dt.timedelta(days=1)
        score(self.kisumu, yesterday, "high")
        score(self.lamu, yesterday, "very_high")
        score(self.kisumu, TODAY, "moderate")
        trend = system_health()["trend"]
        self.assertEqual([day["date"] for day in trend], [yesterday.isoformat(), TODAY.isoformat()])
        self.assertEqual((trend[0]["high"], trend[0]["very_high"]), (1, 1))
        self.assertEqual(trend[1]["moderate"], 1)
        self.assertEqual(trend[1]["model_versions"], [MODEL_VERSION])


class SatelliteCheckTests(TestCase):
    def test_rank_auc_counts_ties_as_half(self):
        self.assertEqual(_rank_auc([0.3, 0.2], [0.1, 0.2]), 0.875)
        self.assertIsNone(_rank_auc([], [0.1]))

    def test_each_event_compares_flooded_with_dry_counties(self):
        rows = (
            "event,date,county,flooded_km2,flooded,probability,risk_category\n"
            "Rains,2024-04-28,A,12.5,1,0.30,very_high\n"
            "Rains,2024-04-28,B,0.0,0,0.10,moderate\n"
            "Rains,2024-04-28,C,0.0,0,0.15,high\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "unosat.csv"
            path.write_text(rows, encoding="utf-8")
            [event] = satellite_check(path)
        self.assertEqual((event["flooded_counties"], event["dry_counties"]), (1, 2))
        self.assertEqual(event["roc_auc"], 1.0)
        self.assertEqual(event["flooded_at_high_or_above"], 1)
        self.assertEqual(event["dry_at_high_or_above"], 1)
        self.assertEqual(event["counties"][0]["county"], "A")

    def test_a_missing_file_gives_none(self):
        self.assertIsNone(satellite_check(Path("does-not-exist.csv")))
