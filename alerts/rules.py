"""When a region gets an alert, and at what level.

Fixed, readable rules over what the risk engine has already worked out for
each region (riskengine/situation.py): today's category, the change over
the last week, and the 7-day forecast with its wettest day.

  Warning   Very High today and rising, with heavy rain for the county
            forecast in the 7 days, or
            forecast Very High within 3 days with heavy rain for the
            county forecast in those 3 days
  Watch     Very High today, or forecast Very High within the 7 days
  Advisory  High today and rising, or a day of HEAVY_RAIN_MM or more
            forecast in the 7 days

"Heavy rain for the county" is forecast rain over the coming days above
the county's own line for a week in that month: the 90th percentile of its
wettest 7 days in that calendar month, 1996 to 2016 (HEAVY_RAIN_7D_PATH,
written by `manage.py backtest_alerts --save-thresholds`). Until 22 October
2026 a Warning needed a day of HEAVY_RAIN_MM, one line for all of Kenya.

Why: replayed over 1996 to 2016 (alerts/backtest.py), Very High alone
alerted 2,177 county-months, about 20 counties in a typical alert month,
and one alert in four was followed by a recorded flood. With heavy rain
for the county as well it alerted 460, about 6 counties a month, and one in
three was followed by a flood, but it caught 145 floods against 584. So
Watch stays at Very High, which catches the floods, and Warning, the level
that says "act now", needs the local rain. That split was chosen after the
backtest was seen, and the backtest's rain is what fell, a perfect forecast.

HEAVY_RAIN_MM is 50 mm in 24 hours, the proxy the Kenya Meteorological
Department uses for flood early warning. "Rising" is up at least RISING
times the usual chance over 7 days. "High" on its own never alerts: in a
wet El Niño season nearly every county is High, and an alert that is
always on is an alert nobody reads.

The wording of each level's advice is in INSTRUCTIONS. It is general
flood-safety advice of the kind the Kenya Red Cross and county disaster
offices give, not an evacuation order.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from riskengine.briefing import Place

from .models import LEVEL_RANK, Level

HEAVY_RAIN_MM = 50.0
RISING = 0.2
SOON_DAYS = 3

# Each county's heavy-rain line for a week, by month: `manage.py backtest_alerts --save-thresholds`.
HEAVY_RAIN_7D_PATH = Path(__file__).resolve().parent / "heavy_rain_7d.json"

INSTRUCTIONS = {
    Level.ADVISORY: (
        "Stay informed: check the forecast daily. Clear drains and gutters around your home, "
        "and know where you would go if water rose."
    ),
    Level.WATCH: (
        "Be ready to move. Know your route to higher ground, keep documents, medicines and a "
        "phone charger in a waterproof bag, and move livestock and valuables away from rivers "
        "and low ground."
    ),
    Level.WARNING: (
        "Act now. Move away from rivers and low ground, do not walk or drive through flood "
        "water, and follow instructions from your county government. In an emergency call 999 "
        "or 112, or the Kenya Red Cross on 1199."
    ),
}

DISCLAIMER = (
    "FloodScout is a university research system, not the Kenya Meteorological Department or "
    "the National Disaster Operations Centre. Official warnings come from them."
)


@dataclass
class Decision:
    level: str | None
    basis: str  # "observed" or "forecast"
    reasons: list[dict]
    onset: dt.date


def _day(date: dt.date) -> str:
    return f"{date:%A} {date.day} {date:%B}"


@lru_cache(maxsize=1)
def heavy_week_lines() -> dict[str, dict[str, float]]:
    """{county: {month: mm}}, the rain in a week that is heavy for that county and month."""
    if not HEAVY_RAIN_7D_PATH.exists():
        return {}
    return json.loads(HEAVY_RAIN_7D_PATH.read_text())


def heavy_week_line(county: str, month: int) -> float | None:
    return heavy_week_lines().get(county, {}).get(str(month))


def _local_rain(place: Place, days):
    """(day, mm, line) of the first of `days` whose forecast rain so far is heavy for the county."""
    county = place.county or place.name
    for day in days:
        mm = day[4] if len(day) > 4 else None
        line = heavy_week_line(county, day[0].month)
        if mm is not None and line is not None and mm > line:
            return day[0], mm, line
    return None


def local_signal(place: Place, days):
    """The local fact a Warning needs, as (day, reason text), or None.

    Heavy rain for the county today. A river rising on the county's
    largest river goes here as well once discharge is adopted
    (riskengine/ml/README.md, "River discharge").
    """
    heavy = _local_rain(place, days)
    if heavy:
        day, mm, line = heavy
        county = place.county or place.name
        return day, (
            f"{mm:.0f} mm of rain forecast by {_day(day)}, heavy for {county} in {day:%B} "
            f"(above {line:.0f} mm in a week)"
        )
    return None


def decide(place: Place, today: dt.date) -> Decision:
    """The alert `place` should have today, if any, and why."""
    soon = [d for d in place.week if d[0] <= today + dt.timedelta(days=SOON_DAYS)]
    rising = place.change_7d is not None and place.change_7d >= RISING
    very_high_now = place.category == "very_high"

    def first(days, test):
        return next((d for d in days if test(d)), None)

    vh_soon = first(soon, lambda d: d[1] == "very_high")
    vh_week = first(place.week, lambda d: d[1] == "very_high")
    heavy_week = first(place.week, lambda d: (d[3] or 0) >= HEAVY_RAIN_MM)
    # The forecast's rain figures run from the issue to each day, so the
    # first day one crosses its line is the day the heavy rain falls.
    local_soon = local_signal(place, soon)
    local_week = local_signal(place, place.week)

    reasons: list[dict] = []
    level = None
    basis = "observed"
    onset = today

    def because(kind, text):
        reasons.append({"kind": kind, "text": text})

    if very_high_now and rising and local_week:
        level = Level.WARNING
        because("very_high_rising", f"Very High today and up {place.change_7d:.1f}× in 7 days")
        because("heavy_rain_local", local_week[1])
    elif vh_soon and local_soon:
        level, basis, onset = Level.WARNING, "forecast", min(vh_soon[0], local_soon[0])
        because("forecast_very_high", f"Forecast to reach Very High by {_day(vh_soon[0])}")
        because("heavy_rain_local", local_soon[1])
    elif very_high_now:
        level = Level.WATCH
        because("very_high", "Very High today")
    elif vh_week:
        level, basis, onset = Level.WATCH, "forecast", vh_week[0]
        because("forecast_very_high", f"Forecast to reach Very High by {_day(vh_week[0])}")
    elif place.category == "high" and rising:
        level = Level.ADVISORY
        because("rising", f"High and up {place.change_7d:.1f}× in 7 days")
    elif heavy_week:
        level, basis, onset = Level.ADVISORY, "forecast", heavy_week[0]
        because("heavy_rain", f"{heavy_week[3]:.0f} mm of rain forecast by {_day(heavy_week[0])}")

    # The heaviest rain is worth saying under any level that did not name it.
    if level and heavy_week and not any(r["kind"] == "heavy_rain" for r in reasons):
        because("heavy_rain", f"{heavy_week[3]:.0f} mm of rain forecast by {_day(heavy_week[0])}")
    return Decision(level=level, basis=basis, reasons=reasons, onset=onset)


def stronger(new: str, old: str) -> bool:
    return LEVEL_RANK[new] > LEVEL_RANK[old]


def place_name(place: Place) -> str:
    """ "Nyando (Kisumu County)" or "Garissa County"."""
    if place.level == "sub_county":
        return f"{place.name} ({place.county} County)"
    return f"{place.name} County"


def wording(level: str, place: Place, decision: Decision) -> dict:
    """The headline, description and instruction of an alert."""
    label = Level(level).label
    where = place_name(place)
    facts = "; ".join(r["text"] for r in decision.reasons)
    chance = (
        f" The model puts the chance of a large flood at about {place.times_usual:.1f} times "
        "the usual for this place and time of year."
        if place.times_usual is not None
        else ""
    )
    return {
        "headline": f"{label} for {where}",
        "description": f"{facts}.{chance} {DISCLAIMER}",
        "instruction": INSTRUCTIONS[level],
    }
