"""Tests for area-averaged rainfall: where samples go and how they combine."""

from __future__ import annotations

import datetime as dt
from io import StringIO
from unittest import mock

from django.contrib.gis.geos import MultiPolygon, Point as GEOSPoint, Polygon
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from geodata.management.commands import seed_rainfall
from geodata.models import EnvironmentalData, Region
from geodata.services.sampling import mean_series, rain_sample_points, regional_rainfall

DAY = dt.date(2026, 9, 1)


def _region(name="Big", size=2.0):
    square = Polygon.from_bbox((36.0, -1.0, 36.0 + size, -1.0 + size))
    square.srid = 4326
    return Region(name=name, county_name=name, geom=MultiPolygon(square, srid=4326), area_size=1.0)


class SamplePointTests(SimpleTestCase):
    def test_one_point_is_the_representative_point(self):
        region = _region()
        surface = region.geom.point_on_surface
        [point] = rain_sample_points(region, 1)
        self.assertEqual((point.lon, point.lat), (surface.x, surface.y))

    def test_points_are_distinct_inside_and_spread_out(self):
        region = _region()
        points = rain_sample_points(region, 5)
        self.assertEqual(len(points), 5)
        self.assertEqual(len({(round(p.lon, 1), round(p.lat, 1)) for p in points}), 5)
        for p in points:
            self.assertTrue(region.geom.contains(GEOSPoint(p.lon, p.lat, srid=4326)))
        spread = max(p.lon for p in points) - min(p.lon for p in points)
        self.assertGreater(spread, 1.0)  # covers most of a 2-degree county

    def test_a_region_smaller_than_one_grid_cell_gets_one_point(self):
        self.assertEqual(len(rain_sample_points(_region(size=0.05), 5)), 1)


class MeanSeriesTests(SimpleTestCase):
    def test_averages_days_most_points_have(self):
        series = [{DAY: 10.0, DAY + dt.timedelta(1): 4.0}, {DAY: 20.0}, {DAY: 30.0}]
        self.assertEqual(mean_series(series), {DAY: 20.0})

    def test_regional_rainfall_splits_one_fetch_back_per_region(self):
        regions = [_region("A"), _region("B", size=0.05)]
        fetched = []

        def fetch(points):
            fetched.append(len(points))
            return [{DAY: float(i)} for i in range(len(points))]

        series = regional_rainfall(regions, fetch, 3)
        self.assertEqual(fetched, [4])  # 3 for A, 1 for the tiny B
        self.assertEqual(series, [{DAY: 1.0}, {DAY: 3.0}])


class SeedRainfallPointsTests(TestCase):
    def test_points_option_stores_the_average(self):
        region = _region()
        region.save()

        def fetch(points, past_days):
            return [{DAY: 10.0 * (i + 1)} for i in range(len(points))]

        with mock.patch.object(seed_rainfall, "fetch_daily_rainfall", side_effect=fetch):
            call_command("seed_rainfall", "--points", "3", stdout=StringIO())

        self.assertEqual(EnvironmentalData.objects.get(region=region, record_date=DAY).rainfall_intensity, 20.0)
