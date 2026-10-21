"""The 7-day forecast (riskengine/forecast.py) and the explanation of a score
(riskengine/ml/explain.py, riskengine/why.py)."""

import datetime as dt
from io import StringIO
from unittest import mock

import numpy as np
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from rest_framework import status
from rest_framework.test import APITestCase
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline

from geodata.models import EnvironmentalData
from geodata.services.open_meteo import Point

from .forecast import FORECAST_DAYS, forecast_regions, forecast_totals, join_rain
from .ml.explain import FACTORS, explain_row, grouped
from .ml.features import FEATURE_NAMES
from .models import RiskForecast, RiskScore
from .scoring import start_of_day
from .tests import make_region
from .why import summary

ISSUED = dt.date(2026, 10, 20)


def small_model(seed: int = 0) -> dict:
    """A real fitted imputer-and-forest pipeline on made-up rows.

    The flood label leans on the first input (rain_30d), so the forest has
    something to split on.
    """
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(400, len(FEATURE_NAMES)))
    x[rng.random(x.shape) < 0.05] = np.nan
    y = (np.nan_to_num(x[:, 0]) + rng.normal(scale=0.5, size=400) > 1.0).astype(int)
    pipeline = make_pipeline(
        SimpleImputer(strategy="median"),
        RandomForestClassifier(n_estimators=25, min_samples_leaf=5, random_state=seed),
    )
    pipeline.fit(x, y)
    return {
        "version": "test-forest",
        "features": list(FEATURE_NAMES),
        "pipeline": pipeline,
        "probability_bands": [0.05, 0.1, 0.2],
        "normals": {"Alpha": {month: 90.0 for month in range(1, 13)}},
        "oni": {},
        "dmi": {},
    }


class ExplainRowTests(SimpleTestCase):
    def setUp(self):
        self.model = small_model()
        rng = np.random.default_rng(1)
        self.rows = rng.normal(size=(20, len(FEATURE_NAMES))).tolist()
        self.rows[3][2] = float("nan")  # the imputer is part of the path

    def test_contributions_add_up_to_the_prediction(self):
        predicted = self.model["pipeline"].predict_proba(np.array(self.rows))[:, 1]
        for row, expected in zip(self.rows, predicted):
            explanation = explain_row(self.model, row)
            self.assertAlmostEqual(explanation.probability, expected, places=9)
            self.assertAlmostEqual(
                explanation.bias + sum(explanation.contributions.values()), expected, places=9
            )

    def test_the_input_the_label_depends_on_matters_most(self):
        total = {name: 0.0 for name in FEATURE_NAMES}
        for row in self.rows:
            for name, value in explain_row(self.model, row).contributions.items():
                total[name] += abs(value)
        self.assertEqual(max(total, key=total.get), "rain_30d")

    def test_groups_sum_their_inputs_and_cover_every_input_once(self):
        explanation = explain_row(self.model, self.rows[0])
        factors = grouped(explanation)
        self.assertAlmostEqual(
            sum(f["contribution"] for f in factors), sum(explanation.contributions.values())
        )
        grouped_inputs = [name for _key, _label, names in FACTORS for name in names]
        self.assertCountEqual(grouped_inputs, FEATURE_NAMES)
        sizes = [abs(f["contribution"]) for f in factors]
        self.assertEqual(sizes, sorted(sizes, reverse=True))


class SummaryTests(SimpleTestCase):
    def factor(self, label, times):
        return {"label": label, "times_usual": times}

    def test_names_what_raised_and_what_held_down_with_matching_amounts(self):
        text = summary(
            [
                self.factor("El Niño / La Niña (Pacific Ocean)", 1.26),
                self.factor("Rain in the last 30 days", -0.39),
                self.factor("Time of year", -0.14),
                self.factor("Rain in the month before", -0.11),
            ],
            1.63,
        )
        self.assertEqual(
            text,
            "El Niño / La Niña (Pacific Ocean) raised the risk the most "
            "(+1.3 times the usual chance). Rain in the last 30 days and time of year "
            "held it down the most (−0.5 times the usual chance). "
            "Together: about 1.6 times the usual chance.",
        )

    def test_tiny_factors_are_left_out(self):
        text = summary([self.factor("Elevation and slope", 0.01)], 1.0)
        self.assertEqual(
            text,
            "No input moved the risk far from the usual chance. "
            "Together: about 1.0 times the usual chance.",
        )

    def test_proper_names_keep_their_capitals_mid_sentence(self):
        text = summary(
            [self.factor("Rain in the last 30 days", 0.8), self.factor("Indian Ocean Dipole", 0.2)],
            None,
        )
        self.assertIn("Indian Ocean Dipole also raised it (+0.2", text)


