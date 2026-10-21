"""When a region gets an alert, and at what level.

Fixed, readable rules over what the risk engine has already worked out for
each region (riskengine/situation.py): today's category, the change over
the last week, and the 7-day forecast with its wettest day.

  Warning   Very High today and rising, or
            forecast Very High within 3 days with a day of HEAVY_RAIN_MM
            or more forecast in those 3 days
  Watch     Very High today, or forecast Very High within the 7 days
  Advisory  High today and rising, or a day of HEAVY_RAIN_MM or more
            forecast in the 7 days

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
from dataclasses import dataclass

from riskengine.briefing import Place

from .models import LEVEL_RANK, Level

HEAVY_RAIN_MM = 50.0
RISING = 0.2
SOON_DAYS = 3

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


def decide(place: Place, today: dt.date) -> Decision:
    """The alert `place` should have today, if any, and why."""
    soon = [d for d in place.week if d[0] <= today + dt.timedelta(days=SOON_DAYS)]
    rising = place.change_7d is not None and place.change_7d >= RISING
    very_high_now = place.category == "very_high"

    def first(days, test):
        return next((d for d in days if test(d)), None)

    vh_soon = first(soon, lambda d: d[1] == "very_high")
    vh_week = first(place.week, lambda d: d[1] == "very_high")
    heavy_soon = first(soon, lambda d: (d[3] or 0) >= HEAVY_RAIN_MM)
    heavy_week = first(place.week, lambda d: (d[3] or 0) >= HEAVY_RAIN_MM)
    # The forecast's wettest-day figure runs from the issue to each day, so
    # the first day it crosses the threshold is the day the heavy rain falls.

    reasons: list[dict] = []
    level = None
    basis = "observed"
    onset = today

    def because(kind, text):
        reasons.append({"kind": kind, "text": text})

    if very_high_now and rising:
        level = Level.WARNING
        because("very_high_rising", f"Very High today and up {place.change_7d:.1f}× in 7 days")
    elif vh_soon and heavy_soon:
        level, basis, onset = Level.WARNING, "forecast", min(vh_soon[0], heavy_soon[0])
        because("forecast_very_high", f"Forecast to reach Very High by {_day(vh_soon[0])}")
        because("heavy_rain", f"{heavy_soon[3]:.0f} mm of rain forecast by {_day(heavy_soon[0])}")
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
