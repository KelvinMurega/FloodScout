"""The Dartmouth Flood Observatory archive of large flood events.

This is where the model's labels come from: every large flood DFO recorded
worldwide from 1985 to 2021, each with start and end dates, a centroid, the
affected area in km², deaths, displacement and a severity class.

Source: G.R. Brakenridge, *Global Active Archive of Large Flood Events*,
Dartmouth Flood Observatory, University of Colorado.
http://floodobservatory.colorado.edu/Archives/index.html

The archive gives a centroid and an area, not an outline. `footprint()`
turns the two into a circle of that area. That is an approximation of where
the flood was, and the labels inherit it.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from django.contrib.gis.geos import Point

# The observatory's own server is often unreachable, so the Wayback Machine
# snapshot of the same file is the fallback. The snapshot is pinned, which
# also makes the training set reproducible.
ARCHIVE_URLS = (
    "https://floodobservatory.colorado.edu/temp/FloodArchive.xlsx",
    "https://web.archive.org/web/20240415011515id_/"
    "https://floodobservatory.colorado.edu/temp/FloodArchive.xlsx",
)

REQUEST_TIMEOUT_SECONDS = 120

# Arc 1960 / UTM 36S, the same metric CRS `seed_regions` measures area in.
METRIC_SRID = 21037

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_EXCEL_EPOCH = dt.date(1899, 12, 30)


class DFOError(RuntimeError):
    """Raised when the archive cannot be downloaded or read."""


@dataclass(frozen=True)
class FloodEvent:
    id: int
    country: str
    began: dt.date
    ended: dt.date
    lon: float
    lat: float
    area_km2: float
    severity: float  # DFO class: 1 large, 1.5 very large, 2 extreme
    dead: int
    displaced: int
    main_cause: str


def download(path: Path) -> Path:
    """Fetch the archive to `path` unless it is already there."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for url in ARCHIVE_URLS:
        try:
            with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                path.write_bytes(response.read())
            return path
        except (urllib.error.URLError, TimeoutError) as exc:
            errors.append(f"{url}: {exc}")
    raise DFOError("Could not download the DFO archive:\n  " + "\n  ".join(errors))


def _rows(path: Path) -> list[dict[str, str]]:
    """The first worksheet as a list of {column letter: cell text}.

    An .xlsx is a zip of XML, and this sheet is a plain table, so reading it
    directly avoids a spreadsheet dependency for one file.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            strings = [
                "".join(text.text or "" for text in item.iter(f"{{{_NS['m']}}}t"))
                for item in ElementTree.parse(archive.open("xl/sharedStrings.xml"))
                .getroot()
                .findall("m:si", _NS)
            ]
            sheet = ElementTree.parse(archive.open("xl/worksheets/sheet1.xml")).getroot()
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise DFOError(f"{path} is not a readable DFO archive: {exc}") from exc

    rows = []
    for row in sheet.iter(f"{{{_NS['m']}}}row"):
        cells = {}
        for cell in row.findall("m:c", _NS):
            value = cell.find("m:v", _NS)
            if value is None:
                continue
            column = re.match(r"[A-Z]+", cell.get("r")).group()
            cells[column] = strings[int(value.text)] if cell.get("t") == "s" else value.text
        rows.append(cells)
    return rows


def _excel_date(serial: str) -> dt.date:
    return _EXCEL_EPOCH + dt.timedelta(days=int(float(serial)))


def load_events(path: Path) -> list[FloodEvent]:
    """Every event in the archive that has dates, a centroid and an area."""
    rows = _rows(path)
    if not rows:
        raise DFOError(f"{path} has no rows.")
    header = rows[0]

    events = []
    for cells in rows[1:]:
        record = {header[column]: value for column, value in cells.items() if column in header}
        try:
            event = FloodEvent(
                id=int(float(record["ID"])),
                country=record.get("Country", "").strip(),
                began=_excel_date(record["Began"]),
                ended=_excel_date(record["Ended"]),
                lon=float(record["long"]),
                lat=float(record["lat"]),
                area_km2=float(record["Area"]),
                severity=float(record["Severity"]),
                dead=int(float(record.get("Dead", 0))),
                displaced=int(float(record.get("Displaced", 0))),
                main_cause=record.get("MainCause", "").strip(),
            )
        except (KeyError, ValueError):
            continue  # a row missing a date, centroid or area cannot be placed
        if event.area_km2 > 0 and event.ended >= event.began:
            events.append(event)
    return events


def footprint(event: FloodEvent):
    """A circle of the event's affected area around its centroid, in EPSG:4326."""
    centre = Point(event.lon, event.lat, srid=4326)
    centre.transform(METRIC_SRID)
    circle = centre.buffer(math.sqrt(event.area_km2 * 1_000_000 / math.pi))
    circle.transform(4326)
    return circle
