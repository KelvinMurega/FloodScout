"""Tests for the seed_regions management command.

These never touch the network: each test either points --path at a
fixture written to a temp directory, or patches the download helper.
"""

import datetime
import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.gis.geos import MultiPolygon, Point as GEOSPoint, Polygon
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.test import APITestCase

from geodata.management.commands import seed_rainfall, seed_regions, seed_terrain
from geodata.models import EnvironmentalData, GeospatialFeature, Region
from geodata.serializers import COORDINATE_PRECISION
from geodata.services import open_meteo
from geodata.services.open_meteo import OpenMeteoError


def _box(min_lon, min_lat, max_lon, max_lat):
    """A closed GeoJSON linear ring."""
    return [
        [min_lon, min_lat],
        [max_lon, min_lat],
        [max_lon, max_lat],
        [min_lon, max_lat],
        [min_lon, min_lat],
    ]


# A 0.3 deg x 0.3 deg box sitting on the equator near Kisumu. On the
# ground that is roughly 33.4 km x 33.2 km, so a correct km2 computation
# lands near 1108. The same figure read straight off EPSG:4326 would be
# 0.09 -- four orders of magnitude out -- which is what makes this a real
# check that the projection step happened.
KISUMU_RING = _box(34.60, -0.20, 34.90, 0.10)

FIXTURE = {
    "type": "FeatureCollection",
    "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
    "features": [
        {
            # Arrives as a plain Polygon, like 43 of the 47 real counties.
            "type": "Feature",
            "properties": {"shapeName": "Kisumu", "shapeISO": "KE-42"},
            "geometry": {"type": "Polygon", "coordinates": [KISUMU_RING]},
        },
        {
            # Arrives already as a MultiPolygon, like the coastal counties.
            "type": "Feature",
            "properties": {"shapeName": "Lamu", "shapeISO": "KE-05"},
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [_box(40.80, -2.40, 40.90, -2.30)],
                    [_box(41.00, -2.20, 41.10, -2.10)],
                ],
            },
        },
    ],
}


class SeedRegionsTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture_path = Path(self._tmp.name) / "counties.geojson"
        self.fixture_path.write_text(json.dumps(FIXTURE), encoding="utf-8")

    def run_command(self, **kwargs):
        out = StringIO()
        call_command("seed_regions", path=self.fixture_path, stdout=out, **kwargs)
        return out.getvalue()

    # (a) Polygon input gets stored as a MultiPolygon.
    def test_polygon_input_is_stored_as_multipolygon(self):
        self.run_command()

        kisumu = Region.objects.get(county_name="Kisumu")
        self.assertEqual(kisumu.geom.geom_type, "MultiPolygon")
        self.assertEqual(kisumu.geom.srid, 4326)
        # One polygon in, one polygon out -- wrapping must not lose geometry.
        self.assertEqual(len(kisumu.geom), 1)

    def test_multipolygon_input_is_preserved(self):
        self.run_command()

        lamu = Region.objects.get(county_name="Lamu")
        self.assertEqual(lamu.geom.geom_type, "MultiPolygon")
        self.assertEqual(len(lamu.geom), 2)

    # (b) area_size is computed in plausible square kilometres.
    def test_area_size_is_in_square_kilometres(self):
        self.run_command()

        kisumu = Region.objects.get(county_name="Kisumu")
        self.assertGreater(kisumu.area_size, 1050)
        self.assertLess(kisumu.area_size, 1200)

    def test_area_size_is_not_degrees(self):
        """Guard the specific bug: reading .area straight off EPSG:4326."""
        self.run_command()

        kisumu = Region.objects.get(county_name="Kisumu")
        degrees_area = 0.3 * 0.3
        self.assertNotAlmostEqual(kisumu.area_size, degrees_area, places=2)

    # (c) Running twice does not duplicate rows.
    def test_rerunning_does_not_duplicate_rows(self):
        first = self.run_command()
        self.assertEqual(Region.objects.count(), 2)
        self.assertIn("Created: 2", first)

        second = self.run_command()
        self.assertEqual(Region.objects.count(), 2)
        self.assertIn("Created: 0", second)
        self.assertIn("Updated: 2", second)

    def test_rerunning_refreshes_values_in_place(self):
        self.run_command()
        kisumu = Region.objects.get(county_name="Kisumu")
        original_pk, original_area = kisumu.pk, kisumu.area_size

        Region.objects.filter(pk=original_pk).update(area_size=0.0)
        self.run_command()

        kisumu.refresh_from_db()
        self.assertEqual(kisumu.pk, original_pk)
        self.assertAlmostEqual(kisumu.area_size, original_area, places=6)

    # --county selects a single county.
    def test_county_option_loads_only_that_county(self):
        self.run_command(county="Kisumu")

        self.assertEqual(Region.objects.count(), 1)
        self.assertEqual(Region.objects.get().county_name, "Kisumu")

    def test_county_option_is_case_insensitive(self):
        self.run_command(county="kISUMU")

        self.assertEqual(Region.objects.count(), 1)

    def test_unknown_county_raises(self):
        with self.assertRaises(CommandError):
            self.run_command(county="Atlantis")
        self.assertEqual(Region.objects.count(), 0)

    def test_missing_path_raises(self):
        with self.assertRaises(CommandError):
            call_command("seed_regions", path=Path(self._tmp.name) / "nope.geojson")

    # The download path is exercised without any network access.
    def test_downloads_when_no_path_given(self):
        cache_path = Path(self._tmp.name) / "downloaded.geojson"

        def fake_download(url, dest):
            dest.write_text(json.dumps(FIXTURE), encoding="utf-8")
            return dest

        with mock.patch.object(seed_regions, "DEFAULT_CACHE_PATH", cache_path), mock.patch.object(
            seed_regions, "download_boundaries", side_effect=fake_download
        ) as mocked:
            out = StringIO()
            call_command("seed_regions", stdout=out)

        mocked.assert_called_once()
        called_url = mocked.call_args.args[0]
        self.assertEqual(called_url, seed_regions.SOURCE_URL)
        self.assertEqual(Region.objects.count(), 2)

    def test_uses_cached_file_instead_of_redownloading(self):
        cache_path = Path(self._tmp.name) / "cached.geojson"
        cache_path.write_text(json.dumps(FIXTURE), encoding="utf-8")

        with mock.patch.object(seed_regions, "DEFAULT_CACHE_PATH", cache_path), mock.patch.object(
            seed_regions, "download_boundaries"
        ) as mocked:
            out = StringIO()
            call_command("seed_regions", stdout=out)

        mocked.assert_not_called()
        self.assertEqual(Region.objects.count(), 2)


