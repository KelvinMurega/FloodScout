"""Replay alert rules over the training history, to choose between them.

The alert rules (alerts/rules.py) turn a region's score into Advisory,
Watch or Warning. On the 2015-16 blind test the model caught every flood
but put all 47 counties above the warning line in every rainy month, so a
rule that alerts on the score alone alerts on most of Kenya. This module
replays candidate rules over every county-month from 1996 to 2021 and
counts, for each, the alerts it would have issued and the recorded floods
that followed one.

Scores: each county-month gets the probability of a model that never saw
its year. 1996 to 2016 are scored by the year-grouped folds `compare_inputs`
uses; 2017 to 2021 by a model fitted on 1996 to 2016, as in the final test.
Every probability is the mean over train.SEEDS.

Rain: history has no stored forecasts, so the rain that actually fell in
the month stands in for the forecast. That is a perfect forecast, which
flatters every rule that uses rain, and the live rules will do worse.

"Heavy rain" is judged against each county's own climate: the month's
wettest 7 days (`rain_7d_max`) above that county's 90th percentile for the
calendar month. One line for all of Kenya would not work: the gridded
rainfall reaches 50 mm in a day on about 0.1% of days. "Wet ground" is
`soil_moisture_start` above the county's median for the month. Both are
worked out from 1996 to 2016 only, so the 2017-2021 check uses thresholds
fixed before it.

The rules are fixed in RULES before anything is run, and the winner is
chosen on 1996 to 2016 by `choose`; 2017 to 2021 is scored once, after.
`manage.py backtest_alerts` prints the result.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
from sklearn.base import clone
from sklearn.model_selection import GroupKFold

from riskengine.ml import experiments, train
from riskengine.ml.features import WINDOW_DAYS, soil_features
from riskengine.ml.infer import times_usual

from .rules import FLOOR_7D_MM, HEAVY_RAIN_MM, RISING

# The live rules' horizon for "rising": the score now against 7 days before.
RISING_DAYS = 7

HEAVY_PERCENTILE = 90
WET_PERCENTILE = 50

# Bars on the probability, as in train.PROBABILITY_BANDS (High from 10%,
# Very High from 20%) and the higher bar rule E tries.
HIGH, VERY_HIGH = train.PROBABILITY_BANDS[1], train.PROBABILITY_BANDS[2]
HIGHER_BAR = 0.30

# The rule chosen must still catch this share of the floods today's rules catch.
KEEP_CAUGHT = 0.6


@dataclass
class History:
    """One row per county-month, as in the training set."""

    counties: np.ndarray
    dates: np.ndarray  # month ends, as datetime.date
    years: np.ndarray
    months: np.ndarray
    flooded: np.ndarray  # bool: a recorded flood in the window
    probability: np.ndarray  # held-out, mean over seeds
    probability_before: np.ndarray  # the same model, RISING_DAYS earlier
    rain_7d_max: np.ndarray
    wettest_day: np.ndarray  # mm
    soil_start: np.ndarray  # m³/m³, NaN where not on file


def _shifted(data: train.TrainingSet, rainfall, days: int) -> train.TrainingSet:
    """`data` with its rainfall inputs for the window ending `days` earlier."""
    earlier = [str(dt.date.fromisoformat(date) - dt.timedelta(days=days)) for date in data.dates]
    moved = train.TrainingSet(
        features=data.features,
        X=data.X,
        y=data.y,
        years=data.years,
        months=data.months,
        counties=data.counties,
        dates=earlier,
        meta=data.meta,
    )
    return experiments.with_rainfall(moved, rainfall)


def held_out_scores(data: train.TrainingSet, rainfall, seeds=train.SEEDS):
    """(probability now, probability RISING_DAYS before) per row, from models blind to its year."""
    before = _shifted(data, rainfall, RISING_DAYS)
    development = np.flatnonzero(data.years < train.FINAL_TEST_FROM)
    test = np.flatnonzero(data.years >= train.FINAL_TEST_FROM)
    splits = [
        (development[fit], development[score])
        for fit, score in GroupKFold(train.CV_FOLDS).split(
            development, groups=data.years[development]
        )
    ]
    if len(test):
        splits.append((development, test))

    pipeline = train.candidates()[experiments.MODEL]
    seed_steps = train._random_steps(pipeline)
    now = np.zeros(len(data.y))
    then = np.zeros(len(data.y))
    for fit_rows, score_rows in splits:
        normals = train.county_normals(data, fit_rows)
        X_now = train.with_normals(data, normals)
        X_then = train.with_normals(before, normals)
        for seed in seeds:
            model = clone(pipeline).set_params(**{step: seed for step in seed_steps})
            model.fit(X_now[fit_rows], data.y[fit_rows])
            now[score_rows] += model.predict_proba(X_now[score_rows])[:, 1] / len(seeds)
            then[score_rows] += model.predict_proba(X_then[score_rows])[:, 1] / len(seeds)
    return now, then


def history(data: train.TrainingSet, rainfall, soil=None, seeds=train.SEEDS) -> History:
    """Everything the rules read, for every row of `data`."""
    now, then = held_out_scores(data, rainfall, seeds)
    dates = np.array([dt.date.fromisoformat(date) for date in data.dates])
    wettest = np.array(
        [
            max(
                (rainfall[county].get(end - dt.timedelta(days=back), 0.0) for back in range(WINDOW_DAYS)),
                default=np.nan,
            )
            for county, end in zip(data.counties, dates)
        ]
    )
    soil_start = np.full(len(dates), np.nan)
    if soil is not None:
        soil_start = np.array(
            [
                soil_features(soil.get(county, {}), end)["soil_moisture_start"]
                for county, end in zip(data.counties, dates)
            ]
        )
    return History(
        counties=data.counties,
        dates=dates,
        years=data.years,
        months=data.months,
        flooded=data.y == 1,
        probability=now,
        probability_before=then,
        rain_7d_max=data.X[:, data.column("rain_7d_max")],
        wettest_day=wettest,
        soil_start=soil_start,
    )


def county_month_percentile(h: History, values: np.ndarray, percentile: float, digits: int = 1) -> dict:
    """{county: {month: the `percentile` of `values`}} over 1996 to 2016 only."""
    out: dict[str, dict[int, float]] = {}
    development = h.years < train.FINAL_TEST_FROM
    for county in np.unique(h.counties):
        for month in range(1, 13):
            rows = development & (h.counties == county) & (h.months == month) & ~np.isnan(values)
            if rows.any():
                value = float(np.percentile(values[rows], percentile))
                out.setdefault(str(county), {})[month] = round(value, digits)
    return out


def _above(h: History, values: np.ndarray, thresholds: dict) -> np.ndarray:
    limit = np.array(
        [thresholds.get(county, {}).get(int(month), np.inf) for county, month in zip(h.counties, h.months)]
    )
    return values > limit


def signals(h: History) -> dict[str, np.ndarray]:
    """The yes/no facts the rules combine, per row."""
    usual = np.array([times_usual(p) or 0.0 for p in h.probability])
    usual_before = np.array([times_usual(p) or 0.0 for p in h.probability_before])
    heavy = county_month_percentile(h, h.rain_7d_max, HEAVY_PERCENTILE)
    wet = county_month_percentile(h, h.soil_start, WET_PERCENTILE, digits=4)
    return {
        "high": h.probability > HIGH,
        "very_high": h.probability > VERY_HIGH,
        "higher_bar": h.probability > HIGHER_BAR,
        "rising": np.round(usual - usual_before, 1) >= RISING,
        "heavy_day": h.wettest_day >= HEAVY_RAIN_MM,
        "heavy_local": _above(h, h.rain_7d_max, heavy),
        "floor": (h.rain_7d_max >= FLOOR_7D_MM) | (h.wettest_day >= HEAVY_RAIN_MM),
        "wet_ground": _above(h, h.soil_start, wet),
    }


# Fixed on 22 October 2026, before the backtest was run.
RULES = {
    "A": (
        "Today: any level (Very High, or High and rising, or a day of 50 mm)",
        lambda s: s["very_high"] | (s["high"] & s["rising"]) | s["heavy_day"],
    ),
    "B": ("Very High alone (today's Watch and Warning)", lambda s: s["very_high"]),
    "C": ("Very High and heavy rain for the county", lambda s: s["very_high"] & s["heavy_local"]),
    "D": (
        "C and the ground wetter than usual",
        lambda s: s["very_high"] & s["heavy_local"] & s["wet_ground"],
    ),
    "E": ("Above 30% and heavy rain for the county", lambda s: s["higher_bar"] & s["heavy_local"]),
    # Added 28 October 2026, after the rules above had been run: the live
    # Warning (C) fired on 25 mm over three days in Kajiado, so F is C with
    # an absolute floor. It is compared with C, not entered in `choose`, and
    # is the live Warning rule since (alerts/rules.py).
    "F": (
        "C and at least 30 mm in the week or a day of 50 mm",
        lambda s: s["very_high"] & s["heavy_local"] & s["floor"],
    ),
}

# The live Warning rule and the floor proposed for it, both scored on 2017-2021.
WARNING_RULES = ("C", "F")


def score(alerted: np.ndarray, h: History, rows: np.ndarray) -> dict:
    """How rule `alerted` did on `rows`."""
    alerted, flooded = alerted[rows], h.flooded[rows]
    caught = int((alerted & flooded).sum())
    dates = h.dates[rows]
    per_month = [int(alerted[dates == date].sum()) for date in np.unique(dates[alerted])]
    return {
        "alerts": int(alerted.sum()),
        "floods": int(flooded.sum()),
        "caught": caught,
        "caught_share": round(caught / flooded.sum(), 3) if flooded.any() else None,
        "hit_rate": round(caught / alerted.sum(), 3) if alerted.any() else None,
        "alert_months": len(per_month),
        "counties_per_alert_month": round(float(np.mean(per_month)), 1) if per_month else 0.0,
    }


def by_year(alerted: np.ndarray, h: History, rows: np.ndarray) -> dict[int, dict]:
    return {int(year): score(alerted, h, rows & (h.years == year)) for year in np.unique(h.years[rows])}


def choose(results: dict[str, dict]) -> str:
    """The rule with the best hit rate that keeps KEEP_CAUGHT of the floods rule A catches."""
    floor = KEEP_CAUGHT * results["A"]["caught"]
    eligible = [name for name, r in results.items() if r["caught"] >= floor and r["hit_rate"] is not None]
    return max(eligible, key=lambda name: results[name]["hit_rate"])


def backtest(h: History) -> dict:
    """Every rule on 1996-2016, the winner chosen there, then it and A on 2017-2021."""
    facts = signals(h)
    alerted = {name: rule(facts) for name, (_text, rule) in RULES.items()}
    development = h.years < train.FINAL_TEST_FROM
    test = ~development
    results = {name: score(alerted[name], h, development) for name in RULES}
    winner = choose({name: r for name, r in results.items() if name != "F"})
    checked = {}
    if test.any():
        checked = {
            name: score(alerted[name], h, test) for name in dict.fromkeys(("A", winner, *WARNING_RULES))
        }
    return {
        "rules": {name: text for name, (text, _rule) in RULES.items()},
        "development": results,
        "development_by_year": {name: by_year(alerted[name], h, development) for name in RULES},
        "winner": winner,
        "test": checked,
        "test_by_year": {name: by_year(alerted[name], h, test) for name in checked},
        "heavy_rain_7d_mm": county_month_percentile(h, h.rain_7d_max, HEAVY_PERCENTILE),
        "soil_with_record": int((~np.isnan(h.soil_start)).sum()),
    }
