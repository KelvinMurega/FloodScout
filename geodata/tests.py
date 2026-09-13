from django.contrib.gis.geos import MultiPolygon, Polygon
from rest_framework.test import APITestCase

from .models import EnvironmentalData, GeospatialFeature, Region


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
