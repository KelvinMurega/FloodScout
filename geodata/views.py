from rest_framework import viewsets

from .models import EnvironmentalData, GeospatialFeature, Region
from .serializers import (
    EnvironmentalDataSerializer,
    GeospatialFeatureSerializer,
    RegionSerializer,
)


class RegionViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only Region endpoint — returns GeoJSON Features for the
    Leaflet.js dashboard (Sprint 5, IR-01).
    """

    queryset = Region.objects.all()
    serializer_class = RegionSerializer


class _RegionFilterMixin:
    """Shared `?region=<id>` filtering for the two per-region datasets."""

    def get_queryset(self):
        qs = super().get_queryset()
        region_id = self.request.query_params.get("region")
        if region_id:
            qs = qs.filter(region_id=region_id)
        return qs


class GeospatialFeatureViewSet(_RegionFilterMixin, viewsets.ReadOnlyModelViewSet):
    queryset = GeospatialFeature.objects.select_related("region").all()
    serializer_class = GeospatialFeatureSerializer


class EnvironmentalDataViewSet(_RegionFilterMixin, viewsets.ReadOnlyModelViewSet):
    queryset = EnvironmentalData.objects.select_related("region").all()
    serializer_class = EnvironmentalDataSerializer