class ExplainAPITests(APITestCase):
    def setUp(self):
        self.region = make_region("Alpha")
        self.score = RiskScore.objects.create(
            region=self.region,
            prediction_date=start_of_day(ISSUED),
            predicted_value=0.2,
            risk_category=RiskScore.RiskCategory.HIGH,
            model_version="test-forest",
        )

    def test_explains_a_score_and_the_parts_add_up(self):
        with mock.patch("riskengine.ml.infer._MODEL", small_model()):
            response = self.client.get(f"/api/risk-scores/{self.score.id}/explain/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.data
        self.assertTrue(body["same_model"])
        self.assertAlmostEqual(
            body["baseline"] + sum(f["contribution"] for f in body["factors"]),
            body["probability"],
            places=3,
        )
        self.assertIn(body["driven_by"], ("rain", "conditions"))
        self.assertTrue(body["summary"].endswith("times the usual chance."))

    def test_placeholder_scores_have_nothing_to_explain(self):
        with mock.patch("riskengine.ml.infer._MODEL", None):
            response = self.client.get(f"/api/risk-scores/{self.score.id}/explain/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class JoinRainTests(SimpleTestCase):
    def test_observed_wins_and_forecast_fills_the_days_after(self):
        observed = {ISSUED - dt.timedelta(days=1): 4.0, ISSUED: 2.0}
        forecast = {ISSUED: 9.0, ISSUED + dt.timedelta(days=1): 7.0}
        joined = join_rain(observed, forecast, ISSUED)
        self.assertEqual(joined[ISSUED], 2.0)
        self.assertEqual(joined[ISSUED + dt.timedelta(days=1)], 7.0)
        self.assertEqual(joined[ISSUED - dt.timedelta(days=1)], 4.0)

    def test_todays_forecast_is_used_only_when_today_was_not_measured(self):
        joined = join_rain({}, {ISSUED: 9.0}, ISSUED)
        self.assertEqual(joined[ISSUED], 9.0)

    def test_a_stored_day_after_the_issue_day_is_ignored(self):
        later = ISSUED + dt.timedelta(days=2)
        joined = join_rain({later: 1.0}, {later: 5.0}, ISSUED)
        self.assertEqual(joined[later], 5.0)

    def test_forecast_totals_count_only_the_days_after_issue_to_the_target(self):
        forecast = {ISSUED + dt.timedelta(days=n): float(n) for n in range(0, 8)}
        total, wettest = forecast_totals(forecast, ISSUED, ISSUED + dt.timedelta(days=3))
        self.assertEqual((total, wettest), (6.0, 3.0))
        self.assertEqual(forecast_totals({}, ISSUED, ISSUED), (None, None))


class ForecastRegionsTests(TestCase):
    def setUp(self):
        self.region = make_region("Alpha")
        EnvironmentalData.objects.bulk_create(
            EnvironmentalData(
                region=self.region,
                record_date=ISSUED - dt.timedelta(days=n),
                rainfall_intensity=3.0,
            )
            for n in range(61)
        )
        self.fetched = []

    def fetch(self, points, days):
        self.fetched.append((points, days))
        series = {ISSUED + dt.timedelta(days=n): 20.0 for n in range(days + 1)}
        return [series for _ in points]

    def run_forecast(self):
        with (
            mock.patch("riskengine.ml.infer._MODEL", small_model()),
            mock.patch("riskengine.ml.infer.MODEL_VERSION", "test-forest"),
        ):
            return forecast_regions(ISSUED, fetch=self.fetch)

    def test_one_forecast_per_region_per_day_ahead(self):
        result = self.run_forecast()
        self.assertEqual(len(result.days), FORECAST_DAYS)
        rows = RiskForecast.objects.order_by("target_date")
        self.assertEqual(rows.count(), FORECAST_DAYS)
        self.assertEqual(rows.first().target_date, ISSUED + dt.timedelta(days=1))
        self.assertEqual(rows.last().target_date, ISSUED + dt.timedelta(days=FORECAST_DAYS))
        self.assertEqual({row.model_version for row in rows}, {"test-forest"})
        # The forecast totals grow by a day's rain each day ahead.
        self.assertEqual([row.rain_forecast_mm for row in rows][:3], [20.0, 40.0, 60.0])
        self.assertTrue(all(row.rain_forecast_max_24h == 20.0 for row in rows))
        self.assertIsInstance(self.fetched[0][0][0], Point)

    def test_forecast_rain_is_never_stored_as_rain_that_fell(self):
        before = EnvironmentalData.objects.count()
        self.run_forecast()
        self.assertEqual(EnvironmentalData.objects.count(), before)
        self.assertFalse(EnvironmentalData.objects.filter(record_date__gt=ISSUED).exists())

    def test_rerunning_the_same_day_replaces_that_issue(self):
        self.run_forecast()
        self.run_forecast()
        self.assertEqual(RiskForecast.objects.count(), FORECAST_DAYS)

    def test_the_command_prints_a_table(self):
        out = StringIO()
        with (
            mock.patch("riskengine.forecast.fetch_forecast_rainfall", side_effect=self.fetch),
            mock.patch("riskengine.ml.infer._MODEL", small_model()),
            mock.patch("riskengine.forecast.timezone.localdate", return_value=ISSUED),
        ):
            call_command("forecast_scores", "--days", "3", stdout=out)
        self.assertIn("Forecast 1 regions for 3 days, issued 2026-10-20", out.getvalue())
        self.assertEqual(RiskForecast.objects.count(), 3)


class ForecastAPITests(APITestCase):
    def setUp(self):
        self.alpha = make_region("Alpha")
        self.beta = make_region("Beta", offset=2.0)
        for issued in (ISSUED - dt.timedelta(days=1), ISSUED):
            for region in (self.alpha, self.beta):
                for n in (1, 2):
                    RiskForecast.objects.create(
                        region=region,
                        issued_on=issued,
                        target_date=issued + dt.timedelta(days=n),
                        predicted_value=0.1 * n,
                        risk_category=RiskScore.RiskCategory.HIGH,
                        model_version="test",
                    )

    def test_days_lists_the_newest_issue(self):
        response = self.client.get("/api/risk-forecast/days/")
        self.assertEqual(
            response.data, {"issued_on": "2026-10-20", "days": ["2026-10-21", "2026-10-22"]}
        )

    def test_one_day_from_the_newest_issue(self):
        response = self.client.get("/api/risk-forecast/", {"target_date": "2026-10-21"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        self.assertEqual({row["issued_on"] for row in response.data}, {"2026-10-20"})
        self.assertIn("times_usual", response.data[0])

    def test_one_regions_week_and_an_earlier_issue(self):
        week = self.client.get("/api/risk-forecast/", {"region": self.alpha.id})
        self.assertEqual([row["target_date"] for row in week.data], ["2026-10-21", "2026-10-22"])
        earlier = self.client.get(
            "/api/risk-forecast/", {"region": self.alpha.id, "issued_on": "2026-10-19"}
        )
        self.assertEqual([row["target_date"] for row in earlier.data], ["2026-10-20", "2026-10-21"])

    def test_no_forecasts_yet(self):
        RiskForecast.objects.all().delete()
        self.assertEqual(
            self.client.get("/api/risk-forecast/days/").data, {"issued_on": None, "days": []}
        )
        self.assertEqual(self.client.get("/api/risk-forecast/").data, [])
