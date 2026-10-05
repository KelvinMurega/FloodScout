from django.contrib.gis.db import models as gis_models
from django.db import models


class Region(models.Model):
    """REGION entity from the ERD (Chapter 4, Section 4.4.1).

    A Region is either one of the 47 counties (ADM1) or one of the 290
    sub-counties (ADM2) inside them. Both levels live in this one table on
    purpose: GeospatialFeature, EnvironmentalData and RiskScore all hang off
    Region, so a sub-county gets its own terrain, rainfall and risk score
    through exactly the same ingest and scoring code as a county.

    `name` is the region's own name; `county_name` is always the county it
    lies in. For a county the two are the same.
    """

    class Level(models.TextChoices):
        COUNTY = "county", "County"
        SUB_COUNTY = "sub_county", "Sub-county"

    name = models.CharField(max_length=100)
    county_name = models.CharField(max_length=100)
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.COUNTY)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sub_counties",
        help_text="The county a sub-county lies in. Empty for counties.",
    )
    geom = gis_models.MultiPolygonField(srid=4326)
    area_size = models.FloatField(help_text="Area in square kilometres")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["level", "name"], name="unique_region_level_name"),
        ]

    def save(self, *args, **kwargs):
        # A county is its own county, so callers that only know `county_name`
        # (everything written before sub-counties existed) keep working.
        if not self.name and self.level == self.Level.COUNTY:
            self.name = self.county_name
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        if self.level == self.Level.SUB_COUNTY:
            return f"{self.name} ({self.county_name})"
        return self.name


class GeospatialFeature(models.Model):
    """GEOSPATIAL_FEATURE entity — static per-region inputs (elevation,
    slope, distance to river) required by DR-01 (Chapter 4, Section 4.2.3).
    """

    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="geospatial_features")
    elevation = models.FloatField(help_text="Metres above sea level")
    slope = models.FloatField(help_text="Degrees")
    # Nullable because there is no free, keyless source for it. Elevation
    # and slope are ingested from a real DEM (see
    # geodata/services/open_meteo.py); distance to the nearest watercourse
    # needs a hydrography layer that is not wired up yet, and inventing a
    # number would be worse than recording that it is unknown.
    distance_to_river = models.FloatField(help_text="Metres", null=True, blank=True)

    class Meta:
        # These inputs are static per county, so one row per Region is the
        # whole truth. The constraint is what lets `seed_terrain` use
        # update_or_create and be safely re-runnable.
        constraints = [
            models.UniqueConstraint(
                fields=["region"], name="unique_geospatialfeature_per_region"
            )
        ]

    def __str__(self) -> str:
        return f"Geospatial features for {self.region}"


class EnvironmentalData(models.Model):
    """ENVIRONMENTAL_DATA entity — time-varying inputs (e.g. rainfall) per
    region and date, required by DR-01/DR-02 (Chapter 4, Section 4.2.3).
    """

    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="environmental_records")
    record_date = models.DateField()
    rainfall_intensity = models.FloatField(help_text="mm")

    class Meta:
        indexes = [models.Index(fields=["region", "record_date"])]
        ordering = ["-record_date"]

    def __str__(self) -> str:
        return f"{self.region} — {self.record_date}"
