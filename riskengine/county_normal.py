"""Would a county's own normal cut the map's false alarms? A replay, nothing live.

The map counts a county as warned at High, a chance above 10%. That line
is the same for all of Kenya, and the model's chance runs with the season:
in the long rains most counties are above it whether or not a flood is
recorded, and the wet western highlands (Kericho, Uasin Gishu, Siaya,
Nandi, Bungoma, Trans Nzoia) are above it in about a third of the months
with no flood (riskengine/ml/README.md, "Where it is wrong"). The live
record shows the same: in October 2026, 45 of 47 counties were warned.

So the question is whether a warning should also need the chance to be
unusual for that county in that calendar month: above the county's own
median, 75th or 90th percentile of the chances the model gave it in that
month in other years. Kericho in April would then need a wetter April
than Kericho usually has.

How it is replayed, fixed before it was run (15 November 2026):

* Scores are the held-out chances alerts/backtest.py works out: each
  county-month scored by models that never saw its year, averaged over
  train.SEEDS. Rain is what fell; that does not flatter these candidates
  against each other, as none of them reads the forecast.
* A county's own line for a year is worked out from the other years only,
  so a flood year does not raise its own bar.
* 1996 to 2016 only. 2017 to 2021 is the model's final test and is closed.
* The candidates are CANDIDATES below, and the one chosen is the best hit
  rate that still catches KEEP_CAUGHT of the floods today's line catches.
  Leaving alerts out at random keeps the hit rate where it was, so a
  higher hit rate is the evidence that a line knows which alerts to drop.
* The dry gate is not replayed. It lowers some categories on dry windows,
  for every candidate alike.

Result, run once on 15 November 2026 with seeds 0 to 4: not adopted.

  Line                     Warnings  Caught  False alarms  Hit rate
  Today's, above 10%         4,273     855         3,418     0.200
  Very High, above 20%       2,177     584         1,593     0.268
  Own median as well         3,850     778         3,072     0.202
  Own 75th percentile        2,631     518         2,113     0.197
  Own 90th percentile        1,488     257         1,231     0.173

The rule chose the median, and its hit rate is today's: it drops alerts no
better than at random, losing a flood for every four or five false alarms
it removes. The higher lines do worse than random, and the western
highlands and the short rains look the same. So the false alarms are not
wet counties sitting above a line set for all of Kenya: within a county, a
month that floods and one that does not get much the same chance. Only a
higher chance separates them (Very High: 0.268, and 0.389 in the long
rains, but it keeps 68% of the floods). That is what the alert rules
already do, with Watch at Very High; the cure for the map's High band is
better inputs and labels, not a different line.

`manage.py backtest_county_normal` runs it and writes the result beside
the alert backtest. A candidate that wins here is not switched on by this:
it changes what the map shows, so that is a separate change, made after
the result is read.
"""

from __future__ import annotations

import numpy as np

from riskengine.ml import train

HIGH, VERY_HIGH = train.PROBABILITY_BANDS[1], train.PROBABILITY_BANDS[2]

# The chosen line must still catch this share of the floods today's line catches.
KEEP_CAUGHT = 0.8

# Long rains, short rains, and the counties README.md names for false alarms.
LONG_RAINS = (3, 4, 5)
SHORT_RAINS = (10, 11, 12)
WESTERN_HIGHLANDS = ("Kericho", "Uasin Gishu", "Siaya", "Nandi", "Bungoma", "Trans Nzoia")

# name: (what it is, the county percentile it also needs, or None)
CANDIDATES = {
    "today": ("Today's line: High, above 10%", None),
    "very_high": ("Very High only, above 20%", None),
    "own_50": ("High and above the county's own median for the month", 50),
    "own_75": ("High and above the county's own 75th percentile for the month", 75),
    "own_90": ("High and above the county's own 90th percentile for the month", 90),
}


def own_line(
    probability: np.ndarray,
    counties: np.ndarray,
    months: np.ndarray,
    years: np.ndarray,
    percentile: float,
) -> np.ndarray:
    """Per row, the `percentile` of its county's chances in that calendar month in other years.

    NaN where the county has no other year for that month.
    """
    line = np.full(len(probability), np.nan)
    for county in np.unique(counties):
        for month in np.unique(months):
            rows = np.flatnonzero((counties == county) & (months == month))
            for year in np.unique(years[rows]):
                others = rows[years[rows] != year]
                if len(others):
                    line[rows[years[rows] == year]] = np.percentile(probability[others], percentile)
    return line


def warned(name: str, probability, counties, months, years) -> np.ndarray:
    """Which rows candidate `name` counts as a warning."""
    if name == "very_high":
        return probability > VERY_HIGH
    alarm = probability > HIGH
    percentile = CANDIDATES[name][1]
    if percentile is not None:
        line = own_line(probability, counties, months, years, percentile)
        alarm &= probability > np.nan_to_num(line, nan=-np.inf)
    return alarm


def score(alarm: np.ndarray, flooded: np.ndarray, rows: np.ndarray | None = None) -> dict:
    if rows is not None:
        alarm, flooded = alarm[rows], flooded[rows]
    caught = int((alarm & flooded).sum())
    alarms = int(alarm.sum())
    floods = int(flooded.sum())
    return {
        "warnings": alarms,
        "floods": floods,
        "caught": caught,
        "missed": floods - caught,
        "false_alarms": alarms - caught,
        "recall": round(caught / floods, 3) if floods else None,
        "hit_rate": round(caught / alarms, 3) if alarms else None,
    }


def choose(results: dict[str, dict]) -> str | None:
    """The best hit rate among the candidates keeping KEEP_CAUGHT of today's catches."""
    floor = KEEP_CAUGHT * results["today"]["caught"]
    eligible = [n for n, r in results.items() if r["caught"] >= floor and r["hit_rate"] is not None]
    return max(eligible, key=lambda n: results[n]["hit_rate"]) if eligible else None


def replay(probability, flooded, counties, months, years) -> dict:
    """Every candidate on 1996 to 2016, overall and where today's line fails most."""
    development = years < train.FINAL_TEST_FROM
    probability, flooded = probability[development], flooded[development]
    counties, months, years = counties[development], months[development], years[development]
    groups = {
        "all": np.ones(len(flooded), bool),
        "long_rains": np.isin(months, LONG_RAINS),
        "short_rains": np.isin(months, SHORT_RAINS),
        "western_highlands": np.isin(counties, WESTERN_HIGHLANDS),
    }
    results = {}
    for name in CANDIDATES:
        alarm = warned(name, probability, counties, months, years)
        dates = np.array([f"{y}-{m:02d}" for y, m in zip(years, months)])
        per_month = [int(alarm[dates == d].sum()) for d in np.unique(dates[alarm])]
        results[name] = {
            group: score(alarm, flooded, rows) for group, rows in groups.items()
        } | {"counties_per_warned_month": round(float(np.mean(per_month)), 1) if per_month else 0.0}
    return {
        "candidates": {name: text for name, (text, _p) in CANDIDATES.items()},
        "years": [int(years.min()), int(years.max())],
        "results": results,
        "chosen": choose({name: r["all"] for name, r in results.items()}),
        "keep_caught": KEEP_CAUGHT,
    }
