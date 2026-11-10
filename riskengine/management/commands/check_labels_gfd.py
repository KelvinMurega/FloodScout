"""Measure the circle labels against flood water mapped from satellite.

The model's labels place each Dartmouth Flood Observatory event as a circle
and call every county the circle touches flooded. The Global Flood Database
mapped the water of some of those events. For each event it mapped, this
counts the counties under the circle that held mapped flood water, the ones
that did not, and the ones outside the circle that did.

It measures the labels. It does not score the model and changes nothing.

Writes data/processed/gfd_label_check.csv (one row per event and county that
either source marks) and prints the counts. Needs Earth Engine the first
time; the mapped areas are then cached in data/external/gfd/.
"""

from __future__ import annotations

import csv

from django.core.management.base import BaseCommand, CommandError

from geodata.models import Region
from geodata.services import dfo, gfd
from geodata.services.earth_engine_rain import EarthEngineError
from riskengine.ml.train import FIRST_TRAINING_YEAR

from .build_training_set import DATA_DIR, DFO_PATH

CACHE_PATH = DATA_DIR / "external" / "gfd" / "county_flooded_km2.json"
OUTPUT_PATH = DATA_DIR / "processed" / "gfd_label_check.csv"

# The database's years.
MAPPED_YEARS = (2000, 2018)


class Command(BaseCommand):
    help = "Compare the circle flood labels with flood water mapped by the Global Flood Database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-km2",
            type=float,
            default=5.0,
            help="Mapped flood water a county needs to count as flooded (default 5, as validate_unosat).",
        )

    def handle(self, *args, **options):
        counties = list(Region.objects.filter(level=Region.Level.COUNTY).order_by("name"))
        if not counties:
            raise CommandError("No counties in the database — run `seed_regions` first.")

        try:
            events = dfo.load_events(dfo.download(DFO_PATH))
        except dfo.DFOError as exc:
            raise CommandError(str(exc)) from exc

        # The events the training set places on Kenya, as build_training_set does.
        circles: dict[int, set[str]] = {}
        began = {}
        for event in events:
            if event.ended.year < FIRST_TRAINING_YEAR:
                continue
            if not (25 <= event.lon <= 50 and -14 <= event.lat <= 14):
                continue
            footprint = dfo.footprint(event)
            touched = {county.name for county in counties if county.geom.intersects(footprint)}
            if touched:
                circles[event.id] = touched
                began[event.id] = event.began

        in_years = {
            event_id for event_id in circles if MAPPED_YEARS[0] <= began[event_id].year <= MAPPED_YEARS[1]
        }
        try:
            mapped_ids = sorted(in_years & gfd.mapped_event_ids())
            mapped = gfd.flooded_km2(counties, mapped_ids, CACHE_PATH, log=self.stdout.write)
        except EarthEngineError as exc:
            raise CommandError(str(exc)) from exc

        min_km2 = options["min_km2"]
        rows = gfd.compare_with_circles(circles, mapped, min_km2)

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with OUTPUT_PATH.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["event", "began", "county", "under_circle", "mapped_km2", "mapped_wet"])
            for event_id in mapped_ids:
                for county in counties:
                    km2 = mapped[event_id].get(county.name, 0.0)
                    under = county.name in circles[event_id]
                    if under or km2 >= min_km2:
                        writer.writerow(
                            [event_id, began[event_id], county.name, int(under), km2, int(km2 >= min_km2)]
                        )

        labels = sum(len(circles[event_id]) for event_id in in_years)
        covered = sum(len(circles[event_id]) for event_id in mapped_ids)
        self.stdout.write(
            f"Events placed on Kenya, {MAPPED_YEARS[0]} to {MAPPED_YEARS[1]}: {len(in_years)}, "
            f"of which mapped: {len(mapped_ids)}"
        )
        self.stdout.write(f"County labels from those events: {labels}, under a mapped event: {covered}")
        self.stdout.write("")
        self.stdout.write(f"A county is mapped wet at {min_km2:g} km² or more of flood water.")
        self.stdout.write(
            f"  {'event':>6} {'began':<11} {'circle':>7} {'and wet':>8} {'not wet':>8} "
            f"{'wet outside':>12} {'km²':>8}"
        )
        for row in rows:
            self.stdout.write(
                f"  {row['event']:>6} {began[row['event']]!s:<11} {row['circle']:>7} "
                f"{row['circle_and_wet']:>8} {row['circle_only']:>8} {row['wet_only']:>12} "
                f"{row['mapped_km2']:>8}"
            )
        total = {key: sum(row[key] for row in rows) for key in ("circle", "circle_and_wet", "circle_only", "wet_only")}
        self.stdout.write(
            f"  {'all':>6} {'':<11} {total['circle']:>7} {total['circle_and_wet']:>8} "
            f"{total['circle_only']:>8} {total['wet_only']:>12}"
        )
        if total["circle"]:
            self.stdout.write("")
            self.stdout.write(
                f"{100 * total['circle_and_wet'] / total['circle']:.0f}% of the counties under a circle "
                f"held mapped flood water. {total['wet_only']} counties held it outside a circle."
            )
        self.stdout.write(self.style.SUCCESS(f"Wrote {OUTPUT_PATH}"))
