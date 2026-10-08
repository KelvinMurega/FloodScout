import datetime as dt

from django.db.models import Max
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from accounts.permissions import IsAdministrator
from geodata.filters import filter_by_region_params
from geodata.models import Region

from .advisory import CATEGORY_RANK
from .inputs import score_inputs
from .ml import infer as scorer
from .models import RiskOutlook, RiskScore
from .scoring import NoRegionsError, score_regions, start_of_day
from .serializers import RiskOutlookSerializer, RiskScoreSerializer

# How close two regions' boundaries must come to count as neighbours, in
# degrees (~220 m). Not zero: the ADM2 boundaries do not always share
# vertices exactly, so strict touching would miss real neighbours.
NEIGHBOUR_TOLERANCE_DEG = 0.002


def date_param(params, name: str) -> dt.date | None:
    """`?<name>=YYYY-MM-DD` as a date, None when absent, HTTP 400 when malformed."""
    raw = params.get(name)
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError({name: "Expected YYYY-MM-DD."}) from exc


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

    `?date_after=` and `?date_before=` (YYYY-MM-DD, both inclusive) keep the
    scores for those days (FR-05). With `region` they give one region's
    history, newest first; on `latest-by-region/` the same day in both gives
    the map as it stood on that day. `dates/` lists the days scores exist for.
    """

    queryset = RiskScore.objects.select_related("region").all()
    serializer_class = RiskScoreSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        region_id = params.get("region")
        if region_id:
            if not region_id.isdigit():
                raise ValidationError({"region": "Expected a region id."})
            qs = qs.filter(region_id=int(region_id))

        after = date_param(params, "date_after")
        before = date_param(params, "date_before")
        if after and before and after > before:
            raise ValidationError({"date_after": "Must not be later than date_before."})
        # prediction_date is midnight local time on the day a score is for.
        if after:
            qs = qs.filter(prediction_date__gte=start_of_day(after))
        if before:
            qs = qs.filter(prediction_date__lt=start_of_day(before + dt.timedelta(days=1)))
        return filter_by_region_params(qs, params, prefix="region__")

    @action(detail=False, methods=["get"])
    def dates(self, request):
        """The days scores exist for, newest first, as YYYY-MM-DD strings.

        The dashboard's date picker offers these. Honours the same filters
        as the rest of the viewset.
        """
        days = (
            self.get_queryset()
            .annotate(day=TruncDate("prediction_date"))
            .order_by("-day")
            .values_list("day", flat=True)
            .distinct()
        )
        return Response([day.isoformat() for day in days])

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

    @action(detail=True, methods=["get"])
    def inputs(self, request, pk=None):
        """The model inputs behind one stored score (wireframe 4.8).

        Each input comes with what is usual for it where there is a usual,
        and whether the value is above, below or near that. They are worked
        out again from the stored rainfall with the model loaded now, so
        `same_model` says whether that is the model that wrote the score.
        A placeholder score has no model inputs and gets an empty list.
        """
        score = self.get_object()
        model = scorer._MODEL
        date = timezone.localtime(score.prediction_date).date()
        body = {
            "score": score.id,
            "region": score.region_id,
            "region_name": score.region.name,
            "county_name": score.region.county_name,
            "date": date.isoformat(),
            "model_version": score.model_version,
            "computed_with": model["version"] if model else None,
            "same_model": bool(model) and model["version"] == score.model_version,
            "inputs": score_inputs(model, score.region, date) if model else [],
        }
        return Response(body)

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
