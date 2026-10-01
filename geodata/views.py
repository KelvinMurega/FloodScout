from rest_framework import viewsets
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from . import focus_regions
from .filters import filter_by_region_params
from .models import EnvironmentalData, GeospatialFeature, Region
from .serializers import (
    EnvironmentalDataSerializer,
    GeospatialFeatureSerializer,
    RegionSerializer,
)


class RegionViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only Region endpoint — returns GeoJSON Features for the
    Leaflet.js dashboard (Sprint 5, IR-01).

    The list defaults to the 47 counties, as it did before sub-counties
    existed; `?level=sub_county`, `?parent=<id>` and `?focus_region=<slug>`
    narrow or widen it (see geodata/filters.py). Detail lookups are never
    filtered, so any region is reachable by id.
    """

    queryset = Region.objects.order_by("county_name", "level", "name")
    serializer_class = RegionSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "list":
            qs = filter_by_region_params(
                qs, self.request.query_params, default_level=Region.Level.COUNTY
            )
        return qs


class FocusRegionViewSet(viewsets.ViewSet):
    """The flood-prone regions the dashboard can drill into.

    Static, editorial groupings of counties (geodata/focus_regions.py) with
    each county resolved to its Region id, so the client can pick them out
    of the county layer it already holds.
    """

    lookup_field = "slug"

    def _serialize(self, focus, county_ids):
        return {
            "slug": focus.slug,
            "name": focus.name,
            "hazards": focus.hazards,
            "counties": [
                {"id": county_ids[name], "name": name}
                for name in focus.counties
                if name in county_ids
            ],
        }

    def _county_ids(self):
        return dict(
            Region.objects.filter(level=Region.Level.COUNTY).values_list("name", "id")
        )

    def list(self, request):
        county_ids = self._county_ids()
        return Response(
            [self._serialize(focus, county_ids) for focus in focus_regions.FOCUS_REGIONS]
        )

    def retrieve(self, request, slug=None):
        focus = focus_regions.get(slug)
        if focus is None:
            raise NotFound(f"No focus region {slug!r}.")
        return Response(self._serialize(focus, self._county_ids()))


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
