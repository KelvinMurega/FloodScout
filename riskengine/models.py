from django.conf import settings
from django.db import models
from django.utils import timezone

from geodata.models import Region


class RiskScore(models.Model):
    """RISK_SCORE entity from the ERD (Chapter 4, Section 4.4.1) — the output
    of FR-03 (risk prediction) and FR-04 (risk classification).
    """

    class RiskCategory(models.TextChoices):
        LOW = "low", "Low"
        MODERATE = "moderate", "Moderate"
        HIGH = "high", "High"
        VERY_HIGH = "very_high", "Very High"

    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="risk_scores")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="risk_scores"
    )
    # The date this score is *for*, not the moment the row was written.
    # `score_regions` pins it to midnight local time so that re-running for
    # the same date updates the day's rows instead of appending a second set.
    # (Was auto_now_add, which cannot be set and so made --date unrecordable.)
    prediction_date = models.DateTimeField(default=timezone.now)
    predicted_value = models.FloatField(help_text="Raw model output score")
    risk_category = models.CharField(max_length=10, choices=RiskCategory.choices)
    model_version = models.CharField(
        max_length=50,
        default="heuristic-stub-v0",
        help_text=(
            "Identifier of the scorer that produced this row. Placeholder "
            "heuristics and trained models must never be indistinguishable — "
            "see riskengine/ml/infer.py."
        ),
    )

    class Meta:
        ordering = ["-prediction_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["region", "prediction_date"],
                name="unique_riskscore_region_prediction_date",
            )
        ]

    def __str__(self) -> str:
        return f"{self.region} — {self.risk_category} ({self.prediction_date:%Y-%m-%d})"


class RiskOutlook(models.Model):
    """A forecast-driven flood probability for a region for a month ahead.

    Written by `flood_outlook` from a seasonal rainfall forecast. Kept apart
    from RiskScore on purpose: a RiskScore is computed from rain that has
    fallen, an outlook from rain that is only predicted, and the dashboard's
    "latest score" must never quietly be the second kind.
    """

    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="risk_outlooks")
    month = models.DateField(help_text="First day of the month the outlook is for")
    flood_probability = models.FloatField(help_text="Mean over the forecast ensemble")
    probability_low = models.FloatField(help_text="10th percentile of the ensemble")
    probability_high = models.FloatField(help_text="90th percentile of the ensemble")
    rain_vs_normal = models.FloatField(
        null=True, blank=True, help_text="Median forecast rainfall over the county's normal"
    )
    risk_category = models.CharField(max_length=10, choices=RiskScore.RiskCategory.choices)
    model_version = models.CharField(max_length=50)
    issued_on = models.DateField(help_text="The day the forecast was fetched")

    class Meta:
        ordering = ["month", "region_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["region", "month"], name="unique_riskoutlook_region_month"
            )
        ]

    def __str__(self) -> str:
        return f"{self.region} — {self.risk_category} ({self.month:%Y-%m} outlook)"
