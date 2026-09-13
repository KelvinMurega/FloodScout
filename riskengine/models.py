from django.conf import settings
from django.db import models

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
    prediction_date = models.DateTimeField(auto_now_add=True)
    predicted_value = models.FloatField(help_text="Raw model output score")
    risk_category = models.CharField(max_length=10, choices=RiskCategory.choices)

    class Meta:
        ordering = ["-prediction_date"]

    def __str__(self) -> str:
        return f"{self.region} — {self.risk_category} ({self.prediction_date:%Y-%m-%d})"
