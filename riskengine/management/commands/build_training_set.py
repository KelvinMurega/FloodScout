"""Assemble the training table from real observations.

One row per county per month from 1996 to 2021: the rainfall, El Niño and
Indian Ocean Dipole indices, terrain and county area for the 30 days ending
on the last day of the month, and whether the Dartmouth Flood Observatory
recorded a large flood there in that window.

Rainfall comes from one of two sources (--rain-source):

  open-meteo    ERA5 at one representative point per county. The default;
                needs no account. --rain-model best_match asks for the
                service's own choice instead, which is ERA5 to 2016 and the
                ECMWF IFS analysis from 2017; the training sets up to
                October 2026 were built that way.
  earth-engine  ERA5-Land averaged over the whole county, computed by
                Google Earth Engine. Better for large counties; needs
                earthengine-api and an authorised Earth Engine project.

How rainfall was measured is written next to the CSV as
training_set.meta.json, and travels with the trained model so scoring
measures it the same way.

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
from geodata.services import dfo, earth_engine_rain
from geodata.services.climate_indices import (
    ClimateIndexError,
    dmi_for,
    fetch_dmi,
    fetch_oni,
    oni_for,
)
from geodata.services.open_meteo import (
    ARCHIVE_MODELS,
    DEFAULT_ARCHIVE_MODEL,
    OpenMeteoError,
    fetch_archive_rainfall,
)
from riskengine.ml.features import FEATURE_NAMES, HISTORY_DAYS, WINDOW_DAYS, feature_row

# The archive runs to 2021 but has almost no Kenyan floods before 1996, so
# earlier months would be labelled "no flood" for want of a report. See
# FIRST_TRAINING_YEAR in riskengine/ml/train.py.
FIRST_MONTH = dt.date(1996, 1, 1)
LAST_MONTH = dt.date(2021, 9, 1)

RAIN_SOURCES = ("open-meteo", "earth-engine")

DATA_DIR = Path(settings.BASE_DIR) / "data"
DFO_PATH = DATA_DIR / "external" / "dfo" / "FloodArchive.xlsx"
RAINFALL_DIR = DATA_DIR / "raw" / "rainfall_archive"
EE_RAINFALL_DIR = DATA_DIR / "raw" / "rainfall_ee"
TRAINING_SET_PATH = DATA_DIR / "processed" / "training_set.csv"
TRAINING_META_PATH = TRAINING_SET_PATH.with_suffix(".meta.json")

LABEL_COLUMNS = ("flood", "severity", "events")

# A training set or model that names no rainfall model was built before the
# archive was asked for one, so the service chose.
UNNAMED_RAIN_MODEL = "best_match"

# A flood counts for the 30-day window it began in, and for a later window
# when at least this many of its days fall in it. 1 is any overlap at all.
MIN_OVERLAP_DAYS = 1

FETCH_BATCH = 5


def month_ends(first: dt.date, last: dt.date) -> list[dt.date]:
    """The last day of every month from `first` to `last` inclusive."""
    ends = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        ends.append(dt.date(year, month, 1) - dt.timedelta(days=1))
    return ends


def in_window(event, start: dt.date, end: dt.date, min_days: int = MIN_OVERLAP_DAYS) -> bool:
    """Whether `event` counts as a flood in the window from `start` to `end`.

    It always counts in the window it began in, because the rain behind it
    fell there. With `min_days` above 1, a flood whose last few days run
    into a later window no longer labels the whole of that window.
    """
    overlap = (min(event.ended, end) - max(event.began, start)).days + 1
    return overlap >= 1 and (event.began >= start or overlap >= min_days)


def events_by_county(counties, log=print) -> dict:
    """{county name: [DFO events whose footprint touches the county]}."""
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
    log(f"DFO archive: {len(events)} events worldwide, {len(placed)} reaching Kenya")
    return by_county


def county_area(region) -> float | None:
    """km² of the county a region lies in. A sub-county is scored as its county."""
    county = region.parent if getattr(region, "parent_id", None) else region
    return county.area_size


def rain_model_of(meta: dict) -> str:
    """The archive rainfall model a training set or trained model was built on."""
    return meta.get("rain_model", UNNAMED_RAIN_MODEL)


def rainfall_cache_dir(cache_dir: Path, model: str) -> Path:
    """Where rainfall from `model` is cached under `cache_dir`.

    `best_match` keeps the top level, where every file was written before
    the model was named, so those files are still read back as what they
    are. Any other model gets a folder of its own.
    """
    return cache_dir if model == UNNAMED_RAIN_MODEL else cache_dir / model


def open_meteo_rainfall(
    counties,
    start,
    end,
    log=print,
    cache_dir: Path = RAINFALL_DIR,
    model: str = DEFAULT_ARCHIVE_MODEL,
):
    """{region name: {date: mm}} at each region's representative point, cached per region.

    `cache_dir` keeps regions of another level apart: a sub-county can share
    its name with a county. Rainfall from each `model` is cached apart.
    """
    cache_dir = rainfall_cache_dir(cache_dir, model)
    cache_dir.mkdir(parents=True, exist_ok=True)
    rainfall = {}
    missing = []
    for county in counties:
        # Some sub-county names hold a slash ("Sigowet/Soin").
        safe_name = county.name.replace("/", "-").replace("\\", "-")
        path = cache_dir / f"{safe_name}_{start}_{end}.json"
        if path.exists():
            cached = json.loads(path.read_text())
            rainfall[county.name] = {dt.date.fromisoformat(day): mm for day, mm in cached.items()}
        else:
            missing.append((county, path))

    if missing:
        log(f"Fetching {start} to {end} {model} rainfall for {len(missing)} counties")
    # A few counties per request, each written as it arrives, so a run cut
    # short by the rate limit resumes where it stopped.
    for index in range(0, len(missing), FETCH_BATCH):
        batch = missing[index : index + FETCH_BATCH]
        points = [representative_point(county) for county, _path in batch]
        for (county, path), series in zip(batch, fetch_archive_rainfall(points, start, end, model)):
            path.write_text(json.dumps({day.isoformat(): mm for day, mm in series.items()}))
            rainfall[county.name] = series
    return rainfall


def county_rainfall(
    counties,
    start,
    end,
    source: str,
    log=print,
    cache_dir: Path = RAINFALL_DIR,
    model: str = DEFAULT_ARCHIVE_MODEL,
):
    """Daily rainfall per county from `source`, and how many points scoring should average.

    `model` is the archive model for open-meteo; Earth Engine is always ERA5-Land.
    """
    if source == "earth-engine":
        series = earth_engine_rain.fetch_county_rainfall(counties, start, end, EE_RAINFALL_DIR, log=log)
        return series, earth_engine_rain.SCORING_POINTS
    return open_meteo_rainfall(counties, start, end, log=log, cache_dir=cache_dir, model=model), 1


class Command(BaseCommand):
    help = "Build data/processed/training_set.csv from DFO floods, rainfall, ONI and DMI."

    def add_arguments(self, parser):
        parser.add_argument(
            "--rain-source",
            choices=RAIN_SOURCES,
            default="open-meteo",
            help="open-meteo: one point per county (default). "
            "earth-engine: averaged over the county (needs earthengine-api).",
        )
        parser.add_argument(
            "--rain-model",
            choices=ARCHIVE_MODELS,
            default=DEFAULT_ARCHIVE_MODEL,
            help=f"Archive model for open-meteo rainfall (default {DEFAULT_ARCHIVE_MODEL}). "
            "best_match is ERA5 to 2016 and ECMWF IFS from 2017, as the older training sets were.",
        )

    def handle(self, *args, **options):
        counties = list(
            Region.objects.filter(level=Region.Level.COUNTY)
            .prefetch_related("geospatial_features")
            .order_by("name")
        )
        if not counties:
            raise CommandError("No counties in the database — run `seed_regions` first.")

        source = options["rain_source"]
        model = options["rain_model"]
        if source == "earth-engine" and model != DEFAULT_ARCHIVE_MODEL:
            raise CommandError("--rain-model applies to open-meteo rainfall; Earth Engine is ERA5-Land.")
        ends = month_ends(FIRST_MONTH, LAST_MONTH)
        try:
            placed = events_by_county(counties, log=self.stdout.write)
            rainfall, rain_points = county_rainfall(
                counties,
                ends[0] - dt.timedelta(days=HISTORY_DAYS),
                ends[-1],
                source,
                log=self.stdout.write,
                model=model,
            )
            oni = fetch_oni()
            dmi = fetch_dmi()
        except (
            dfo.DFOError,
            OpenMeteoError,
            ClimateIndexError,
            earth_engine_rain.EarthEngineError,
        ) as exc:
            raise CommandError(str(exc)) from exc

        rows = []
        for county in counties:
            rain = rainfall.get(county.name, {})
            terrain = next(iter(county.geospatial_features.all()), None)
            # The county's normal rainfall for each calendar month, from the
            # same record the rows are built on. Training recomputes it per
            # cross-validation fold; this column is for reference.
            totals = defaultdict(list)
            for end in ends:
                start = end - dt.timedelta(days=WINDOW_DAYS - 1)
                totals[end.month].append(
                    sum(rain.get(start + dt.timedelta(days=i), 0.0) for i in range(WINDOW_DAYS))
                )
            normals = {month: sum(values) / len(values) for month, values in totals.items()}

            for end in ends:
                start = end - dt.timedelta(days=WINDOW_DAYS - 1)
                active = [event for event in placed[county.name] if in_window(event, start, end)]
                features = feature_row(
                    rain,
                    end,
                    normal_30d=normals[end.month],
                    oni=oni_for(end, oni),
                    dmi=dmi_for(end, dmi),
                    area_km2=county_area(county),
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
        meta = {
            "rain_source": "earth-engine-county-mean" if source == "earth-engine" else "open-meteo-point",
            "rain_points": rain_points,
        }
        if source == "open-meteo":
            meta["rain_model"] = model
        TRAINING_META_PATH.write_text(
            json.dumps(
                {
                    **meta,
                    "period": [ends[0].isoformat(), ends[-1].isoformat()],
                    "min_overlap_days": MIN_OVERLAP_DAYS,
                    "built": dt.date.today().isoformat(),
                },
                indent=2,
            )
            + "\n"
        )

        flooded = sum(row[-3] for row in rows)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(rows)} rows to {TRAINING_SET_PATH}"))
        self.stdout.write(
            f"County-months with a recorded flood: {flooded} ({100 * flooded / len(rows):.1f}%)"
        )