class HelperTests(TestCase):
    def test_resolve_name_field_reports_available_fields(self):
        layer = mock.Mock()
        layer.fields = ["OBJECTID", "Shape_Area"]

        with self.assertRaises(CommandError) as ctx:
            seed_regions.resolve_name_field(layer)

        self.assertIn("OBJECTID", str(ctx.exception))


# --- REST API surface (IR-01) ---


def _square(x=36.8, y=-1.3, size=0.1):
    return MultiPolygon(
        Polygon(((x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)))
    )


class RegionAPITests(APITestCase):
    def setUp(self):
        self.region = Region.objects.create(
            county_name="Nairobi", geom=_square(), area_size=696.0
        )

    def test_list_regions_returns_geojson_feature_collection(self):
        response = self.client.get("/api/regions/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["type"], "FeatureCollection")
        self.assertEqual(len(response.data["features"]), 1)
        self.assertEqual(
            response.data["features"][0]["properties"]["county_name"], "Nairobi"
        )

    def test_geometry_is_a_geojson_object_not_an_ewkt_string(self):
        """Leaflet needs a real geometry object.

        Without an explicit GeometryField the geo_field serialises to the
        string "SRID=4326;MULTIPOLYGON (((...)))", which renders nothing and
        fails silently in the browser.
        """
        response = self.client.get("/api/regions/")

        geometry = response.data["features"][0]["geometry"]
        self.assertNotIsInstance(geometry, str)
        self.assertEqual(geometry["type"], "MultiPolygon")
        self.assertTrue(geometry["coordinates"])

    def test_coordinates_are_trimmed_to_the_documented_precision(self):
        response = self.client.get("/api/regions/")

        lon, lat = response.data["features"][0]["geometry"]["coordinates"][0][0][0]
        for ordinate in (lon, lat):
            decimals = str(ordinate)[::-1].find(".")
            self.assertLessEqual(decimals, COORDINATE_PRECISION)


class GeospatialFeatureAPITests(APITestCase):
    def setUp(self):
        self.region = Region.objects.create(
            county_name="Nairobi", geom=_square(), area_size=696.0
        )
        self.other_region = Region.objects.create(
            county_name="Kisumu", geom=_square(x=34.7, y=-0.1), area_size=100.0
        )
        GeospatialFeature.objects.create(
            region=self.region, elevation=1700, slope=2.5, distance_to_river=500
        )
        GeospatialFeature.objects.create(
            region=self.other_region, elevation=1130, slope=1.0, distance_to_river=200
        )

    def test_filter_by_region(self):
        response = self.client.get(f"/api/geospatial-features/?region={self.region.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["region"], self.region.id)

    def test_unfiltered_returns_all(self):
        response = self.client.get("/api/geospatial-features/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)


class EnvironmentalDataAPITests(APITestCase):
    def test_list_environmental_data(self):
        region = Region.objects.create(county_name="Nairobi", geom=_square(), area_size=696.0)
        EnvironmentalData.objects.create(
            region=region, record_date="2026-01-01", rainfall_intensity=12.5
        )
        response = self.client.get("/api/environmental-data/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["rainfall_intensity"], 12.5)


# --- Ingesting real terrain and rainfall (Open-Meteo) ---


class SlopeMathTests(TestCase):
    """slope_degrees is pure arithmetic, so it is checked against hand
    calculations rather than against the network.
    """

    def test_flat_terrain_has_zero_slope(self):
        slope = seed_terrain.slope_degrees(
            centre=100.0, north=100.0, south=100.0, east=100.0, west=100.0, lat=0.0
        )

        self.assertAlmostEqual(slope, 0.0)

    def test_known_north_south_gradient(self):
        # North and south samples are 2 * 0.01 deg * 111320 m apart. Making
        # the rise equal that run gives a gradient of exactly 1 => 45 degrees.
        run = 2 * seed_terrain.SLOPE_OFFSET_DEG * seed_terrain.METRES_PER_DEGREE
        slope = seed_terrain.slope_degrees(
            centre=0.0, north=run, south=0.0, east=0.0, west=0.0, lat=0.0
        )

        self.assertAlmostEqual(slope, 45.0, places=6)

    def test_downhill_slope_is_reported_as_positive(self):
        downhill = seed_terrain.slope_degrees(
            centre=0.0, north=0.0, south=500.0, east=0.0, west=0.0, lat=0.0
        )

        self.assertGreater(downhill, 0.0)


class SeedTerrainTests(TestCase):
    def setUp(self):
        # The command walks regions ordered by county_name, so Mombasa is
        # sampled before Nairobi. The fixtures below follow that order.
        self.nairobi = Region.objects.create(
            county_name="Nairobi", geom=_square(), area_size=696.0
        )
        self.mombasa = Region.objects.create(
            county_name="Mombasa", geom=_square(x=39.6, y=-4.0), area_size=229.0
        )

    def _elevations(self, first_centre=20.0, second_centre=1600.0):
        """Five samples per region, in the order the command asks for them.

        First block is Mombasa (alphabetically first), second is Nairobi.
        """
        return [
            first_centre,
            first_centre + 10,
            first_centre - 10,
            first_centre + 5,
            first_centre - 5,
            second_centre,
            second_centre + 2,
            second_centre - 2,
            second_centre + 1,
            second_centre - 1,
        ]

    def test_writes_one_feature_per_region_from_the_centre_sample(self):
        with mock.patch.object(
            seed_terrain, "fetch_elevations", return_value=self._elevations()
        ):
            call_command("seed_terrain", stdout=StringIO())

        self.assertEqual(GeospatialFeature.objects.count(), 2)
        self.assertAlmostEqual(
            GeospatialFeature.objects.get(region=self.nairobi).elevation, 1600.0
        )
        self.assertAlmostEqual(
            GeospatialFeature.objects.get(region=self.mombasa).elevation, 20.0
        )
        self.assertGreater(
            GeospatialFeature.objects.get(region=self.nairobi).slope, 0.0
        )

    def test_distance_to_river_is_left_unknown_rather_than_invented(self):
        with mock.patch.object(
            seed_terrain, "fetch_elevations", return_value=self._elevations()
        ):
            call_command("seed_terrain", stdout=StringIO())

        self.assertTrue(
            all(f.distance_to_river is None for f in GeospatialFeature.objects.all())
        )

    def test_rerunning_updates_in_place(self):
        with mock.patch.object(
            seed_terrain, "fetch_elevations", return_value=self._elevations()
        ):
            call_command("seed_terrain", stdout=StringIO())
        original_pks = set(GeospatialFeature.objects.values_list("pk", flat=True))

        with mock.patch.object(
            seed_terrain,
            "fetch_elevations",
            return_value=self._elevations(second_centre=1700.0),
        ):
            call_command("seed_terrain", stdout=StringIO())

        self.assertEqual(GeospatialFeature.objects.count(), 2)
        self.assertEqual(
            set(GeospatialFeature.objects.values_list("pk", flat=True)), original_pks
        )
        self.assertAlmostEqual(
            GeospatialFeature.objects.get(region=self.nairobi).elevation, 1700.0
        )

    def test_samples_five_points_per_region_starting_inside_the_polygon(self):
        with mock.patch.object(
            seed_terrain, "fetch_elevations", return_value=self._elevations()
        ) as fetch:
            call_command("seed_terrain", stdout=StringIO())

        points = fetch.call_args.args[0]
        self.assertEqual(len(points), 10)
        # points[0] is Mombasa (ordered first), points[5] is Nairobi. Each
        # centre sample must land inside its own county, not merely near it.
        self.assertTrue(
            self.mombasa.geom.contains(GEOSPoint(points[0].lon, points[0].lat, srid=4326))
        )
        self.assertTrue(
            self.nairobi.geom.contains(GEOSPoint(points[5].lon, points[5].lat, srid=4326))
        )

    def test_upstream_failure_is_a_command_error_and_writes_nothing(self):
        with mock.patch.object(
            seed_terrain, "fetch_elevations", side_effect=OpenMeteoError("boom")
        ):
            with self.assertRaises(CommandError):
                call_command("seed_terrain", stdout=StringIO())

        self.assertEqual(GeospatialFeature.objects.count(), 0)


class SeedRainfallTests(TestCase):
    SERIES = [
        {
            datetime.date(2026, 9, 20): 1.5,
            datetime.date(2026, 9, 21): 0.0,
            datetime.date(2026, 9, 22): 12.25,
        }
    ]

    def setUp(self):
        self.region = Region.objects.create(
            county_name="Nairobi", geom=_square(), area_size=696.0
        )

    def test_writes_one_row_per_region_per_day(self):
        with mock.patch.object(
            seed_rainfall, "fetch_daily_rainfall", return_value=self.SERIES
        ):
            call_command("seed_rainfall", stdout=StringIO())

        self.assertEqual(EnvironmentalData.objects.count(), 3)
        self.assertAlmostEqual(
            EnvironmentalData.objects.get(
                record_date=datetime.date(2026, 9, 22)
            ).rainfall_intensity,
            12.25,
        )

    def test_rerunning_does_not_duplicate_days(self):
        with mock.patch.object(
            seed_rainfall, "fetch_daily_rainfall", return_value=self.SERIES
        ):
            call_command("seed_rainfall", stdout=StringIO())
            call_command("seed_rainfall", stdout=StringIO())

        self.assertEqual(EnvironmentalData.objects.count(), 3)

    def test_rerunning_refreshes_the_value(self):
        with mock.patch.object(
            seed_rainfall, "fetch_daily_rainfall", return_value=self.SERIES
        ):
            call_command("seed_rainfall", stdout=StringIO())

        revised = [dict(self.SERIES[0])]
        revised[0][datetime.date(2026, 9, 22)] = 40.0
        with mock.patch.object(
            seed_rainfall, "fetch_daily_rainfall", return_value=revised
        ):
            call_command("seed_rainfall", stdout=StringIO())

        self.assertEqual(EnvironmentalData.objects.count(), 3)
        self.assertAlmostEqual(
            EnvironmentalData.objects.get(
                record_date=datetime.date(2026, 9, 22)
            ).rainfall_intensity,
            40.0,
        )

    def test_past_days_must_be_positive(self):
        with self.assertRaises(CommandError):
            call_command("seed_rainfall", "--past-days", "0", stdout=StringIO())


class OpenMeteoClientTests(TestCase):
    def test_null_precipitation_days_are_dropped_not_stored_as_zero(self):
        payload = {
            "daily": {
                "time": ["2026-09-20", "2026-09-21", "2026-09-22"],
                "precipitation_sum": [1.0, None, 3.0],
            }
        }
        with mock.patch.object(open_meteo, "_get_json", return_value=payload):
            series = open_meteo.fetch_daily_rainfall(
                [open_meteo.Point(36.8, -1.3)], past_days=3
            )

        self.assertEqual(
            series[0],
            {datetime.date(2026, 9, 20): 1.0, datetime.date(2026, 9, 22): 3.0},
        )

    def test_short_elevation_response_is_an_error(self):
        with mock.patch.object(
            open_meteo, "_get_json", return_value={"elevation": [1.0]}
        ):
            with self.assertRaises(OpenMeteoError):
                open_meteo.fetch_elevations(
                    [open_meteo.Point(36.8, -1.3), open_meteo.Point(39.6, -4.0)]
                )
