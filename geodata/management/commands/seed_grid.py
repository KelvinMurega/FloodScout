"""Lay a regular grid over Kenya and measure each cell's terrain.

One GridCell per square whose centre lies inside a county, with elevation
and slope measured at the centre exactly as `seed_terrain` measures a
county's. `score_grid` then scores every cell from its own rainfall, which
is what lets the map show where inside a county the risk is.

    python manage.py seed_grid                  # 0.25 degree cells, about 28 km
    python manage.py seed_grid --cell-deg 0.1   # finer, but see the note below

Every cell costs one rainfall request a day. At 0.25 degrees Kenya is about
750 cells, which with the region scores stays inside Open-Meteo's free daily
allowance. At 0.1 degrees it is about 4,700 and does not.

Re-running with the same size updates the cells in place. A different size
replaces the grid, and the grid scores with it.
"""

from __future__ import annotations

import math

from django.contrib.gis.geos import Point as GEOSPoint
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from geodata.management.commands.seed_terrain import SLOPE_OFFSET_DEG, slope_degrees
from geodata.models import GridCell, Region
from geodata.services.open_meteo import OpenMeteoError, Point, fetch_elevations

DEFAULT_CELL_DEG = 0.25


def cell_centres(county: Region, size: float) -> list[Point]:
    """Centres of the grid cells, aligned to multiples of `size`, inside `county`."""
    xmin, ymin, xmax, ymax = county.geom.extent
    centres = []
    for i in range(math.floor(xmin / size), math.ceil(xmax / size)):
        for j in range(math.floor(ymin / size), math.ceil(ymax / size)):
            lon, lat = round((i + 0.5) * size, 6), round((j + 0.5) * size, 6)
            if county.geom.contains(GEOSPoint(lon, lat, srid=4326)):
                centres.append(Point(lon=lon, lat=lat))
    return centres


class Command(BaseCommand):
    help = "Lay a grid of cells over the counties and measure each cell's elevation and slope."

    def add_arguments(self, parser):
        parser.add_argument(
            "--cell-deg",
            type=float,
            default=DEFAULT_CELL_DEG,
            help=f"Cell width in degrees (default {DEFAULT_CELL_DEG}).",
        )

    def handle(self, *args, **options):
        size = options["cell_deg"]
        if not 0.05 <= size <= 1.0:
            raise CommandError("--cell-deg must be between 0.05 and 1.")

        counties = list(Region.objects.filter(level=Region.Level.COUNTY).order_by("county_name"))
        if not counties:
            raise CommandError("No counties in the database — run `seed_regions` first.")

        cells = [(county, centre) for county in counties for centre in cell_centres(county, size)]
        self.stdout.write(f"{len(cells)} cells of {size} degrees over {len(counties)} counties")

        # Five samples per cell, as in seed_terrain: centre, then N/S/E/W.
        points = []
        for _county, c in cells:
            points += [
                c,
                Point(c.lon, c.lat + SLOPE_OFFSET_DEG),
                Point(c.lon, c.lat - SLOPE_OFFSET_DEG),
                Point(c.lon + SLOPE_OFFSET_DEG, c.lat),
                Point(c.lon - SLOPE_OFFSET_DEG, c.lat),
            ]
        try:
            elevations = fetch_elevations(points)
        except OpenMeteoError as exc:
            raise CommandError(str(exc)) from exc

        with transaction.atomic():
            replaced, _ = GridCell.objects.exclude(size_deg=size).delete()
            if replaced:
                self.stdout.write(f"Removed the previous grid ({replaced} rows, scores included)")
            for index, (county, centre) in enumerate(cells):
                middle, north, south, east, west = elevations[index * 5 : index * 5 + 5]
                GridCell.objects.update_or_create(
                    lon=centre.lon,
                    lat=centre.lat,
                    size_deg=size,
                    defaults={
                        "county": county,
                        "elevation": middle,
                        "slope": round(
                            slope_degrees(middle, north, south, east, west, centre.lat), 3
                        ),
                    },
                )

        self.stdout.write(self.style.SUCCESS(f"Grid cells now: {GridCell.objects.count()}"))
        self.stdout.write("Score them with `score_grid`.")
