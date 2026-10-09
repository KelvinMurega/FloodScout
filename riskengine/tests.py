"""Tests for the RiskScore API, the placeholder scorer, the score_regions
command, and the per-region latest-score endpoint.
"""

import datetime as dt
import json
from io import StringIO
from unittest import mock

from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from geodata.models import EnvironmentalData, GeospatialFeature, Region
from geodata.services.climate_indices import oni_for, parse_oni
from riskengine.management.commands.score_regions import start_of_day
from riskengine.ml import train
from riskengine.ml.features import (
    FEATURE_NAMES,
    TERRAIN_FEATURES,
    feature_row,
    rainfall_features,
)
from riskengine.ml.infer import (
    METRICS_PATH,
    MODEL_VERSION,
    NEUTRAL_COMPONENT,
    RAINFALL_SATURATION_MM,
    infer,
    model_infer,
    times_usual,
    usual_chance,
)

from .models import RiskOutlook, RiskScore


def _square(x=36.8, y=-1.3, size=0.1):
    return MultiPolygon(
        Polygon(((x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)))
    )


class RiskScoreAPITests(APITestCase):
    def setUp(self):
        self.region = Region.objects.create(
            county_name="Nairobi", geom=_square(), area_size=696.0
        )
        RiskScore.objects.create(
            region=self.region, predicted_value=0.2, risk_category=RiskScore.RiskCategory.LOW
        )
        self.latest = RiskScore.objects.create(
            region=self.region, predicted_value=0.8, risk_category=RiskScore.RiskCategory.HIGH
        )

    def test_list_scores_for_region(self):
        response = self.client.get(f"/api/risk-scores/?region={self.region.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

    def test_latest_action_returns_most_recent_score(self):
        response = self.client.get(f"/api/risk-scores/latest/?region={self.region.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.latest.id)
        self.assertEqual(response.data["risk_category"], "high")

    def test_latest_action_404_when_no_scores_for_region(self):
        other = Region.objects.create(county_name="Kisumu", geom=_square(x=34.7, y=-0.1), area_size=100.0)
        response = self.client.get(f"/api/risk-scores/latest/?region={other.id}")
        self.assertEqual(response.status_code, 404)


# --- Placeholder scorer, score_regions, latest-by-region ---


SCORE_DATE = dt.date(2026, 4, 15)


def make_region(county_name: str, *, offset: float = 0.0) -> Region:
    """A Region with a throwaway 1x1 degree square for geometry."""
    square = Polygon.from_bbox((offset, offset, offset + 1.0, offset + 1.0))
    square.srid = 4326
    return Region.objects.create(
        county_name=county_name,
        geom=MultiPolygon(square, srid=4326),
        area_size=12345.0,
    )


@mock.patch("riskengine.ml.infer._MODEL", None)
class InferTests(TestCase):
    """The heuristic fallback, used when no trained model is on disk.

    infer() must always hand back a category the model field accepts.
    """

    def test_returns_a_valid_category_with_no_input_data(self):
        region = make_region("Nowhere")

        predicted_value, risk_category = infer(region, SCORE_DATE)

        self.assertIn(risk_category, RiskScore.RiskCategory.values)
        # Both components missing -> the documented neutral fallback.
        self.assertAlmostEqual(predicted_value, NEUTRAL_COMPONENT)
        self.assertEqual(risk_category, RiskScore.RiskCategory.MODERATE)

    def test_returns_a_valid_category_for_a_wet_lowland_region(self):
        region = make_region("Floodplain")
        GeospatialFeature.objects.create(
            region=region, elevation=0.0, slope=0.5, distance_to_river=50.0
        )
        for day_offset in range(7):
            EnvironmentalData.objects.create(
                region=region,
                record_date=SCORE_DATE - dt.timedelta(days=day_offset),
                rainfall_intensity=RAINFALL_SATURATION_MM / 7.0,
            )

        predicted_value, risk_category = infer(region, SCORE_DATE)

        self.assertIn(risk_category, RiskScore.RiskCategory.values)
        self.assertEqual(risk_category, RiskScore.RiskCategory.VERY_HIGH)
        self.assertAlmostEqual(predicted_value, 1.0)

    def test_returns_a_valid_category_for_a_dry_highland_region(self):
        region = make_region("Highland")
        GeospatialFeature.objects.create(
            region=region, elevation=3000.0, slope=20.0, distance_to_river=40000.0
        )
        EnvironmentalData.objects.create(
            region=region, record_date=SCORE_DATE, rainfall_intensity=0.0
        )

        predicted_value, risk_category = infer(region, SCORE_DATE)

        self.assertIn(risk_category, RiskScore.RiskCategory.values)
        self.assertEqual(risk_category, RiskScore.RiskCategory.LOW)
        self.assertAlmostEqual(predicted_value, 0.0)

    def test_rainfall_outside_the_window_is_ignored(self):
        region = make_region("StaleData")
        EnvironmentalData.objects.create(
            region=region,
            record_date=SCORE_DATE - dt.timedelta(days=30),
            rainfall_intensity=500.0,
        )

        predicted_value, risk_category = infer(region, SCORE_DATE)

        self.assertAlmostEqual(predicted_value, NEUTRAL_COMPONENT)
        self.assertIn(risk_category, RiskScore.RiskCategory.values)

    def test_every_category_produced_is_a_valid_choice(self):
        """Sweep elevations across the range; nothing may fall outside the enum."""
        for index, elevation in enumerate(range(0, 3500, 250)):
            region = make_region(f"Sweep {index}", offset=index * 2.0)
            GeospatialFeature.objects.create(
                region=region, elevation=float(elevation), slope=1.0, distance_to_river=100.0
            )
            _value, risk_category = infer(region, SCORE_DATE)
            self.assertIn(risk_category, RiskScore.RiskCategory.values)


class FeatureTests(TestCase):
    def test_rainfall_totals_cover_the_window_and_the_one_before(self):
        rain = {SCORE_DATE - dt.timedelta(days=offset): 1.0 for offset in range(60)}
        rain[SCORE_DATE - dt.timedelta(days=3)] = 50.0   # in the wettest week
        rain[SCORE_DATE - dt.timedelta(days=40)] = 11.0  # in the previous window

        features = rainfall_features(rain, SCORE_DATE)

        self.assertAlmostEqual(features["rain_30d"], 29 + 50)
        self.assertAlmostEqual(features["rain_7d_max"], 6 + 50)
        self.assertAlmostEqual(features["rain_prev_30d"], 29 + 11)

    def test_a_window_with_too_many_missing_days_is_unknown_not_dry(self):
        rain = {SCORE_DATE - dt.timedelta(days=offset): 5.0 for offset in range(10)}

        features = rainfall_features(rain, SCORE_DATE)

        self.assertTrue(all(value != value for value in features.values()))  # NaN

    def test_row_follows_the_feature_order(self):
        rain = {SCORE_DATE - dt.timedelta(days=offset): 2.0 for offset in range(60)}

        terrain = GeospatialFeature(elevation=100.0, slope=2.0, flood_zone_fraction=0.3)

        row = feature_row(rain, SCORE_DATE, normal_30d=30.0, oni=1.8, terrain=terrain)

        named = dict(zip(FEATURE_NAMES, row))
        self.assertAlmostEqual(named["rain_30d_ratio"], 2.0)
        self.assertEqual((named["oni"], named["elevation"], named["slope"]), (1.8, 100.0, 2.0))
        # Stored for the region, but not a model input: see features.py.
        self.assertNotIn("flood_zone_fraction", named)

    def test_row_without_terrain_has_unknown_terrain(self):
        row = feature_row({}, SCORE_DATE, normal_30d=None, oni=None, terrain=None)

        named = dict(zip(FEATURE_NAMES, row))
        self.assertTrue(all(named[name] != named[name] for name in TERRAIN_FEATURES))


class OniTests(TestCase):
    TABLE = parse_oni("SEAS  YR   TOTAL   ANOM\n MJJ 2026  29.02   1.39\n JJA 2026  29.09   1.80\n")

    def test_seasons_are_keyed_by_centre_month(self):
        self.assertEqual(self.TABLE, {(2026, 6): 1.39, (2026, 7): 1.80})

    def test_uses_the_value_published_by_the_date(self):
        # In August the newest published season is the one centred on June.
        self.assertEqual(oni_for(dt.date(2026, 8, 15), self.TABLE), 1.39)

    def test_carries_the_last_value_forward_and_has_none_before_the_table(self):
        self.assertEqual(oni_for(dt.date(2026, 12, 31), self.TABLE), 1.80)
        self.assertIsNone(oni_for(dt.date(2020, 1, 1), self.TABLE))


class ModelInferTests(TestCase):
    """The trained-model path, with a small model fitted in the test."""

    @classmethod
    def setUpTestData(cls):
        # Flood whenever the 30-day rainfall is high; nothing else matters.
        rows = [[float(mm)] + [0.0] * (len(FEATURE_NAMES) - 1) for mm in range(0, 400, 10)]
        labels = [int(row[0] >= 200) for row in rows]
        cls.model = {
            "version": "test-model",
            "pipeline": train.candidates()["logreg"].fit(rows, labels),
            "probability_bands": list(train.PROBABILITY_BANDS),
            "normals": {},
            "oni": {},
        }

    def _region(self, name, daily_mm, offset):
        region = make_region(name, offset=offset)
        for day_offset in range(60):
            EnvironmentalData.objects.create(
                region=region,
                record_date=SCORE_DATE - dt.timedelta(days=day_offset),
                rainfall_intensity=daily_mm,
            )
        return region

    def test_wet_region_scores_above_dry_region(self):
        wet_value, wet_category = model_infer(self.model, self._region("Wet", 12.0, 0.0), SCORE_DATE)
        dry_value, dry_category = model_infer(self.model, self._region("Dry", 0.0, 2.0), SCORE_DATE)

        self.assertGreater(wet_value, dry_value)
        self.assertEqual(wet_category, RiskScore.RiskCategory.VERY_HIGH)
        self.assertEqual(dry_category, RiskScore.RiskCategory.LOW)

    def test_region_with_no_data_still_gets_a_valid_category(self):
        value, category = model_infer(self.model, make_region("Empty"), SCORE_DATE)

        self.assertTrue(0.0 <= value <= 1.0)
        self.assertIn(category, RiskScore.RiskCategory.values)

    def test_infer_uses_the_trained_model_when_one_is_loaded(self):
        region = self._region("Wet", 12.0, 0.0)
        with mock.patch("riskengine.ml.infer._MODEL", self.model):
            self.assertEqual(infer(region, SCORE_DATE), model_infer(self.model, region, SCORE_DATE))


class ScoreRegionsCommandTests(TestCase):
    def setUp(self):
        self.regions = [
            make_region("Alpha", offset=0.0),
            make_region("Beta", offset=2.0),
            make_region("Gamma", offset=4.0),
        ]

    def test_writes_one_row_per_region_stamped_with_the_model_version(self):
        call_command("score_regions", "--date", SCORE_DATE.isoformat(), stdout=StringIO())

        self.assertEqual(RiskScore.objects.count(), len(self.regions))
        self.assertEqual(
            set(RiskScore.objects.values_list("region_id", flat=True)),
            {region.pk for region in self.regions},
        )
        self.assertEqual(
            set(RiskScore.objects.values_list("model_version", flat=True)),
            {MODEL_VERSION},
        )

    def test_is_idempotent_for_the_same_date(self):
        call_command("score_regions", "--date", SCORE_DATE.isoformat(), stdout=StringIO())
        first_pass = {
            score.region_id: (score.pk, score.predicted_value, score.risk_category)
            for score in RiskScore.objects.all()
        }

        call_command("score_regions", "--date", SCORE_DATE.isoformat(), stdout=StringIO())

        self.assertEqual(RiskScore.objects.count(), len(self.regions))
        second_pass = {
            score.region_id: (score.pk, score.predicted_value, score.risk_category)
            for score in RiskScore.objects.all()
        }
        # Same rows updated in place, not a second set inserted.
        self.assertEqual(first_pass, second_pass)

    def test_a_different_date_adds_a_separate_set_of_rows(self):
        call_command("score_regions", "--date", SCORE_DATE.isoformat(), stdout=StringIO())
        call_command(
            "score_regions",
            "--date",
            (SCORE_DATE + dt.timedelta(days=1)).isoformat(),
            stdout=StringIO(),
        )

        self.assertEqual(RiskScore.objects.count(), len(self.regions) * 2)

    def test_defaults_to_today(self):
        call_command("score_regions", stdout=StringIO())

        self.assertEqual(RiskScore.objects.count(), len(self.regions))
        self.assertEqual(
            RiskScore.objects.filter(
                prediction_date=start_of_day(timezone.localdate())
            ).count(),
            len(self.regions),
        )


class LatestByRegionEndpointTests(APITestCase):
    def setUp(self):
        self.regions = [
            make_region("Alpha", offset=0.0),
            make_region("Beta", offset=2.0),
            make_region("Gamma", offset=4.0),
        ]
        # Two days of scores, so "latest per region" has something to choose.
        call_command("score_regions", "--date", SCORE_DATE.isoformat(), stdout=StringIO())
        self.latest_date = SCORE_DATE + dt.timedelta(days=1)
        call_command("score_regions", "--date", self.latest_date.isoformat(), stdout=StringIO())

    def test_returns_exactly_one_row_per_region(self):
        response = self.client.get(reverse("risk-score-latest-by-region"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), len(self.regions))
        self.assertEqual(
            sorted(row["region"] for row in response.data),
            sorted(region.pk for region in self.regions),
        )

    def test_each_row_is_the_most_recent_score_for_its_region(self):
        response = self.client.get(reverse("risk-score-latest-by-region"))

        expected = start_of_day(self.latest_date)
        for row in response.data:
            self.assertEqual(dt.datetime.fromisoformat(row["prediction_date"]), expected)

    def test_rows_expose_the_model_version(self):
        response = self.client.get(reverse("risk-score-latest-by-region"))

        for row in response.data:
            self.assertEqual(row["model_version"], MODEL_VERSION)

    def test_all_regions_come_back_in_a_single_query(self):
        """The whole point: one request and one query, not one per county."""
        with self.assertNumQueries(1):
            self.client.get(reverse("risk-score-latest-by-region"))


# --- Sub-counties, relocation advisory, neighbours ---


def make_sub_county(name: str, county: Region, *, x: float, y: float = 0.0) -> Region:
    """A 0.1 degree square sub-county; squares 0.1 apart share an edge."""
    square = Polygon.from_bbox((x, y, x + 0.1, y + 0.1))
    square.srid = 4326
    return Region.objects.create(
        name=name,
        county_name=county.name,
        level=Region.Level.SUB_COUNTY,
        parent=county,
        geom=MultiPolygon(square, srid=4326),
        area_size=100.0,
    )


def score(region: Region, value: float) -> RiskScore:
    from .ml.infer import classify

    return RiskScore.objects.create(
        region=region, predicted_value=value, risk_category=classify(value)
    )


class ScoreHistoryAPITests(APITestCase):
    """`date_after` and `date_before` on the risk-score endpoints (FR-05)."""

    DAYS = [dt.date(2026, 4, 13), dt.date(2026, 4, 14), dt.date(2026, 4, 15)]

    def setUp(self):
        self.alpha = make_region("Alpha", offset=0.0)
        self.beta = make_region("Beta", offset=2.0)
        for region, base in ((self.alpha, 0.1), (self.beta, 0.5)):
            for step, day in enumerate(self.DAYS):
                RiskScore.objects.create(
                    region=region,
                    prediction_date=start_of_day(day),
                    predicted_value=round(base + step / 10, 1),
                    risk_category=RiskScore.RiskCategory.LOW,
                )

    def _days(self, response):
        return [row["prediction_date"][:10] for row in response.data]

    def test_one_regions_history_is_newest_first(self):
        response = self.client.get(f"/api/risk-scores/?region={self.alpha.id}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual({row["region"] for row in response.data}, {self.alpha.id})
        self.assertEqual(self._days(response), ["2026-04-15", "2026-04-14", "2026-04-13"])

    def test_both_date_bounds_are_inclusive(self):
        response = self.client.get(
            f"/api/risk-scores/?region={self.alpha.id}&date_after=2026-04-14&date_before=2026-04-15"
        )

        self.assertEqual(self._days(response), ["2026-04-15", "2026-04-14"])

    def test_either_bound_works_alone(self):
        after = self.client.get(f"/api/risk-scores/?region={self.alpha.id}&date_after=2026-04-15")
        before = self.client.get(f"/api/risk-scores/?region={self.alpha.id}&date_before=2026-04-13")

        self.assertEqual(self._days(after), ["2026-04-15"])
        self.assertEqual(self._days(before), ["2026-04-13"])

    def test_a_range_with_no_scores_is_empty_not_an_error(self):
        response = self.client.get("/api/risk-scores/?date_after=2026-05-01")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_rejects_a_malformed_date(self):
        for query in ("date_after=14-04-2026", "date_before=yesterday", "date_after=2026-02-30"):
            with self.subTest(query=query):
                response = self.client.get(f"/api/risk-scores/?{query}")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_a_range_that_ends_before_it_starts(self):
        response = self.client.get(
            "/api/risk-scores/?date_after=2026-04-15&date_before=2026-04-13"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date_after", response.data)

    def test_rejects_a_region_that_is_not_an_id(self):
        response = self.client.get("/api/risk-scores/?region=alpha")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_latest_by_region_for_one_day_gives_the_map_as_it_stood(self):
        response = self.client.get(
            "/api/risk-scores/latest-by-region/?date_after=2026-04-14&date_before=2026-04-14"
        )

        self.assertEqual(len(response.data), 2)
        self.assertEqual(set(self._days(response)), {"2026-04-14"})
        self.assertEqual(
            {row["region_name"]: row["predicted_value"] for row in response.data},
            {"Alpha": 0.2, "Beta": 0.6},
        )

    def test_dates_lists_each_day_once_newest_first(self):
        response = self.client.get("/api/risk-scores/dates/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, ["2026-04-15", "2026-04-14", "2026-04-13"])

    def test_dates_honours_the_region_filters(self):
        RiskScore.objects.create(
            region=self.beta,
            prediction_date=start_of_day(dt.date(2026, 4, 16)),
            predicted_value=0.9,
            risk_category=RiskScore.RiskCategory.HIGH,
        )

        response = self.client.get(f"/api/risk-scores/dates/?region={self.alpha.id}")

        self.assertEqual(response.data, ["2026-04-15", "2026-04-14", "2026-04-13"])


class RelocationAdvisoryTests(TestCase):
    def test_maps_each_category(self):
        from .advisory import NONE, PREPARE, RELOCATE, relocation_advisory

        self.assertEqual(relocation_advisory(RiskScore.RiskCategory.VERY_HIGH), RELOCATE)
        self.assertEqual(relocation_advisory(RiskScore.RiskCategory.HIGH), PREPARE)
        self.assertEqual(relocation_advisory(RiskScore.RiskCategory.MODERATE), NONE)
        self.assertEqual(relocation_advisory(RiskScore.RiskCategory.LOW), NONE)


class SubCountyScoreAPITests(APITestCase):
    def setUp(self):
        self.nairobi = make_region("Nairobi", offset=0.0)
        self.garissa = make_region("Garissa", offset=5.0)
        # A row of three touching sub-counties and one far away.
        self.kibra = make_sub_county("Kibra", self.nairobi, x=0.1)
        self.langata = make_sub_county("Langata", self.nairobi, x=0.2)
        self.westlands = make_sub_county("Westlands", self.nairobi, x=0.3)
        self.dadaab = make_sub_county("Dadaab", self.garissa, x=5.1, y=5.1)

        score(self.nairobi, 0.4)
        score(self.garissa, 0.6)
        score(self.kibra, 0.2)       # low
        score(self.langata, 0.9)     # very high
        score(self.westlands, 0.4)   # moderate
        score(self.dadaab, 0.1)      # low, but nowhere near Langata

    def test_rows_carry_region_name_level_and_advisory(self):
        response = self.client.get(
            reverse("risk-score-latest-by-region"), {"level": "sub_county"}
        )

        rows = {row["region_name"]: row for row in response.data}
        self.assertEqual(set(rows), {"Kibra", "Langata", "Westlands", "Dadaab"})
        self.assertEqual(rows["Langata"]["county_name"], "Nairobi")
        self.assertEqual(rows["Langata"]["region_level"], "sub_county")
        self.assertEqual(rows["Langata"]["relocation_advisory"], "relocate")
        self.assertEqual(rows["Kibra"]["relocation_advisory"], "none")

    def test_latest_by_region_filters_by_focus_region(self):
        response = self.client.get(
            reverse("risk-score-latest-by-region"),
            {"level": "sub_county", "focus_region": "nairobi"},
        )

        self.assertEqual(
            [row["region_name"] for row in response.data], ["Kibra", "Langata", "Westlands"]
        )

    def test_latest_by_region_is_still_one_query_when_filtered(self):
        with self.assertNumQueries(1):
            self.client.get(
                reverse("risk-score-latest-by-region"),
                {"level": "sub_county", "focus_region": "nairobi"},
            )

    def test_lower_risk_neighbours_are_adjacent_and_lower_least_risky_first(self):
        response = self.client.get(
            reverse("risk-score-lower-risk-neighbours"), {"region": self.langata.id}
        )

        self.assertEqual(response.status_code, 200)
        # Dadaab is lower risk but not adjacent; counties are another level.
        self.assertEqual([row["region_name"] for row in response.data], ["Kibra", "Westlands"])

    def test_lower_risk_neighbours_excludes_equal_or_higher_risk(self):
        response = self.client.get(
            reverse("risk-score-lower-risk-neighbours"), {"region": self.kibra.id}
        )

        self.assertEqual(response.data, [])

    def test_lower_risk_neighbours_needs_a_region(self):
        url = reverse("risk-score-lower-risk-neighbours")
        self.assertEqual(self.client.get(url).status_code, 400)
        self.assertEqual(self.client.get(url, {"region": 999999}).status_code, 404)


class RiskOutlookAPITests(APITestCase):
    def setUp(self):
        self.nairobi = make_region("Nairobi", offset=0.0)
        self.garissa = make_region("Garissa", offset=5.0)
        self.kibra = make_sub_county("Kibra", self.nairobi, x=0.1)
        for region, month, probability in (
            (self.nairobi, dt.date(2026, 11, 1), 0.17),
            (self.nairobi, dt.date(2026, 12, 1), 0.12),
            (self.garissa, dt.date(2026, 11, 1), 0.06),
            (self.kibra, dt.date(2026, 11, 1), 0.25),
        ):
            RiskOutlook.objects.create(
                region=region,
                month=month,
                flood_probability=probability,
                probability_low=probability / 2,
                probability_high=probability * 2,
                rain_vs_normal=2.3,
                risk_category=RiskScore.RiskCategory.HIGH,
                model_version="test-model",
                issued_on=dt.date(2026, 10, 1),
            )

    def test_months_lists_each_month_once_soonest_first(self):
        response = self.client.get(reverse("risk-outlook-months"))

        self.assertEqual(
            [row["month"] for row in response.data], [dt.date(2026, 11, 1), dt.date(2026, 12, 1)]
        )

    def test_filters_by_month_and_level(self):
        response = self.client.get(
            reverse("risk-outlook-list"), {"month": "2026-11-01", "level": "county"}
        )

        rows = {row["region_name"]: row for row in response.data}
        self.assertEqual(set(rows), {"Nairobi", "Garissa"})
        self.assertEqual(rows["Nairobi"]["predicted_value"], 0.17)
        self.assertEqual(rows["Nairobi"]["month"], "2026-11-01")

    def test_filters_by_focus_region(self):
        response = self.client.get(
            reverse("risk-outlook-list"),
            {"month": "2026-11-01", "level": "sub_county", "focus_region": "nairobi"},
        )

        self.assertEqual([row["region_name"] for row in response.data], ["Kibra"])

    def test_rows_carry_no_relocation_advisory(self):
        response = self.client.get(reverse("risk-outlook-list"), {"month": "2026-11-01"})

        self.assertTrue(all("relocation_advisory" not in row for row in response.data))

    def test_rejects_a_malformed_month(self):
        response = self.client.get(reverse("risk-outlook-list"), {"month": "November"})

        self.assertEqual(response.status_code, 400)


class RefreshScoresCommandTests(TestCase):
    COMMANDS = "riskengine.management.commands.refresh_scores"

    def test_fetches_rainfall_then_scores(self):
        with mock.patch(f"{self.COMMANDS}.call_command") as run:
            call_command("refresh_scores", stdout=StringIO())
        self.assertEqual([call.args[0] for call in run.call_args_list], ["seed_rainfall", "score_regions"])

    def test_a_failed_fetch_stops_a_single_run(self):
        with mock.patch(f"{self.COMMANDS}.call_command", side_effect=CommandError("no network")):
            with self.assertRaises(CommandError):
                call_command("refresh_scores", stdout=StringIO())

    def test_on_a_schedule_a_failed_run_is_reported_and_the_next_one_still_happens(self):
        class Stop(Exception):
            pass

        stderr = StringIO()
        with mock.patch(
            f"{self.COMMANDS}.call_command", side_effect=CommandError("no network")
        ) as run, mock.patch(f"{self.COMMANDS}._sleep", side_effect=[None, Stop]) as sleep:
            with self.assertRaises(Stop):
                call_command("refresh_scores", "--every-hours", "24", stdout=StringIO(), stderr=stderr)
        self.assertEqual(run.call_count, 2)  # one failed attempt per run, two runs
        sleep.assert_called_with(24 * 3600)
        self.assertIn("no network", stderr.getvalue())

    def test_the_interval_must_be_positive(self):
        with self.assertRaises(CommandError):
            call_command("refresh_scores", "--every-hours", "0", stdout=StringIO())


class TimesUsualTests(APITestCase):
    def test_a_probability_is_a_multiple_of_the_usual_chance(self):
        self.assertEqual(times_usual(0.30, usual=0.10), 3.0)
        self.assertEqual(times_usual(0.02, usual=0.0968), 0.2)

    def test_nothing_to_compare_with_gives_none(self):
        self.assertIsNone(times_usual(0.30, usual=None))
        self.assertIsNone(times_usual(None, usual=0.10))

    def test_the_usual_chance_is_the_trained_base_rate(self):
        # The shipped model and its metrics.json are in the repository.
        self.assertEqual(usual_chance(), json.loads(METRICS_PATH.read_text())["base_rate"])

    def test_scores_carry_the_multiple_in_the_api(self):
        region = make_region("Alpha")
        RiskScore.objects.create(
            region=region, predicted_value=0.29, risk_category=RiskScore.RiskCategory.VERY_HIGH
        )
        response = self.client.get(f"/api/risk-scores/latest/?region={region.id}")
        self.assertEqual(response.data["times_usual"], times_usual(0.29))
        self.assertGreater(response.data["times_usual"], 2.5)
