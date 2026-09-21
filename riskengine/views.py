from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import RiskScore
from .serializers import RiskScoreSerializer


class RiskScoreViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only RiskScore endpoint (FR-03/FR-04, IR-01).

    Supports `?region=<id>` filtering on the list endpoint, and a
    `latest/?region=<id>` action for "what's the current risk here"
    lookups — the case the Sprint 5 dashboard actually needs most often.
    """

    queryset = RiskScore.objects.select_related("region").all()
    serializer_class = RiskScoreSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        region_id = self.request.query_params.get("region")
        if region_id:
            qs = qs.filter(region_id=region_id)
        return qs

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
