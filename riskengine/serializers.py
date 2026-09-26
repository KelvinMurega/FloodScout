from rest_framework import serializers

from .advisory import relocation_advisory
from .models import RiskScore


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
