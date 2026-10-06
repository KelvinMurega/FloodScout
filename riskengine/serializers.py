from rest_framework import serializers

from .advisory import relocation_advisory
from .models import RiskOutlook, RiskScore


class RiskScoreSerializer(serializers.ModelSerializer):
    # `county_name` is the enclosing county at either level; `region_name` is
    # the scored region's own name (the county itself, or a sub-county).
    county_name = serializers.CharField(source="region.county_name", read_only=True)
    region_name = serializers.CharField(source="region.name", read_only=True)
    region_level = serializers.CharField(source="region.level", read_only=True)
    relocation_advisory = serializers.SerializerMethodField()

    class Meta:
        model = RiskScore
        fields = [
            "id",
            "region",
            "region_name",
            "region_level",
            "county_name",
            "prediction_date",
            "predicted_value",
            "risk_category",
            "relocation_advisory",
            # Always exposed: an API consumer must be able to tell a placeholder
            # score from a trained-model one. See riskengine/ml/infer.py.
            "model_version",
        ]

    def get_relocation_advisory(self, score) -> str:
        return relocation_advisory(score.risk_category)


class RiskOutlookSerializer(serializers.ModelSerializer):
    """Shaped like RiskScoreSerializer so the dashboard draws either the same
    way. There is no relocation advisory: a months-ahead probability is not
    grounds to tell anyone to move.
    """

    county_name = serializers.CharField(source="region.county_name", read_only=True)
    region_name = serializers.CharField(source="region.name", read_only=True)
    region_level = serializers.CharField(source="region.level", read_only=True)
    predicted_value = serializers.FloatField(source="flood_probability", read_only=True)

    class Meta:
        model = RiskOutlook
        fields = [
            "id",
            "region",
            "region_name",
            "region_level",
            "county_name",
            "month",
            "predicted_value",
            "probability_low",
            "probability_high",
            "rain_vs_normal",
            "risk_category",
            "model_version",
            "issued_on",
        ]
