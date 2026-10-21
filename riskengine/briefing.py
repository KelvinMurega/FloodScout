"""The situation in a few sentences, and the places that stand out.

Reads what the rest of the system has already worked out (today's scores,
their change over the week, the 30-day rain against normal, the 7-day
forecast and the ocean indices) and says it in words, the way a duty
forecaster would open a briefing. The words come from fixed templates, not
a language model, so the same data always gives the same text and every
sentence can be traced to a number.

A place is a *hotspot* when any of these holds:

  forecast_very_high  the forecast reaches Very High in the coming week
                      where it is not Very High today
  heavy_rain          a forecast day of HEAVY_RAIN_MM or more: the Kenya
                      Meteorological Department uses 50 mm in 24 hours as
                      its flood early-warning proxy
  rising_fast         up by RISING_FAST times the usual chance in a week
  top                 among the highest scores today (TOP_N)

The alerts (riskengine/alerts.py, when built) apply their own, stricter
rules; a hotspot is something to look at, not a warning.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

HEAVY_RAIN_MM = 50.0
RISING = 0.2
RISING_FAST = 0.5
TOP_N = 3
ONI_PHASE = 0.5


@dataclass
class Place:
    """Everything the briefing knows about one region."""

    region: int
    name: str
    category: str
    times_usual: float | None
    change_7d: float | None = None
    rain_ratio: float | None = None
    # The forecast week: [(day, category, times_usual, wettest day mm)].
    week: list[tuple[dt.date, str, float | None, float | None]] = field(default_factory=list)

    @property
    def peak(self):
        """The forecast day with the highest score, or None."""
        known = [day for day in self.week if day[2] is not None]
        return max(known, key=lambda day: day[2]) if known else None

    @property
    def wettest(self):
        """(day, mm) of the wettest forecast day, or None."""
        known = [(day[0], day[3]) for day in self.week if day[3] is not None]
        return max(known, key=lambda item: item[1]) if known else None


def _day(date: dt.date) -> str:
    return f"{date:%A} {date.day} {date:%B}"


def _x(times: float | None) -> str:
    return "—" if times is None else f"{times:.1f}×"


def _list(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _count(n: int, unit: str) -> str:
    """'1 county', '3 counties', '1 sub-county'."""
    plural = {"county": "counties", "sub-county": "sub-counties"}.get(unit, unit + "s")
    return f"{n} {unit if n == 1 else plural}"


def hotspots(places: list[Place]) -> list[dict]:
    """The places that stand out, each with every reason it does, most urgent first."""
    ranked = sorted((p for p in places if p.times_usual is not None), key=lambda p: -p.times_usual)
    top = {p.region for p in ranked[:TOP_N]}
    found = []
    for place in places:
        reasons = []
        peak = place.peak
        if peak and peak[1] == "very_high" and place.category != "very_high":
            reasons.append(
                {
                    "kind": "forecast_very_high",
                    "text": f"Forecast to reach Very High ({_x(peak[2])}) by {_day(peak[0])}",
                }
            )
        wettest = place.wettest
        if wettest and wettest[1] >= HEAVY_RAIN_MM:
            reasons.append(
                {
                    "kind": "heavy_rain",
                    "text": f"{wettest[1]:.0f} mm of rain forecast on {_day(wettest[0])}",
                }
            )
        if place.change_7d is not None and place.change_7d >= RISING_FAST:
            reasons.append(
                {"kind": "rising_fast", "text": f"Up {place.change_7d:.1f}× in the last 7 days"}
            )
        if place.region in top:
            reasons.append(
                {"kind": "top", "text": f"Among the highest today ({_x(place.times_usual)})"}
            )
        if reasons:
            found.append(
                {
                    "region": place.region,
                    "name": place.name,
                    "category": place.category,
                    "times_usual": place.times_usual,
                    "reasons": reasons,
                }
            )
    urgency = ["heavy_rain", "forecast_very_high", "rising_fast", "top"]
    found.sort(
        key=lambda h: (
            min(urgency.index(r["kind"]) for r in h["reasons"]),
            -(h["times_usual"] or 0),
        )
    )
    return found


def briefing(
    places: list[Place], *, date: dt.date, area: str, unit: str, oni: float | None
) -> dict:
    """The briefing for `places` (the regions of `area`, each a `unit`) on `date`."""
    paragraphs = []
    if not places:
        return {
            "date": date.isoformat(),
            "headline": f"No scores for {area} yet.",
            "paragraphs": [],
            "hotspots": [],
        }

    by_category = {c: [p for p in places if p.category == c] for c in ("very_high", "high")}
    ranked = sorted((p for p in places if p.times_usual is not None), key=lambda p: -p.times_usual)
    very_high = len(by_category["very_high"])
    headline = (
        f"{_count(very_high, unit).capitalize()} of {len(places)} at Very High flood risk"
        if very_high
        else f"No {unit} at Very High flood risk"
    )

    top = ranked[:TOP_N]
    opening = (
        f"On {_day(date)}, {_count(very_high, unit)} in {area} "
        f"{'is' if very_high == 1 else 'are'} at Very High risk and "
        f"{_count(len(by_category['high']), unit)} at High."
    )
    if top:
        opening += (
            " The highest are " + _list([f"{p.name} ({_x(p.times_usual)})" for p in top]) + "."
        )
    paragraphs.append(opening)

    changes = [p for p in places if p.change_7d is not None]
    if changes:
        rising = sorted((p for p in changes if p.change_7d >= RISING), key=lambda p: -p.change_7d)
        falling = [p for p in changes if p.change_7d <= -RISING]
        if rising:
            trend = (
                f"Risk rose in {_count(len(rising), unit)} over the last 7 days, most in "
                f"{rising[0].name} (up {rising[0].change_7d:.1f}×)"
            )
            trend += f", and fell in {len(falling)}." if falling else "."
        elif falling:
            trend = (
                f"Risk fell in {_count(len(falling), unit)} over the last 7 days and rose in none."
            )
        else:
            trend = "Risk has held steady over the last 7 days."
        paragraphs.append(trend)

    forecast = [p for p in places if p.peak]
    if forecast:
        peak_vh = [p for p in forecast if any(day[1] == "very_high" for day in p.week)]
        sentence = (
            f"Over the next {len(forecast[0].week)} days the forecast puts "
            f"{_count(len(peak_vh), unit)} at Very High on at least one day, against {very_high} today."
        )
        risers = sorted(
            (p for p in forecast if p.times_usual is not None and p.peak[2] is not None),
            key=lambda p: -(p.peak[2] - p.times_usual),
        )
        if risers and risers[0].peak[2] - risers[0].times_usual >= RISING:
            p = risers[0]
            sentence += f" The biggest rise is expected in {p.name}, to {_x(p.peak[2])} by {_day(p.peak[0])}."
        paragraphs.append(sentence)

        wet = [p for p in forecast if p.wettest]
        if wet:
            wettest = max(wet, key=lambda p: p.wettest[1])
            heavy = [p for p in wet if p.wettest[1] >= HEAVY_RAIN_MM]
            rain = (
                f"The heaviest rain forecast is in {wettest.name}: {wettest.wettest[1]:.0f} mm "
                f"on {_day(wettest.wettest[0])}."
            )
            if heavy:
                rain += (
                    f" {_count(len(heavy), unit).capitalize()} {'has' if len(heavy) == 1 else 'have'} "
                    f"a forecast day of {HEAVY_RAIN_MM:.0f} mm or more, the level the Kenya "
                    "Meteorological Department treats as a flood warning sign."
                )
            else:
                rain += f" No {unit} has a forecast day of {HEAVY_RAIN_MM:.0f} mm or more."
            paragraphs.append(rain)

    ratios = [p for p in places if p.rain_ratio is not None]
    if ratios and oni is not None:
        dry = [p for p in ratios if p.rain_ratio < 0.8]
        if oni >= ONI_PHASE and len(dry) * 2 >= len(ratios):
            paragraphs.append(
                f"Most of this risk comes from El Niño conditions (ONI {oni:.1f}) in the rainy "
                f"season rather than from rain that has already fallen: {len(dry)} of "
                f"{len(ratios)} had less than 80% of their normal rain in the last 30 days. "
                "In past El Niño years, such as 1997 and 2023, the short rains brought "
                "widespread flooding."
            )
        elif oni >= ONI_PHASE:
            paragraphs.append(
                f"El Niño conditions (ONI {oni:.1f}) are adding to the risk across the country."
            )
        elif oni <= -ONI_PHASE:
            paragraphs.append(
                f"La Niña conditions (ONI {oni:.1f}) usually mean a drier short rains in Kenya, "
                "which is holding the risk down."
            )

    return {
        "date": date.isoformat(),
        "headline": headline,
        "paragraphs": paragraphs,
        "hotspots": hotspots(places),
    }
