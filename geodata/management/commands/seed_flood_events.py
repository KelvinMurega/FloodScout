"""Load the large floods recorded in Kenya into geodata.FloodEvent.

The events are the Dartmouth Flood Observatory's, the same archive the
model's labels come from (geodata/services/dfo.py). Kept are the ones whose
footprint reaches a county, which is the test `build_training_set` uses to
label a county-month. The map draws them as past floods, and the detail
panel says how often a place has flooded before.

    python manage.py seed_flood_events

Downloads the archive once, to data/external/dfo/, then reads it from there.
Safe to re-run: events are matched on their DFO ID and updated.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.gis.db.models import Union
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from geodata.models import FloodEvent, Region
from geodata.services import dfo

DFO_PATH = Path(settings.BASE_DIR) / "data" / "external" / "dfo" / "FloodArchive.xlsx"


def kenyan_events(events, kenya) -> list[tuple[dfo.FloodEvent, object]]:
    """(event, footprint) for each event whose footprint touches `kenya`."""
    kept = []
    for event in events:
        # Cheap reject before any geometry, as build_training_set does.
        if not (25 <= event.lon <= 50 and -14 <= event.lat <= 14):
            continue
        footprint = dfo.footprint(event)
        if kenya.intersects(footprint):
            kept.append((event, footprint))
    return kept


class Command(BaseCommand):
    help = "Load the large floods DFO recorded in Kenya, for the map's past-floods layer."

    def handle(self, *args, **options):
        kenya = Region.objects.filter(level=Region.Level.COUNTY).aggregate(geom=Union("geom"))[
            "geom"
        ]
        if kenya is None:
            raise CommandError("No counties in the database — run `manage.py seed_regions` first.")
        try:
            events = dfo.load_events(dfo.download(DFO_PATH))
        except dfo.DFOError as exc:
            raise CommandError(str(exc)) from exc

        kept = kenyan_events(events, kenya)
        created = 0
        with transaction.atomic():
            for event, footprint in kept:
                _row, new = FloodEvent.objects.update_or_create(
                    dfo_id=event.id,
                    defaults={
                        "began": event.began,
                        "ended": event.ended,
                        "centroid": Point(event.lon, event.lat, srid=4326),
                        "footprint": footprint,
                        "area_km2": event.area_km2,
                        "severity": event.severity,
                        "dead": max(event.dead, 0),
                        "displaced": max(event.displaced, 0),
                        "main_cause": event.main_cause[:200],
                    },
                )
                created += new

        self.stdout.write(
            self.style.SUCCESS(
                f"{len(kept)} of {len(events)} DFO events reach Kenya "
                f"({created} new, {len(kept) - created} updated)."
            )
        )
        if kept:
            years = sorted(event.began.year for event, _ in kept)
            self.stdout.write(f"From {years[0]} to {years[-1]}.")
