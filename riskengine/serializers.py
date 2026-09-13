from rest_framework import serializers

from .models import RiskScore


class RiskScoreSerializer(serializers.ModelSerializer):
    county_name = serializers.CharField(source="region.county_name", read_only=True)

    class Meta:
        model = RiskScore
        fields = [
            "id",
            "region",
            "county_name",
            "prediction_date",
            "predicted_value",
            "risk_category",
        ]
