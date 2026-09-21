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
