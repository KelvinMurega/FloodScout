"""Tests for the area grid: seed_grid, score_grid and the risk-grid API.

The network is never touched: elevation and rainfall fetches are replaced.
Scoring runs the trained model shipped in riskengine/ml/artifacts.
"""

import datetime as dt
from io import StringIO
from unittest import mock

from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.test import APITestCase

from geodata.management.commands.seed_grid import cell_centres
from geodata.models import GridCell, Region
from riskengine.grid import MAX_PAST_DAYS, GridError, score_grid
from riskengine.ml import infer
from riskengine.models import GridRiskScore, RiskScore

TODAY = dt.date(2026, 10, 19)
CATEGORIES = {category.value for category in RiskScore.RiskCategory}


def make_county(name="Alpha", x=36.0, y=-1.0, size=1.0):
    square = Polygon.from_bbox((x, y, x + size, y + size))
    square.srid = 4326
    return Region.objects.create(
        county_name=name, geom=MultiPolygon(square, srid=4326), area_size=12345.0
    )


def make_cell(county, lon, lat):
    return GridCell.objects.create(
        lon=lon, lat=lat, size_deg=0.25, county=county, elevation=1200.0, slope=1.5
    )


def wet_and_dry(points, past_days):
    """The first point gets 40 mm every day, the rest none."""
    days = [TODAY - dt.timedelta(days=n) for n in range(past_days)]
    return [{day: 40.0 if i == 0 else 0.0 for day in days} for i in range(len(points))]


class CellCentreTests(TestCase):
    def test_cells_are_aligned_to_the_grid_and_inside_the_county(self):
        county = make_county(x=36.0, y=-1.0, size=1.0)
        centres = cell_centres(county, 0.25)
        self.assertEqual(len(centres), 16)
        self.assertIn((36.125, -0.875), {(c.lon, c.lat) for c in centres})


class SeedGridTests(TestCase):
    COMMAND = "geodata.management.commands.seed_grid"

    def _seed(self, *args):
        with mock.patch(
            f"{self.COMMAND}.fetch_elevations", side_effect=lambda points: [1000.0] * len(points)
        ):
            call_command("seed_grid", *args, stdout=StringIO())

    def test_one_cell_per_square_with_terrain(self):
        county = make_county()
        self._seed()
        self.assertEqual(GridCell.objects.filter(county=county).count(), 16)
        cell = GridCell.objects.first()
        self.assertEqual((cell.elevation, cell.slope), (1000.0, 0.0))

    def test_a_new_size_replaces_the_grid(self):
        make_county()
        self._seed()
        self._seed("--cell-deg", "0.5")
        self.assertEqual(set(GridCell.objects.values_list("size_deg", flat=True)), {0.5})
        self.assertEqual(GridCell.objects.count(), 4)

    def test_needs_counties(self):
        with self.assertRaises(CommandError):
            self._seed()


@mock.patch("riskengine.grid.timezone.localdate", return_value=TODAY)
class ScoreGridTests(TestCase):
    def setUp(self):
        county = make_county()
        self.wet = make_cell(county, 36.125, -0.875)
        self.dry = make_cell(county, 36.875, -0.125)

    def test_scores_every_cell_from_its_own_rain(self, _today):
        result = score_grid([TODAY], fetch=wet_and_dry)

        self.assertEqual(result.cells, 2)
        wet = GridRiskScore.objects.get(cell=self.wet)
        dry = GridRiskScore.objects.get(cell=self.dry)
        self.assertEqual((wet.rain_30d, dry.rain_30d), (1200.0, 0.0))
        self.assertGreater(wet.predicted_value, dry.predicted_value)
        self.assertLessEqual({wet.risk_category, dry.risk_category}, CATEGORIES)
        self.assertEqual(wet.model_version, infer.MODEL_VERSION)

    def test_fetches_enough_rain_for_the_earliest_day(self, _today):
        fetch = mock.Mock(side_effect=wet_and_dry)
        score_grid([TODAY, TODAY - dt.timedelta(days=3)], fetch=fetch)
        self.assertEqual(fetch.call_args.args[1], 61 + 3)
        self.assertEqual(GridRiskScore.objects.count(), 4)

    def test_a_rerun_replaces_the_day(self, _today):
        score_grid([TODAY], fetch=wet_and_dry)
        score_grid([TODAY], fetch=wet_and_dry)
        self.assertEqual(GridRiskScore.objects.count(), 2)

    def test_a_day_beyond_the_rainfall_record_is_refused(self, _today):
        too_old = TODAY - dt.timedelta(days=MAX_PAST_DAYS)
        with self.assertRaises(GridError):
            score_grid([too_old], fetch=wet_and_dry)

    def test_needs_a_trained_model(self, _today):
        with mock.patch("riskengine.ml.infer._MODEL", None), self.assertRaises(GridError):
            score_grid([TODAY], fetch=wet_and_dry)

    def test_needs_cells(self, _today):
        GridCell.objects.all().delete()
        with self.assertRaises(GridError):
            score_grid([TODAY], fetch=wet_and_dry)


class RiskGridAPITests(APITestCase):
    def setUp(self):
        self.county = make_county()
        other = make_county("Beta", x=38.0)
        self.cells = [make_cell(self.county, 36.125, -0.875), make_cell(other, 38.125, -0.875)]
        for day in (TODAY - dt.timedelta(days=1), TODAY):
            for cell in self.cells:
                GridRiskScore.objects.create(
                    cell=cell,
                    date=day,
                    predicted_value=0.2,
                    risk_category="high",
                    rain_30d=150.0,
                    rain_30d_ratio=1.5,
                    model_version="test-model",
                )

    def test_latest_day_by_default(self):
        response = self.client.get("/api/risk-grid/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["date"], TODAY.isoformat())
        self.assertEqual(response.data["cell_deg"], 0.25)
        self.assertEqual(len(response.data["cells"]), 2)
        cell = response.data["cells"][0]
        self.assertEqual(cell["risk_category"], "high")
        self.assertEqual(cell["times_usual"], infer.times_usual(0.2))

    def test_one_day_and_one_county(self):
        yesterday = (TODAY - dt.timedelta(days=1)).isoformat()
        response = self.client.get(f"/api/risk-grid/?date={yesterday}&county={self.county.id}")
        self.assertEqual(response.data["date"], yesterday)
        self.assertEqual([c["county"] for c in response.data["cells"]], [self.county.id])

    def test_a_malformed_date_is_rejected(self):
        self.assertEqual(self.client.get("/api/risk-grid/?date=yesterday").status_code, 400)

    def test_dates_newest_first(self):
        response = self.client.get("/api/risk-grid/dates/")
        self.assertEqual(
            response.data, [TODAY.isoformat(), (TODAY - dt.timedelta(days=1)).isoformat()]
        )

    def test_nothing_scored_yet(self):
        GridRiskScore.objects.all().delete()
        response = self.client.get("/api/risk-grid/")
        self.assertEqual(response.data["cells"], [])
        self.assertIsNone(response.data["date"])
