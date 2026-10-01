"""The Oceanic Niño Index (ONI), NOAA's measure of El Niño and La Niña.

ONI is the three-month running mean sea-surface temperature anomaly in the
Niño 3.4 region of the Pacific. At or above +0.5 is El Niño, at or below
-0.5 is La Niña. It matters here because El Niño years bring Kenya's
heaviest October-December rains (1997, 2006, 2015, 2023).

Source: NOAA Climate Prediction Center, public domain, updated monthly.
"""

from __future__ import annotations

import datetime as dt
import urllib.error
import urllib.request

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"

# Each row is a three-month season named by its initials (DJF, JFM, ...).
# A season is keyed here by its centre month.
SEASONS = ("DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ")

# ONI for a season is published early in the month after it ends, so on any
# given day the newest value is centred about two months back. Features use
# that same lag in training, so the model never sees an index it could not
# have had at the time.
PUBLICATION_LAG_MONTHS = 2

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


def fetch_oni() -> dict[tuple[int, int], float]:
    try:
        with urllib.request.urlopen(ONI_URL, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return parse_oni(response.read().decode("ascii", errors="replace"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ClimateIndexError(f"ONI request failed: {exc}") from exc


def oni_for(date: dt.date, table: dict[tuple[int, int], float]) -> float | None:
    """The ONI value that was available on `date`.

    The season centred PUBLICATION_LAG_MONTHS before `date`, or the newest
    one before that if the table stops short — which is how a date in the
    future gets today's value carried forward.
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
