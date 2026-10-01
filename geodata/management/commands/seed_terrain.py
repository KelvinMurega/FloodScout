"""Populate geodata.GeospatialFeature with real elevation and slope.

One GeospatialFeature per Region, measured at a representative point inside
the county. Elevation comes straight from the DEM; slope is computed here
from four elevation samples around that point, so both numbers trace back to
measurements rather than to an assumption.

The drainage fields (`distance_to_river` and the two fractions) are not
touched here; `seed_drainage` fills them.
"""

from __future__ import annotations

import math

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from geodata.models import GeospatialFeature, Region
from geodata.services.open_meteo import OpenMeteoError, Point, fetch_elevations

# Sample offset for the slope calculation. 0.01 degrees is ~1.1 km, which is
# a bit over ten cells of the 90 m DEM — wide enough not to be dominated by
# DEM noise, tight enough to describe local terrain rather than the county.
SLOPE_OFFSET_DEG = 0.01

# Metres per degree of latitude. Longitude shrinks by cos(latitude).
METRES_PER_DEGREE = 111_320.0


def representative_point(region: Region) -> Point:
    """A point guaranteed to lie inside the county.

    `point_on_surface`, not `centroid`: the centroid of a horseshoe-shaped
    or island-bearing county can fall outside it, which would sample the
    wrong terrain entirely.
    """
    geom = region.geom.point_on_surface
    return Point(lon=geom.x, lat=geom.y)


def slope_degrees(centre: float, north: float, south: float, east: float, west: float, lat: float) -> float:
    """Terrain slope in degrees from a four-point elevation cross.

    The centre sample is not used in the gradient itself — it is the two
    opposing pairs that give the north-south and east-west rise over run —
    but it is what the row records as the county's elevation.
    """
    rise_ns = north - south
    run_ns = 2 * SLOPE_OFFSET_DEG * METRES_PER_DEGREE

    rise_ew = east - west
    run_ew = 2 * SLOPE_OFFSET_DEG * METRES_PER_DEGREE * math.cos(math.radians(lat))

    if run_ew == 0:
        return 0.0

    gradient = math.hypot(rise_ns / run_ns, rise_ew / run_ew)
    return math.degrees(math.atan(gradient))


class Command(BaseCommand):
    help = "Ingest real elevation and slope per Region from the Open-Meteo DEM."

    def add_arguments(self, parser):
        parser.add_argument(
            "--county",
            type=str,
            default=None,
            help="Only this county and its sub-counties (case-insensitive). Defaults to all.",
        )
        parser.add_argument(
            "--level",
            choices=[level.value for level in Region.Level],
            default=None,
            help="Only counties or only sub-counties. Defaults to both.",
        )

    def handle(self, *args, **options):
        regions = Region.objects.order_by("county_name", "level", "name")
        if options["level"]:
            regions = regions.filter(level=options["level"])
        regions = list(regions)
        if options["county"]:
            wanted = options["county"].strip().casefold()
            regions = [r for r in regions if r.county_name.casefold() == wanted]
            if not regions:
                raise CommandError(f"No county named {options['county']!r}.")
        if not regions:
            raise CommandError("No regions in the database — run `seed_regions` first.")

        self.stdout.write(f"Sampling terrain for {len(regions)} regions")

        # Five samples per county: centre, then N/S/E/W for the gradient.
        points: list[Point] = []
        for region in regions:
            centre = representative_point(region)
            points.extend(
                [
                    centre,
                    Point(centre.lon, centre.lat + SLOPE_OFFSET_DEG),
                    Point(centre.lon, centre.lat - SLOPE_OFFSET_DEG),
                    Point(centre.lon + SLOPE_OFFSET_DEG, centre.lat),
                    Point(centre.lon - SLOPE_OFFSET_DEG, centre.lat),
                ]
            )

        try:
            elevations = fetch_elevations(points)
        except OpenMeteoError as exc:
            raise CommandError(str(exc)) from exc

        created_count = 0
        updated_count = 0
        rows: list[tuple[str, float, float]] = []

        with transaction.atomic():
            for index, region in enumerate(regions):
                centre, north, south, east, west = elevations[index * 5 : index * 5 + 5]
                lat = points[index * 5].lat
                slope = slope_degrees(centre, north, south, east, west, lat)

                _feature, created = GeospatialFeature.objects.update_or_create(
                    region=region,
                    defaults={"elevation": centre, "slope": round(slope, 3)},
                )
                rows.append((str(region), centre, slope))
                if created:
                    created_count += 1
                else:
                    updated_count += 1

        self._report(created_count, updated_count, rows)

    def _report(self, created_count, updated_count, rows):
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Created: {created_count}"))
        self.stdout.write(self.style.SUCCESS(f"Updated: {updated_count}"))
        self.stdout.write("")

        lowest = min(rows, key=lambda r: r[1])
        highest = max(rows, key=lambda r: r[1])
        self.stdout.write(f"Lowest : {lowest[0]} at {lowest[1]:.0f} m")
        self.stdout.write(f"Highest: {highest[0]} at {highest[1]:.0f} m")
        self.stdout.write("Rivers, flood zones and built-up area: run `seed_drainage`.")
