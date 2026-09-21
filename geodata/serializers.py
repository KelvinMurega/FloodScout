from rest_framework import serializers
from rest_framework_gis.fields import GeometryField
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from .models import EnvironmentalData, GeospatialFeature, Region

# Decimal places kept in emitted coordinates. 5 dp is ~1.1 m at the
# equator — far finer than county boundaries are surveyed.
COORDINATE_PRECISION = 5


class RegionSerializer(GeoFeatureModelSerializer):
    """GeoJSON Feature representation of a Region — `geom` becomes the
    Feature's geometry, everything else its properties. Consumed directly
    by the Leaflet.js dashboard (Sprint 5) as a FeatureCollection.
    """

    # Declared explicitly on purpose. Left to the automatic model-field
    # mapping, `geom` serialises to the EWKT *string*
    # "SRID=4326;MULTIPOLYGON (((...)))", which is not a GeoJSON geometry and
    # which Leaflet cannot read. `precision` also trims coordinates to ~1 m,
    # halving the FeatureCollection (6.8 MB -> 3.4 MB) with no visible change
    # at county scale.
    geom = GeometryField(precision=COORDINATE_PRECISION)

    class Meta:
        model = Region
        geo_field = "geom"
        fields = ["id", "county_name", "area_size", "geom"]


class GeospatialFeatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeospatialFeature
        fields = ["id", "region", "elevation", "slope", "distance_to_river"]


class EnvironmentalDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnvironmentalData
        fields = ["id", "region", "record_date", "rainfall_intensity"]
