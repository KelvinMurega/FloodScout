"""The two ocean indices that drive Kenya's rainy seasons.

ONI, the Oceanic Niño Index, is the three-month running mean sea-surface
temperature anomaly in the Niño 3.4 region of the Pacific. At or above +0.5
is El Niño, at or below -0.5 is La Niña. El Niño years bring Kenya's
heaviest October-December rains (1997, 2006, 2015, 2023).

DMI, the Dipole Mode Index, measures the Indian Ocean Dipole: the
sea-surface temperature difference between the western and eastern Indian
Ocean. A positive dipole warms the water off East Africa and drives the
short rains as strongly as El Niño does; 2019 had a record positive dipole
and severe floods with only a weak El Niño.

Sources, both NOAA, public domain, updated monthly:
  ONI  Climate Prediction Center
  DMI  Physical Sciences Laboratory, from HadISST1.1
"""

from __future__ import annotations

import datetime as dt
import urllib.error
import urllib.request

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
DMI_URL = "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data"

# Each row is a three-month season named by its initials (DJF, JFM, ...).
# A season is keyed here by its centre month.
SEASONS = ("DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ")

# ONI for a season is published early in the month after it ends, so on any
# given day the newest value is centred about two months back. Features use
# that same lag in training, so the model never sees an index it could not
# have had at the time. The DMI file runs about two months behind as well.
PUBLICATION_LAG_MONTHS = 2

# Marks a month the DMI file has no value for yet.
DMI_MISSING = -9000.0

REQUEST_TIMEOUT_SECONDS = 60


class ClimateIndexError(RuntimeError):
    """Raised when the index cannot be fetched or parsed."""


def parse_oni(text: str) -> dict[tuple[int, int], float]:
    """{(year, centre month): anomaly} from NOAA's fixed-width table."""
    table: dict[tuple[int, int], float] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[0] not in SEASONS:
            continue
        table[(int(parts[1]), SEASONS.index(parts[0]) + 1)] = float(parts[3])
    if not table:
        raise ClimateIndexError("No ONI rows found in the NOAA table.")
    return table


def parse_dmi(text: str) -> dict[tuple[int, int], float]:
    """{(year, month): DMI} from PSL's table: a year, then twelve monthly values."""
    table: dict[tuple[int, int], float] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 13 or not parts[0].isdigit():
            continue
        year = int(parts[0])
        for month, raw in enumerate(parts[1:], start=1):
            value = float(raw)
            if value > DMI_MISSING:
                table[(year, month)] = value
    if not table:
        raise ClimateIndexError("No DMI rows found in the NOAA table.")
    return table


def _fetch(url: str, name: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.read().decode("ascii", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ClimateIndexError(f"{name} request failed: {exc}") from exc


def fetch_oni() -> dict[tuple[int, int], float]:
    return parse_oni(_fetch(ONI_URL, "ONI"))


def fetch_dmi() -> dict[tuple[int, int], float]:
    return parse_dmi(_fetch(DMI_URL, "DMI"))


def index_for(date: dt.date, table: dict[tuple[int, int], float]) -> float | None:
    """The value of a monthly index that was available on `date`.

    The month PUBLICATION_LAG_MONTHS before `date`, or the newest one before
    that if the table stops short — which is how a date in the future gets
    today's value carried forward.
    """
    if not table:
        return None
    index = date.year * 12 + (date.month - 1) - PUBLICATION_LAG_MONTHS
    oldest = min(year * 12 + (month - 1) for year, month in table)
    while index >= oldest:
        value = table.get((index // 12, index % 12 + 1))
        if value is not None:
            return value
        index -= 1
    return None


# Kept under its old name: ONI is looked up exactly like any other index.
oni_for = index_for
dmi_for = index_for
