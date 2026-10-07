import datetime as dt

from django.db.models import Max
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from accounts.permissions import IsAdministrator
from geodata.filters import filter_by_region_params
from geodata.models import Region

from .advisory import CATEGORY_RANK
from .models import RiskOutlook, RiskScore
from .scoring import NoRegionsError, score_regions
from .serializers import RiskOutlookSerializer, RiskScoreSerializer

# How close two regions' boundaries must come to count as neighbours, in
# degrees (~220 m). Not zero: the ADM2 boundaries do not always share
# vertices exactly, so strict touching would miss real neighbours.
NEIGHBOUR_TOLERANCE_DEG = 0.002


def latest_per_region(queryset):
    """The newest score for each region in `queryset`, via DISTINCT ON.

    DISTINCT ON requires region_id to lead the ordering; the rest picks the
    newest row within each region. PostgreSQL-only, which is fine — the Data
    Tier is PostGIS (Chapter 4, Section 4.4.2).
    """
    return queryset.order_by("region_id", "-prediction_date", "-id").distinct("region_id")


class RiskScoreViewSet(viewsets.ReadOnlyModelViewSet):
    """RiskScore endpoint (FR-03/FR-04, IR-01). Read-only, except `rescore/`,
    which only a System Administrator may call.

    Supports `?region=<id>` filtering on the list endpoint, and a
    `latest/?region=<id>` action for "what's the current risk here"
    lookups — the case the Sprint 5 dashboard actually needs most often.
    `?level=`, `?parent=` and `?focus_region=` filter by the scored region
    (see geodata/filters.py).
    """

    queryset = RiskScore.objects.select_related("region").all()
    serializer_class = RiskScoreSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        region_id = self.request.query_params.get("region")
        if region_id:
            qs = qs.filter(region_id=region_id)
        return filter_by_region_params(qs, self.request.query_params, prefix="region__")

    @action(detail=False, methods=["get"])
    def latest(self, request):
        region_id = request.query_params.get("region")
        qs = self.get_queryset()
        if region_id:
            qs = qs.filter(region_id=region_id)
        # RiskScore.Meta.ordering is ["-prediction_date"], so .first() is latest.
        instance = qs.first()
        if instance is None:
            return Response({"detail": "No risk score found."}, status=404)
        return Response(self.get_serializer(instance).data)

    @action(detail=False, methods=["get"], url_path="latest-by-region")
    def latest_by_region(self, request):
        """The latest score for every region, in one response.

        The Sprint 5 dashboard colours all 47 counties at once. Doing that
        through `latest/?region=<id>` costs one request per county; this
        costs one request and, via DISTINCT ON, one query. Honours the same
        filters as the rest of the viewset.
        """
        # Sorted for the map legend; cannot be done in SQL alongside DISTINCT ON.
        scores = sorted(
            latest_per_region(self.get_queryset()),
            key=lambda score: (score.region.county_name, score.region.level, score.region.name),
        )
        return Response(self.get_serializer(scores, many=True).data)

    @action(detail=False, methods=["get"], url_path="lower-risk-neighbours")
    def lower_risk_neighbours(self, request):
        """Neighbouring regions at the same level whose latest score is lower
        than `?region=<id>`'s, least risky first.

        Answers "where nearby is safer" for the dashboard's relocation panel.
        The neighbours may lie in another county or focus region.
        """
        region_id = request.query_params.get("region")
        if not region_id or not region_id.isdigit():
            raise ValidationError({"region": "Expected a region id."})
        region = get_object_or_404(Region, pk=int(region_id))

        own = RiskScore.objects.filter(region=region).first()
        if own is None:
            return Response({"detail": "No risk score found."}, status=404)

        neighbours = (
            Region.objects.filter(
                level=region.level,
                geom__dwithin=(region.geom, NEIGHBOUR_TOLERANCE_DEG),
            )
            .exclude(pk=region.pk)
        )
        candidates = [
            score
            for score in latest_per_region(
                RiskScore.objects.select_related("region").filter(region__in=neighbours)
            )
            if CATEGORY_RANK[score.risk_category] < CATEGORY_RANK[own.risk_category]
        ]
        candidates.sort(key=lambda score: (score.predicted_value, score.region.name))
        return Response(self.get_serializer(candidates, many=True).data)

    @action(detail=False, methods=["post"], permission_classes=[IsAdministrator])
    def rescore(self, request):
        """Score every region again, as `manage.py score_regions` does.

        Administrator only. `{"date": "YYYY-MM-DD"}` in the body picks the
        date; the default is today. It scores from the rainfall already
        stored and does not fetch new rainfall (`refresh_scores` does both).
        """
        raw_date = request.data.get("date")
        target_date = None
        if raw_date:
            try:
                target_date = dt.date.fromisoformat(str(raw_date))
            except ValueError as exc:
                raise ValidationError({"date": "Expected YYYY-MM-DD."}) from exc
        try:
            result = score_regions(target_date)
        except NoRegionsError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(
            {
                "date": result.date.isoformat(),
                "model_version": result.model_version,
                "regions": result.regions,
                "created": result.created,
                "updated": result.updated,
                "categories": dict(result.categories),
            }
        )


class RiskOutlookViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only seasonal outlook: one row per region per month ahead.

    `?month=YYYY-MM-DD` (the first of the month) picks one month, and the
    region filters of the other endpoints apply. `months/` lists the months
    an outlook exists for.
    """

    queryset = RiskOutlook.objects.select_related("region").all()
    serializer_class = RiskOutlookSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        month = self.request.query_params.get("month")
        if month:
            try:
                qs = qs.filter(month=dt.date.fromisoformat(month))
            except ValueError as exc:
                raise ValidationError({"month": "Expected YYYY-MM-DD."}) from exc
        return filter_by_region_params(qs, self.request.query_params, prefix="region__")

    @action(detail=False, methods=["get"])
    def months(self, request):
        rows = (
            RiskOutlook.objects.values("month")
            .annotate(issued_on=Max("issued_on"))
            .order_by("month")
        )
        return Response(list(rows))
