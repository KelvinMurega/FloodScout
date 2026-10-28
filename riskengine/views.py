import datetime as dt

from django.core.cache import cache
from django.db.models import Max
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from accounts.activity import record
from accounts.models import ActivityLog
from accounts.permissions import CanSeeAnalytics, IsAdministrator
from geodata import focus_regions
from geodata.filters import filter_by_region_params
from geodata.models import Region
from geodata.services.climate_indices import oni_for

from .advisory import CATEGORY_RANK
from .analytics import satellite_check, system_health
from .briefing import briefing
from .context import CHANGE_DAYS, map_context
from .forecast import latest_issue
from .inputs import score_inputs
from .ml import infer as scorer
from .models import GridRiskScore, RiskForecast, RiskOutlook, RiskScore
from .performance import load_performance
from .scoring import NoRegionsError, score_regions, start_of_day
from .season import times_season
from .serializers import RiskForecastSerializer, RiskOutlookSerializer, RiskScoreSerializer
from .situation import newest_places
from .why import explain_score

# `inputs/` and `explain/` work a score out again from the stored rainfall,
# which costs a model run each. Kept a day; the key changes when the model
# or the score does (a rescore after new rainfall).
WORKED_OUT_SECONDS = 24 * 60 * 60


def _worked_out(kind: str, score, model, build):
    version = model["version"] if model else "none"
    key = f"score-{kind}:{score.id}:{version}:{score.predicted_value!r}"
    found = cache.get(key)
    if found is None:
        found = build()
        cache.set(key, found, WORKED_OUT_SECONDS)
    return found

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

    def paginate_queryset(self, queryset):
        # One region's history (the dashboard's chart) comes whole, a row a
        # day; the list of every score is paged (api/pagination.py).
        if self.request.query_params.get("region"):
            return None
        return super().paginate_queryset(queryset)

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

    @action(detail=False, methods=["get"])
    def context(self, request):
        """What the map's themes colour by, for every region on one day.

        `?date=YYYY-MM-DD`, default the newest day scored, plus the region
        filters of the rest of the viewset. One row per region: the score
        as times the usual chance, its change over the week before, and the
        30-day rain against the county's normal (riskengine/context.py).
        """
        qs = self.get_queryset()
        day = date_param(request.query_params, "date")
        if day is None:
            newest = qs.aggregate(newest=Max("prediction_date"))["newest"]
            if newest is None:
                return Response({"date": None, "regions": []})
            day = timezone.localtime(newest).date()

        def on(date):
            return qs.filter(
                prediction_date__gte=start_of_day(date),
                prediction_date__lt=start_of_day(date + dt.timedelta(days=1)),
            )

        scores = list(on(day))
        earlier = on(day - dt.timedelta(days=CHANGE_DAYS)).filter(
            region_id__in=[score.region_id for score in scores]
        )
        return Response(
            {
                "date": day.isoformat(),
                "change_days": CHANGE_DAYS,
                "regions": map_context(scores, earlier, day),
            }
        )

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
            "inputs": _worked_out(
                "inputs", score, model, lambda: score_inputs(model, score.region, date)
            )
            if model
            else [],
        }
        return Response(body)

    @action(detail=True, methods=["get"])
    def explain(self, request, pk=None):
        """Why the model gave this score: each group of inputs' share of it.

        An exact split of the Random Forest's probability (riskengine/ml/
        explain.py), worked out again from the stored rainfall with the
        model loaded now, like `inputs/`. A placeholder score has no model
        to explain and gets 404.
        """
        score = self.get_object()
        model = scorer._MODEL
        if model is None:
            return Response(
                {"detail": "This score came from the placeholder rule, which has no model."},
                status=status.HTTP_404_NOT_FOUND,
            )
        date = timezone.localtime(score.prediction_date).date()
        body = {
            "score": score.id,
            "region": score.region_id,
            "date": date.isoformat(),
            "model_version": score.model_version,
            "computed_with": model["version"],
            "same_model": model["version"] == score.model_version,
            **_worked_out("explain", score, model, lambda: explain_score(model, score.region, date)),
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
        record(
            request,
            ActivityLog.Action.RESCORE,
            date=result.date.isoformat(),
            regions=result.regions,
        )
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


@api_view(["GET"])
def situation_briefing(request):
    """Today's situation in words, and the places that stand out.

    `?level=county` (the default) or `sub_county`, and `?focus_region=<slug>`,
    as on the other endpoints. Built from the newest day's scores, their
    change over the week, the rain against normal, the newest 7-day forecast
    and the El Niño index (riskengine/briefing.py).
    """
    params = request.query_params.copy()
    params.setdefault("level", Region.Level.COUNTY)
    scores_qs = filter_by_region_params(
        RiskScore.objects.select_related("region"), params, prefix="region__"
    )
    focus = focus_regions.get(params.get("focus_region") or "")
    area = f"the {focus.name} region" if focus else "Kenya"
    unit = "county" if params["level"] == Region.Level.COUNTY else "sub-county"
    day, places, issued = newest_places(scores_qs)
    if day is None:
        return Response(briefing([], date=timezone.localdate(), area=area, unit=unit, oni=None))

    model = scorer._MODEL
    oni = oni_for(day, model["oni"]) if model else None
    body = briefing(places, date=day, area=area, unit=unit, oni=oni)
    body["forecast_issued_on"] = issued.isoformat() if issued else None
    return Response(body)


@api_view(["GET"])
@permission_classes([CanSeeAnalytics])
def admin_analytics(request):
    """Everything the administrator's analytics page shows, in one response.

    `model`: how the loaded model did when it was evaluated, from the
    metrics.json its training run wrote (null while the placeholder rule
    is scoring, which was never evaluated). `satellite_check`: its scores
    against floodwater UNOSAT mapped from satellite images. `health`: whether
    the pipeline is keeping the map current, worked out from the database now.
    """
    return Response(
        {
            "model": load_performance() if scorer._MODEL else None,
            "satellite_check": satellite_check() if scorer._MODEL else None,
            "health": system_health(),
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
    # One month's outlook: one row per region.
    pagination_class = None

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


class RiskGridViewSet(viewsets.ViewSet):
    """Read-only flood risk per grid cell (see riskengine/grid.py).

    `list` gives every cell's score for one day: `?date=YYYY-MM-DD`, or the
    latest day with grid scores. `?county=<id>` (repeatable) keeps the cells
    of those counties. `dates/` lists the days grid scores exist for.

    Cells are sent as plain objects rather than GeoJSON: a square is fully
    described by its centre and the one `cell_deg` for the whole grid, and
    hundreds of polygons would be several times the size.
    """

    def list(self, request):
        params = request.query_params
        day = date_param(params, "date")
        if day is None:
            day = GridRiskScore.objects.aggregate(day=Max("date"))["day"]
        scores = GridRiskScore.objects.filter(date=day).select_related("cell__county")
        counties = params.getlist("county")
        if counties:
            if not all(county.isdigit() for county in counties):
                raise ValidationError({"county": "Expected county ids."})
            scores = scores.filter(cell__county_id__in=[int(c) for c in counties])
        scores = list(scores)
        return Response(
            {
                "date": day.isoformat() if day else None,
                "cell_deg": scores[0].cell.size_deg if scores else None,
                "model_version": scores[0].model_version if scores else None,
                "cells": [
                    {
                        "lon": score.cell.lon,
                        "lat": score.cell.lat,
                        "county": score.cell.county_id,
                        "county_name": score.cell.county.county_name,
                        "predicted_value": score.predicted_value,
                        "times_usual": scorer.times_usual(score.predicted_value),
                        "times_season": times_season(
                            score.predicted_value, score.cell.county.county_name, score.date.month
                        ),
                        "risk_category": score.risk_category,
                        "rain_30d": score.rain_30d,
                        "rain_30d_ratio": score.rain_30d_ratio,
                    }
                    for score in scores
                ],
            }
        )

    @action(detail=False, methods=["get"])
    def dates(self, request):
        days = GridRiskScore.objects.order_by("-date").values_list("date", flat=True).distinct()
        return Response([day.isoformat() for day in days])


class RiskForecastViewSet(viewsets.ReadOnlyModelViewSet):
    """The flood risk forecast for the coming days (riskengine/forecast.py).

    Read-only. From the newest issue unless `?issued_on=YYYY-MM-DD` names
    another. `?target_date=YYYY-MM-DD` keeps one day, and the region filters
    of the other endpoints apply, so `?region=<id>` gives one region's week.
    `days/` lists the newest issue and the days it covers.
    """

    serializer_class = RiskForecastSerializer
    # One issue: seven days for each region at most.
    pagination_class = None

    def get_queryset(self):
        params = self.request.query_params
        issued = date_param(params, "issued_on") or latest_issue()
        qs = RiskForecast.objects.select_related("region").filter(issued_on=issued)
        target = date_param(params, "target_date")
        if target:
            qs = qs.filter(target_date=target)
        region_id = params.get("region")
        if region_id:
            if not region_id.isdigit():
                raise ValidationError({"region": "Expected a region id."})
            qs = qs.filter(region_id=int(region_id))
        return filter_by_region_params(qs, params, prefix="region__")

    @action(detail=False, methods=["get"])
    def days(self, request):
        issued = latest_issue()
        days = (
            RiskForecast.objects.filter(issued_on=issued)
            .order_by("target_date")
            .values_list("target_date", flat=True)
            .distinct()
        )
        return Response(
            {
                "issued_on": issued.isoformat() if issued else None,
                "days": [day.isoformat() for day in days],
            }
        )
