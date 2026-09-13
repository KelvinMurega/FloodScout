from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from .models import EnvironmentalData, GeospatialFeature, Region


class RegionSerializer(GeoFeatureModelSerializer):
    """GeoJSON Feature representation of a Region — `geom` becomes the
    Feature's geometry, everything else its properties. Consumed directly
    by the Leaflet.js dashboard (Sprint 5) as a FeatureCollection.
    """

    class Meta:
        model = Region
        geo_field = "geom"
        fields = ["id", "county_name", "area_size"]


class GeospatialFeatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeospatialFeature
        fields = ["id", "region", "elevation", "slope", "distance_to_river"]


class EnvironmentalDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnvironmentalData
        fields = ["id", "region", "record_date", "rainfall_intensity"]
