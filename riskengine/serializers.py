from django.utils import timezone
from rest_framework import serializers

from .advisory import relocation_advisory
from .ml.infer import times_usual
from .models import RiskForecast, RiskOutlook, RiskScore
from .season import times_season


class RiskScoreSerializer(serializers.ModelSerializer):
    # `county_name` is the enclosing county at either level; `region_name` is
    # the scored region's own name (the county itself, or a sub-county).
    county_name = serializers.CharField(source="region.county_name", read_only=True)
    region_name = serializers.CharField(source="region.name", read_only=True)
    region_level = serializers.CharField(source="region.level", read_only=True)
    relocation_advisory = serializers.SerializerMethodField()
    times_usual = serializers.SerializerMethodField()
    times_season = serializers.SerializerMethodField()

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
            # The same value as a multiple of the usual chance of a flood.
            # null for a placeholder score. See riskengine/ml/infer.py.
            "times_usual",
            # Against an ordinary year for the county and month (riskengine/season.py).
            "times_season",
            "risk_category",
            "relocation_advisory",
            # Always exposed: an API consumer must be able to tell a placeholder
            # score from a trained-model one. See riskengine/ml/infer.py.
            "model_version",
        ]

    def get_relocation_advisory(self, score) -> str:
        return relocation_advisory(score.risk_category)

    def get_times_usual(self, score) -> float | None:
        return times_usual(score.predicted_value)

    def get_times_season(self, score) -> float | None:
        return times_season(
            score.predicted_value, score.region.county_name, timezone.localtime(score.prediction_date).month
        )


class RiskOutlookSerializer(serializers.ModelSerializer):
    """Shaped like RiskScoreSerializer so the dashboard draws either the same
    way. There is no relocation advisory: a months-ahead probability is not
    grounds to tell anyone to move.
    """

    county_name = serializers.CharField(source="region.county_name", read_only=True)
    region_name = serializers.CharField(source="region.name", read_only=True)
    region_level = serializers.CharField(source="region.level", read_only=True)
    predicted_value = serializers.FloatField(source="flood_probability", read_only=True)
    times_usual = serializers.SerializerMethodField()
    times_usual_low = serializers.SerializerMethodField()
    times_usual_high = serializers.SerializerMethodField()

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
            "times_usual",
            "probability_low",
            "probability_high",
            "times_usual_low",
            "times_usual_high",
            "rain_vs_normal",
            "risk_category",
            "model_version",
            "issued_on",
        ]

    def get_times_usual(self, outlook) -> float | None:
        return times_usual(outlook.flood_probability)

    def get_times_usual_low(self, outlook) -> float | None:
        return times_usual(outlook.probability_low)

    def get_times_usual_high(self, outlook) -> float | None:
        return times_usual(outlook.probability_high)


class RiskForecastSerializer(serializers.ModelSerializer):
    """Shaped like RiskScoreSerializer, so the dashboard draws a forecast day
    the way it draws today. There is no relocation advisory: a forecast is
    grounds to prepare, which the alerts say, not to move people.
    """

    county_name = serializers.CharField(source="region.county_name", read_only=True)
    region_name = serializers.CharField(source="region.name", read_only=True)
    region_level = serializers.CharField(source="region.level", read_only=True)
    times_usual = serializers.SerializerMethodField()
    times_season = serializers.SerializerMethodField()

    class Meta:
        model = RiskForecast
        fields = [
            "id",
            "region",
            "region_name",
            "region_level",
            "county_name",
            "issued_on",
            "target_date",
            "predicted_value",
            "times_usual",
            "times_season",
            "risk_category",
            "rain_forecast_mm",
            "rain_forecast_max_24h",
            "model_version",
        ]

    def get_times_usual(self, forecast) -> float | None:
        return times_usual(forecast.predicted_value)

    def get_times_season(self, forecast) -> float | None:
        return times_season(
            forecast.predicted_value, forecast.region.county_name, forecast.target_date.month
        )
