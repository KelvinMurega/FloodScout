from django.conf import settings
from django.db import models
from django.utils import timezone

from geodata.models import GridCell, Region


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


class GridRiskScore(models.Model):
    """The flood probability for one grid cell on one day.

    Worked out by the same model as RiskScore, from the cell's own rainfall
    and terrain (see geodata.GridCell). The model learned from floods
    recorded per county, so a cell's value reads as "a county that got this
    cell's rain would have this chance of a large flood": it shows where in
    a county the rain that drives the risk fell, not a flood extent.
    """

    cell = models.ForeignKey(GridCell, on_delete=models.CASCADE, related_name="risk_scores")
    date = models.DateField(help_text="The day the score is for")
    predicted_value = models.FloatField(help_text="Raw model output score")
    risk_category = models.CharField(max_length=10, choices=RiskScore.RiskCategory.choices)
    rain_30d = models.FloatField(null=True, blank=True, help_text="mm in the 30 days to the date")
    rain_30d_ratio = models.FloatField(
        null=True, blank=True, help_text="rain_30d over the county's normal for the month"
    )
    model_version = models.CharField(max_length=50)

    class Meta:
        ordering = ["-date", "cell_id"]
        constraints = [
            models.UniqueConstraint(fields=["cell", "date"], name="unique_gridriskscore_cell_date")
        ]

    def __str__(self) -> str:
        return f"{self.cell} — {self.risk_category} ({self.date:%Y-%m-%d})"


class RiskForecast(models.Model):
    """The flood probability for a region on a day in the coming week.

    Written by `forecast_scores`: the same trained model as RiskScore, given
    the rainfall that has fallen up to the day it was issued and the
    rainfall *forecast* for the days after. Kept apart from RiskScore for the
    reason RiskOutlook is: a score from rain that fell and a score from rain
    that is only predicted must never be mistaken for one another.

    Each run issues seven days ahead and keeps earlier issues, so a forecast
    can later be checked against the score the day actually got.
    """

    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="risk_forecasts")
    issued_on = models.DateField(help_text="The day the forecast was made")
    target_date = models.DateField(help_text="The day the forecast is for")
    predicted_value = models.FloatField(help_text="Model output, as RiskScore.predicted_value")
    risk_category = models.CharField(max_length=10, choices=RiskScore.RiskCategory.choices)
    rain_forecast_mm = models.FloatField(
        null=True, blank=True, help_text="Forecast rain from the day after issue to target_date, mm"
    )
    rain_forecast_max_24h = models.FloatField(
        null=True, blank=True, help_text="Wettest forecast day in that span, mm"
    )
    model_version = models.CharField(max_length=50)

    class Meta:
        ordering = ["target_date", "region_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["region", "issued_on", "target_date"],
                name="unique_riskforecast_region_issue_target",
            )
        ]
        indexes = [models.Index(fields=["issued_on", "target_date"])]

    def __str__(self) -> str:
        return f"{self.region} — {self.risk_category} on {self.target_date} (issued {self.issued_on})"
