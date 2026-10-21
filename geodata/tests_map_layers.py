"""Past floods (FloodEvent, seed_flood_events, flood-events/) and the static
map layers (build_map_layers)."""

import datetime as dt
from io import StringIO
from unittest import mock

import numpy as np
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from geodata.management.commands import build_map_layers, seed_flood_events
from geodata.models import FloodEvent, Region
from geodata.services import dfo


def county(name, west, south, size=1.0):
    square = Polygon.from_bbox((west, south, west + size, south + size))
    square.srid = 4326
    return Region.objects.create(
        county_name=name, geom=MultiPolygon(square, srid=4326), area_size=10000.0
    )


def event(event_id, lon, lat, *, area=100.0, began=dt.date(2018, 4, 1), dead=0, displaced=0):
    return dfo.FloodEvent(
        id=event_id,
        country="Kenya",
        began=began,
        ended=began + dt.timedelta(days=10),
        lon=lon,
        lat=lat,
        area_km2=area,
        severity=1.0,
        dead=dead,
        displaced=displaced,
        main_cause="Heavy rain",
    )


class SeedFloodEventsTests(TestCase):
    def setUp(self):
        county("Kisumu", 34.5, -0.5)

    def run_with(self, events):
        out = StringIO()
        with (
            mock.patch.object(dfo, "download", return_value="unused"),
            mock.patch.object(dfo, "load_events", return_value=events),
        ):
            call_command("seed_flood_events", stdout=out)
        return out.getvalue()

    def test_keeps_only_events_that_reach_kenya(self):
        out = self.run_with(
            [
                event(1, 35.0, 0.0),  # inside
                event(2, 30.0, 0.0),  # Uganda-side, far outside
                event(3, -60.0, -10.0),  # another continent
            ]
        )
        self.assertEqual(list(FloodEvent.objects.values_list("dfo_id", flat=True)), [1])
        self.assertIn("1 of 3 DFO events reach Kenya", out)

    def test_an_event_centred_outside_counts_when_its_footprint_reaches_in(self):
        # About 70 km west of the square, with a radius of about 400 km.
        self.run_with([event(4, 33.9, 0.0, area=500_000.0)])
        self.assertTrue(FloodEvent.objects.filter(dfo_id=4).exists())

    def test_rerun_updates_instead_of_duplicating(self):
        self.run_with([event(1, 35.0, 0.0, dead=1)])
        self.run_with([event(1, 35.0, 0.0, dead=5)])
        self.assertEqual(FloodEvent.objects.count(), 1)
        self.assertEqual(FloodEvent.objects.get().dead, 5)

    def test_needs_counties(self):
        Region.objects.all().delete()
        with self.assertRaises(CommandError):
            self.run_with([])


class KenyanEventsTests(TestCase):
    def test_cheap_box_rejects_far_events_without_geometry(self):
        kenya = mock.Mock()
        kept = seed_flood_events.kenyan_events([event(1, 100.0, 0.0)], kenya)
        self.assertEqual(kept, [])
        kenya.intersects.assert_not_called()


class FloodEventAPITests(APITestCase):
    def setUp(self):
        self.kisumu = county("Kisumu", 34.5, -0.5)
        self.garissa = county("Garissa", 39.0, -1.0)
        for dfo_id, lon, began in (
            (10, 35.0, dt.date(2002, 4, 26)),
            (11, 39.5, dt.date(2018, 5, 1)),
        ):
            centre = Point(lon, 0.0 if lon < 36 else -0.5, srid=4326)
            FloodEvent.objects.create(
                dfo_id=dfo_id,
                began=began,
                ended=began + dt.timedelta(days=5),
                centroid=centre,
                footprint=centre.buffer(0.1),
                area_km2=1000.0,
                severity=1.5,
                dead=160,
                displaced=168000,
                main_cause="Heavy rain",
            )

    def test_list_is_geojson_newest_first(self):
        response = self.client.get("/api/flood-events/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["type"], "FeatureCollection")
        self.assertEqual([f["id"] for f in response.data["features"]], [11, 10])
        first = response.data["features"][1]
        self.assertEqual(first["geometry"], {"type": "Point", "coordinates": [35.0, 0.0]})
        self.assertEqual(first["properties"]["dead"], 160)
        self.assertEqual(first["properties"]["began"], "2002-04-26")

    def test_region_keeps_events_whose_footprint_reaches_it(self):
        response = self.client.get("/api/flood-events/", {"region": self.kisumu.id})
        self.assertEqual([f["id"] for f in response.data["features"]], [10])

    def test_bad_and_unknown_regions(self):
        self.assertEqual(
            self.client.get("/api/flood-events/", {"region": "x"}).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self.client.get("/api/flood-events/", {"region": 99999}).status_code,
            status.HTTP_404_NOT_FOUND,
        )


class MapLayerHelperTests(TestCase):
    def test_mercator_corners_round_trip_to_degrees(self):
        lon, lat = build_map_layers.mercator_to_lonlat(0.0, 0.0)
        self.assertAlmostEqual(lon, 0.0)
        self.assertAlmostEqual(lat, 0.0)
        # 5 degrees north is 557,305 m in Web Mercator.
        _lon, lat = build_map_layers.mercator_to_lonlat(0.0, 557305.0)
        self.assertAlmostEqual(lat, 5.0, places=3)

    def test_colour_depths_is_clear_where_dry_and_darker_where_deeper(self):
        depth = np.array([[0.0, 0.2, 1.0, 5.0, np.nan, 1e9]])
        rgba = build_map_layers.colour_depths(depth)
        alpha = rgba[3][0]
        self.assertEqual(alpha[0], 0)  # dry
        self.assertEqual(alpha[4], 0)  # no data
        self.assertEqual(alpha[5], 0)  # the source's nodata fill
        self.assertTrue(alpha[1] > 0 and alpha[2] > 0 and alpha[3] > 0)
        blue_of = rgba[2][0]
        # Deeper water is drawn darker.
        self.assertGreater(int(blue_of[1]), int(blue_of[3]))
