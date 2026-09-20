"""Tests for the seed_regions management command.

These never touch the network: each test either points --path at a
fixture written to a temp directory, or patches the download helper.
"""

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from geodata.management.commands import seed_regions
from geodata.models import Region


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
