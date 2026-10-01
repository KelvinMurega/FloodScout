"""Assemble the training table from real observations.

One row per county per month from 1985 to 2021: the rainfall, El Niño index
and terrain for the 30 days ending on the last day of the month, and whether
the Dartmouth Flood Observatory recorded a large flood there in that window.

Counties only. The flood archive locates an event to a centroid and an area,
which is honest at county scale and would be false precision below it.

Everything downloaded is cached under data/, so a re-run is offline and
quick. The output is data/processed/training_set.csv.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from geodata.management.commands.seed_terrain import representative_point
from geodata.models import Region
from geodata.services import dfo
from geodata.services.climate_indices import ClimateIndexError, fetch_oni, oni_for
from geodata.services.open_meteo import OpenMeteoError, fetch_archive_rainfall
from riskengine.ml.features import FEATURE_NAMES, HISTORY_DAYS, WINDOW_DAYS, feature_row

# The span the flood archive covers in full.
FIRST_MONTH = dt.date(1985, 1, 1)
LAST_MONTH = dt.date(2021, 9, 1)

DATA_DIR = Path(settings.BASE_DIR) / "data"
DFO_PATH = DATA_DIR / "external" / "dfo" / "FloodArchive.xlsx"
RAINFALL_DIR = DATA_DIR / "raw" / "rainfall_archive"
TRAINING_SET_PATH = DATA_DIR / "processed" / "training_set.csv"

LABEL_COLUMNS = ("flood", "severity", "events")

FETCH_BATCH = 5


def month_ends(first: dt.date, last: dt.date) -> list[dt.date]:
    """The last day of every month from `first` to `last` inclusive."""
    ends = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        ends.append(dt.date(year, month, 1) - dt.timedelta(days=1))
    return ends


class Command(BaseCommand):
    help = "Build data/processed/training_set.csv from DFO floods, ERA5 rainfall and ONI."

    def handle(self, *args, **options):
        counties = list(
            Region.objects.filter(level=Region.Level.COUNTY)
            .prefetch_related("geospatial_features")
            .order_by("name")
        )
        if not counties:
            raise CommandError("No counties in the database — run `seed_regions` first.")

        ends = month_ends(FIRST_MONTH, LAST_MONTH)
        try:
            events_by_county = self._events_by_county(counties)
            rainfall = self._rainfall(counties, ends[0] - dt.timedelta(days=HISTORY_DAYS), ends[-1])
            oni = fetch_oni()
        except (dfo.DFOError, OpenMeteoError, ClimateIndexError) as exc:
            raise CommandError(str(exc)) from exc

        rows = []
        for county in counties:
            rain = rainfall[county.name]
            terrain = next(iter(county.geospatial_features.all()), None)
            # The county's normal rainfall for each calendar month, from the
            # same record the rows are built on.
            totals = defaultdict(list)
            for end in ends:
                start = end - dt.timedelta(days=WINDOW_DAYS - 1)
                totals[end.month].append(
                    sum(rain.get(start + dt.timedelta(days=i), 0.0) for i in range(WINDOW_DAYS))
                )
            normals = {month: sum(values) / len(values) for month, values in totals.items()}

            for end in ends:
                start = end - dt.timedelta(days=WINDOW_DAYS - 1)
                active = [
                    event
                    for event in events_by_county[county.name]
                    if event.began <= end and event.ended >= start
                ]
                features = feature_row(
                    rain,
                    end,
                    normal_30d=normals[end.month],
                    oni=oni_for(end, oni),
                    terrain=terrain,
                )
                rows.append(
                    [county.name, end.isoformat(), round(normals[end.month], 2)]
                    + [round(value, 5) for value in features]
                    + [
                        int(bool(active)),
                        max((event.severity for event in active), default=0),
                        len(active),
                    ]
                )

        TRAINING_SET_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRAINING_SET_PATH.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["county", "date", "normal_30d", *FEATURE_NAMES, *LABEL_COLUMNS])
            writer.writerows(rows)

        flooded = sum(row[-3] for row in rows)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(rows)} rows to {TRAINING_SET_PATH}"))
        self.stdout.write(
            f"County-months with a recorded flood: {flooded} ({100 * flooded / len(rows):.1f}%)"
        )

    def _events_by_county(self, counties):
        """DFO events whose footprint touches each county."""
        events = dfo.load_events(dfo.download(DFO_PATH))
        by_county = defaultdict(list)
        placed = set()
        for event in events:
            # Cheap reject before any geometry: Kenya sits inside this box,
            # padded by the radius of the largest footprints.
            if not (25 <= event.lon <= 50 and -14 <= event.lat <= 14):
                continue
            footprint = dfo.footprint(event)
            for county in counties:
                if county.geom.intersects(footprint):
                    by_county[county.name].append(event)
                    placed.add(event.id)
        self.stdout.write(
            f"DFO archive: {len(events)} events worldwide, {len(placed)} reaching Kenya"
        )
        return by_county

    def _rainfall(self, counties, start, end):
        """{county name: {date: mm}}, downloaded once and cached per county."""
        RAINFALL_DIR.mkdir(parents=True, exist_ok=True)
        rainfall = {}
        missing = []
        for county in counties:
            path = RAINFALL_DIR / f"{county.name}_{start}_{end}.json"
            if path.exists():
                cached = json.loads(path.read_text())
                rainfall[county.name] = {dt.date.fromisoformat(day): mm for day, mm in cached.items()}
            else:
                missing.append((county, path))

        if missing:
            self.stdout.write(f"Fetching {start} to {end} rainfall for {len(missing)} counties")
        # A few counties per request, each written as it arrives, so a run cut
        # short by the rate limit resumes where it stopped.
        for index in range(0, len(missing), FETCH_BATCH):
            batch = missing[index : index + FETCH_BATCH]
            points = [representative_point(county) for county, _path in batch]
            for (county, path), series in zip(batch, fetch_archive_rainfall(points, start, end)):
                path.write_text(json.dumps({day.isoformat(): mm for day, mm in series.items()}))
                rainfall[county.name] = series
        return rainfall
