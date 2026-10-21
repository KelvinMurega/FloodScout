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
    # The three below describe how readily water collects and how poorly it
    # drains. They are filled by `seed_drainage`, separately from elevation
    # and slope, and are null until it has run: an unknown is recorded as
    # unknown, never as a guess.
    distance_to_river = models.FloatField(
        help_text="Metres from the region's representative point to the nearest mapped river",
        null=True,
        blank=True,
    )
    flood_zone_fraction = models.FloatField(
        help_text="Share of the region inside the modelled 100-year river flood extent, 0-1",
        null=True,
        blank=True,
    )
    built_up_fraction = models.FloatField(
        help_text="Share of the region covered by buildings, 0-1",
        null=True,
        blank=True,
    )

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


class FloodEvent(models.Model):
    """A large flood recorded in Kenya by the Dartmouth Flood Observatory.

    The same archive the model's labels come from (geodata/services/dfo.py),
    kept here so the map can show where and when floods were recorded and
    the detail panel can say whether a place has flooded before. Filled by
    `seed_flood_events`.

    `footprint` is DFO's centroid and area turned into a circle: where the
    flood roughly was, not its outline. `centroid` is what the map draws.
    """

    dfo_id = models.PositiveIntegerField(unique=True, help_text="The event's ID in the archive")
    began = models.DateField()
    ended = models.DateField()
    centroid = gis_models.PointField(srid=4326)
    footprint = gis_models.PolygonField(srid=4326)
    area_km2 = models.FloatField(help_text="Affected area DFO recorded, km²")
    severity = models.FloatField(help_text="DFO class: 1 large, 1.5 very large, 2 extreme")
    dead = models.PositiveIntegerField(default=0)
    displaced = models.PositiveIntegerField(default=0)
    main_cause = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-began"]

    def __str__(self) -> str:
        return f"DFO {self.dfo_id} ({self.began:%Y-%m-%d})"


class GridCell(models.Model):
    """One square of a regular latitude/longitude grid laid over Kenya.

    A county is scored as one place, so the whole of it takes one colour
    even when the rain fell on one side of it. The grid is scored as many
    places: each cell has its own rainfall and terrain, read at its centre,
    so the map can show where inside a county the risk is. The cell belongs
    to the county its centre lies in, and borrows that county's normal
    rainfall and area, which the model needs and a cell does not have.

    Filled by `seed_grid`; scored by `score_grid` into GridRiskScore.
    """

    lon = models.FloatField(help_text="Longitude of the centre, degrees")
    lat = models.FloatField(help_text="Latitude of the centre, degrees")
    size_deg = models.FloatField(help_text="Width and height of the cell, degrees")
    county = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="grid_cells")
    elevation = models.FloatField(null=True, blank=True, help_text="Metres above sea level")
    slope = models.FloatField(null=True, blank=True, help_text="Degrees")

    class Meta:
        ordering = ["lat", "lon"]
        constraints = [
            models.UniqueConstraint(
                fields=["lon", "lat", "size_deg"], name="unique_gridcell_position"
            )
        ]

    def __str__(self) -> str:
        return f"Cell {self.lat:.2f}, {self.lon:.2f} ({self.county})"
