"""Download each county's daily river discharge for the training years.

    python manage.py build_discharge                          # up to the budget, then stop
    python manage.py build_discharge --every-hours 24         # the same each day until done

The inputs under trial `discharge_30d_max`, `discharge_30d_mean` and
`discharge_rise` (riskengine/ml/features.py) are read from GloFAS river
discharge, through Open-Meteo's flood service, at a point on each county's
largest river (geodata/services/river_points.py).

Open-Meteo counts a long history as many calls, about one per two weeks of
data per point, and allows 10,000 a day on the free tier. The whole record
for 47 counties is about 30,000, and the daily refresh needs part of the
quota too. So the download is cut into one file per county per year, stops
when `--budget` calls have been spent, and picks up where it stopped on the
next run. With `--every-hours` it waits and carries on by itself.

Everything lands under data/raw/discharge/: river_points_county.json, then
<county>/<year>.json.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from geodata.models import Region
from geodata.services import open_meteo, river_points
from geodata.services.earth_engine_rain import EarthEngineError
from geodata.services.open_meteo import OpenMeteoError, Point

from .build_training_set import DATA_DIR, LAST_MONTH

DISCHARGE_DIR = DATA_DIR / "raw" / "discharge"
POINTS_PATH = DISCHARGE_DIR / "river_points_county.json"

# GloFAS's record for Kenya starts here; earlier days come back empty.
FIRST_DAY = dt.date(1997, 1, 1)
# The end of the last training window.
LAST_DAY = dt.date(LAST_MONTH.year, LAST_MONTH.month + 1, 1) - dt.timedelta(days=1)

# A fortnight in the long rains of 2020, when every large river ran high,
# to tell the river's cell from the dry ones beside it.
PROBE = (dt.date(2020, 5, 1), dt.date(2020, 5, 14))
# Cells either side of the river's line: a 3 by 3 block. HydroRIVERS lines
# are good to about 500 m, so the river's GloFAS cell is among these.
PROBE_REACH = 1
# How much more than its long-term mean a river carried in the fortnight
# above. The median over the 47 counties, measured on 21 October 2026 from
# the wettest cell beside each river, was 7.2 (most between 4 and 15). The
# cell nearest this multiple of the reach's HydroRIVERS mean is taken, which
# passes over Lake Baringo beside the Perkerra and the Tana delta beside
# Lamu's small river.
PROBE_WET_FACTOR = 7.0

DEFAULT_BUDGET = 5000
# Seconds between requests. A year of one point is about 26 calls, so this
# stays well under the 600 a minute the free tier allows.
PAUSE_SECONDS = 3

# Indirection so tests can skip the waits.
_sleep = time.sleep


def year_spans(first: dt.date = FIRST_DAY, last: dt.date = LAST_DAY) -> list[tuple[dt.date, dt.date]]:
    """One (start, end) per calendar year from `first` to `last`, clipped to them."""
    return [
        (max(first, dt.date(year, 1, 1)), min(last, dt.date(year, 12, 31)))
        for year in range(first.year, last.year + 1)
    ]


def year_path(county: str, year: int, root: Path = DISCHARGE_DIR) -> Path:
    return root / county / f"{year}.json"


def load_points(path: Path = POINTS_PATH) -> dict[str, dict]:
    return json.loads(path.read_text()) if path.exists() else {}


def cached_discharge(counties, root: Path = DISCHARGE_DIR) -> dict[str, dict[dt.date, float]]:
    """{county: {date: m³/s}} from every year file downloaded so far."""
    out = {}
    for county in counties:
        series = {}
        for path in sorted((root / county).glob("*.json")):
            series.update({dt.date.fromisoformat(day): m3s for day, m3s in json.loads(path.read_text()).items()})
        out[county] = series
    return out


def missing_years(counties, root: Path = DISCHARGE_DIR) -> list[tuple[str, dt.date, dt.date]]:
    """The (county, start, end) not downloaded yet, county by county."""
    return [
        (county, start, end)
        for county in counties
        for start, end in year_spans()
        if not year_path(county, start.year, root).exists()
    ]


class Command(BaseCommand):
    help = "Download daily river discharge on each county's largest river, within a daily call budget."

    def add_arguments(self, parser):
        parser.add_argument(
            "--budget",
            type=float,
            default=DEFAULT_BUDGET,
            help=f"Open-Meteo calls to spend per run (default {DEFAULT_BUDGET}).",
        )
        parser.add_argument(
            "--every-hours",
            type=float,
            help="Keep going: after spending the budget, wait this long and spend it again, until done.",
        )

    def handle(self, *args, **options):
        counties = list(Region.objects.filter(level=Region.Level.COUNTY).order_by("name"))
        if not counties:
            raise CommandError("No counties in the database: run seed_regions first.")
        while True:
            spent = self._points(counties, options["budget"])
            spent += self._discharge(counties, options["budget"] - spent)
            left = missing_years(load_points())
            if not left:
                self.stdout.write(self.style.SUCCESS("All discharge downloaded."))
                return
            self.stdout.write(f"{len(left)} county-years still to download.")
            if not options["every_hours"]:
                return
            self.stdout.write(f"Waiting {options['every_hours']:g} hours for the quota to come back.")
            _sleep(options["every_hours"] * 3600)

    def _points(self, counties, budget: float) -> float:
        """Find the river point of every county without one; returns the calls spent."""
        points = load_points()
        todo = [county for county in counties if county.name not in points]
        if not todo:
            return 0.0
        self.stdout.write(f"Finding the largest river in {len(todo)} counties (Earth Engine)")
        try:
            reaches = river_points.outlet_points(todo, log=self.stdout.write)
        except EarthEngineError as exc:
            raise CommandError(str(exc)) from exc

        probe_calls = open_meteo.calls_for((2 * PROBE_REACH + 1) ** 2, (PROBE[1] - PROBE[0]).days + 1)
        spent = 0.0
        DISCHARGE_DIR.mkdir(parents=True, exist_ok=True)
        for county in todo:
            reach = reaches.get(county.name)
            if reach is None:
                continue
            if spent + probe_calls > budget:
                break
            try:
                cell, mean = open_meteo.river_cell(
                    Point(reach["lon"], reach["lat"]),
                    *PROBE,
                    expected_m3s=PROBE_WET_FACTOR * reach["mean_m3s"],
                    reach=PROBE_REACH,
                )
            except OpenMeteoError as exc:
                raise CommandError(str(exc)) from exc
            spent += probe_calls
            points[county.name] = {
                "lon": cell.lon,
                "lat": cell.lat,
                "probe_mean_m3s": round(mean, 2),
                "reach_lon": round(reach["lon"], 5),
                "reach_lat": round(reach["lat"], 5),
                "reach_mean_m3s": reach["mean_m3s"],
                "upland_km2": reach["upland_km2"],
            }
            POINTS_PATH.write_text(json.dumps(points, indent=1, sort_keys=True))
            self.stdout.write(f"  {county.name}: {cell.lat:.3f}, {cell.lon:.3f}, {mean:.1f} m³/s in May 2020")
            _sleep(PAUSE_SECONDS)
        return spent

    def _discharge(self, counties, budget: float) -> float:
        """Download missing county-years until `budget` is spent; returns the calls spent."""
        points = load_points()
        spent = 0.0
        done = 0
        for county, start, end in missing_years([c.name for c in counties if c.name in points]):
            calls = open_meteo.calls_for(1, (end - start).days + 1)
            if spent + calls > budget:
                break
            point = points[county]
            try:
                (series,) = open_meteo.fetch_discharge([Point(point["lon"], point["lat"])], start, end)
            except OpenMeteoError as exc:
                # Most likely the day's quota is used up. What is saved stays saved.
                self.stdout.write(self.style.WARNING(f"Stopped at {county} {start.year}: {exc}"))
                break
            path = year_path(county, start.year)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({day.isoformat(): m3s for day, m3s in series.items()}))
            spent += calls
            done += 1
            if done % 25 == 0:
                self.stdout.write(f"  {done} county-years, {spent:.0f} calls")
            _sleep(PAUSE_SECONDS)
        self.stdout.write(f"Downloaded {done} county-years for about {spent:.0f} calls.")
        return spent
