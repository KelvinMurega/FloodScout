# The flood model

Kept as its own subpackage so `riskengine/models.py` (the Django ORM model)
and the machine-learning code never share a filename.

## What it predicts

The probability that the Dartmouth Flood Observatory would record a **large
flood in a region in the 30 days ending on a date**. That probability is
`RiskScore.predicted_value`. It is banded into the four risk categories:

| Category | Probability | Floods recorded on held-out years |
|---|---|---|
| Low | up to 5% | 2.3% |
| Moderate | 5–10% | 8.5% |
| High | 10–20% | 17.0% |
| Very High | above 20% | 26.4% |

A large flood is recorded in about 10% of county-months, so the bands sit
around that base rate. "Very High" means roughly a one-in-four chance, nearly
three times the usual. It does not mean a flood is expected.

## What it learns from

One row per county per month, January 1996 to September 2021. Sources are
in `docs/DATA_DICTIONARY.md`.

Years before 1996 are left out. The flood archive records almost no Kenyan
floods before then, so a wet month in 1990 is labelled "no flood" because
nobody reported one, not because none happened. Those rows taught the
model that rain matters less than it does.

| Input | Meaning |
|---|---|
| `rain_30d` | Rainfall over the 30 days, mm |
| `rain_7d_max` | Wettest 7 consecutive days in those 30, mm |
| `rain_prev_30d` | Rainfall over the 30 days before, mm |
| `rain_30d_ratio` | `rain_30d` over the county's normal for that month |
| `oni` | El Niño index (ONI) as published by that date |
| `dmi` | Indian Ocean Dipole index (DMI) as published by that date |
| `oni_short_rains`, `dmi_short_rains` | The same indices in October to December, zero otherwise |
| `month_sin`, `month_cos` | Time of year |
| `elevation`, `slope` | Terrain at the region's representative point |
| `log_area` | Log of the county's area in km² (a control, see below) |

Both ocean indices drive the October to December short rains far more than
the long rains. A linear model cannot learn "only in these months" from the
index and the month separately, so the short-rains versions carry it.

`log_area` is not a cause of flooding. It corrects for how the labels are
made: a flood is placed as a circle, so a large county is touched by more
circles than a small one. Sub-counties are given their county's area,
because that is the scale the model was trained at.

Rainfall is measured one of two ways, chosen when the training set is
built:

| `--rain-source` | What it is | Scoring then averages |
|---|---|---|
| `open-meteo` (default) | ERA5 at one representative point per county | 1 point per region |
| `earth-engine` | ERA5-Land averaged over the whole county | 5 points per region |

The choice is recorded in `training_set.meta.json` and in the trained
model, and `seed_rainfall` and `flood_outlook` read it from the model, so
rainfall is always measured the way the model was trained.

`features.py` builds this row and is the only place that does, for training,
for `score_regions` and for `flood_outlook`.

## How well it does

Three models are compared by cross-validation that holds out whole years,
because rows from the same month are near-copies of each other and a random
split would flatter all three. Inside that cross-validation the normal
rainfall behind `rain_30d_ratio` is recomputed from the training years
only, so a held-out year never contributes to its own normal.

They are scored against two baselines: always predicting the base rate,
and a season-only model that knows nothing but the month. Beating the
second is what shows rainfall, the ocean indices and terrain add something
beyond "it is the rainy season". `metrics.json` reports both as
`lift_over_base_rate` and `lift_over_season_only`.

The winner's probabilities are then calibrated (Platt or isotonic, see
`calibration.py`) if that lowers the held-out Brier score. This targets the
overconfidence in the top band noted below.

The figures below are the 5 October 2026 run: 14,523 county-months from
1996 to 2021, of which 1,406 (9.7%) have a recorded flood. It repeats the
2 October run on the same data and seed, and its scores are identical.

| Model | ROC AUC | Average precision | Brier |
|---|---|---|---|
| Random Forest (selected) | 0.787 | 0.254 | 0.0798 |
| Logistic Regression | 0.766 | 0.239 | 0.0830 |
| Gradient Boosting | 0.750 | 0.215 | 0.0856 |
| Season only | 0.565 | 0.124 | 0.0873 |
| Always predict the base rate | 0.500 | 0.097 | 0.0874 |

Read this as modest skill. The model's average precision is 2.6 times the
base rate and 2.1 times the season-only model, and a higher band always saw
more floods than the one below it. It is not a precise forecast. Neither
calibration method lowered the held-out Brier score, so none is applied, and
above 20% the model is still overconfident (it says 31% on average where 26%
flooded).

The run before it (1 October 2026) used 1985 to 2021, nine inputs and
normals computed over all years. Logistic Regression won it with ROC AUC
0.726 and average precision 0.160 against a base rate of 0.071.

Those figures pool every held-out row. Fold by fold the result moves a
lot with which years are held out:

| Model | Average precision, mean of six folds | Standard deviation |
|---|---|---|
| Random Forest | 0.297 | 0.098 |
| Logistic Regression | 0.266 | 0.118 |
| Gradient Boosting | 0.239 | 0.073 |
| Season only | 0.202 | 0.105 |

So the Random Forest's lead over the season-only model is 1.5 times when
folds are averaged, against 2.1 times pooled. Quote both.

Treating a probability above a band edge as a warning, on held-out years:

| Warn above | Floods caught | Floods missed | False alarms | Recall | Precision |
|---|---|---|---|---|---|
| 5% (Moderate and up) | 1,243 | 163 | 6,232 | 88.4% | 16.6% |
| 10% (High and up) | 1,008 | 398 | 3,689 | 71.7% | 21.5% |
| 20% (Very High) | 585 | 821 | 1,628 | 41.6% | 26.4% |

Some false alarms are floods nobody recorded, and some "floods" are
counties a circle touched that stayed dry, so the precision is a floor.

`artifacts/metrics.json` holds the current figures: `folds` and
`fold_summary` for the first table, `threshold_errors` for the second, and
`provenance` for the commit, the SHA-256 of the training set, the seed and
the scikit-learn version the run used.

## Checking against satellite floods

`validate_unosat` scores the model on months it never saw (2023 and 2024)
against flood extents UNOSAT mapped from satellite, which say where water
actually was rather than placing a circle. Download the products from
UNOSAT's page on the Humanitarian Data Exchange, list them in a JSON
manifest (the format is in the command's docstring), then:

```bash
docker compose run --rm web python manage.py validate_unosat data/external/unosat/events.json
```

Many UNOSAT products cover a few counties, not all of Kenya. List only the
counties each product analysed under `covers`, so a county nobody mapped
is not counted as dry. With a few events this is a few dozen to a hundred
county-events: indicative, not conclusive.

## Known limits

- **Labels are coarse.** The flood archive gives a centroid and an area, not
  an outline, so each flood is placed as a circle. Some counties inside the
  circle did not flood.
- **Labels depend on reporting.** The archive has almost no Kenyan floods
  before 1996 and under-records 1997–98. The model inherits that.
- **Trained on counties, applied to sub-counties.** A sub-county is scored
  from its own rainfall and terrain against its county's normal rainfall.
  That has not been validated at sub-county scale.
- **Drainage is measured but not used yet.** `seed_drainage` fills river
  distance, flood-zone share and built-up share for every region, and they
  were tried as inputs on 1 October 2026. Held-out average precision moved
  from 0.160 to 0.164, and the model learned both shares backwards (more
  built-up and more flood-zone meant less flood). The cause is the labels.
  A flood is placed as a circle, so a large county is hit more often than a
  small one, and the built-up counties (Mombasa, Nairobi, Vihiga, Kisii)
  are the smallest. Adding county area as a control flipped built-up to
  the expected sign, which confirms it. Used as they stand, the inputs
  would lower the score of the places that drain worst, so they are left
  out. `log_area` is now in the model as that control; once retrained,
  try the drainage inputs again and check their coefficients have the
  expected sign before keeping them. Using them properly needs labels that
  say where inside a county it flooded.
- **The biggest remaining gain is better labels.** The Global Flood
  Database (`GLOBAL_FLOOD_DB/MODIS_EVENTS/V1` on Earth Engine) has the
  mapped extent of each DFO flood from 2000 to 2018. Labelling a county
  only where that extent actually touched it, instead of the whole circle,
  would fix the coarseness above at the source.
- **Severity classes are not modelled.** DFO grades floods 1, 1.5 and 2, but
  only three Kenyan events are class 2. The model predicts whether a large
  flood is recorded, not how bad.

## Running it

```bash
docker compose run --rm web python manage.py build_training_set   # downloads and caches the data
#   or, county-mean rainfall. Once: set EE_PROJECT_ID in .env and run
#   docker compose run --rm web earthengine authenticate --auth_mode=notebook
#   docker compose run --rm web python manage.py build_training_set --rain-source earth-engine
docker compose run --rm web python manage.py train_model          # or the Colab notebook
docker compose run --rm web python manage.py seed_rainfall        # 61 days, what the model reads
docker compose run --rm web python manage.py score_regions        # today, into RiskScore
docker compose run --rm web python manage.py flood_outlook        # the months ahead, into RiskOutlook
```

To train on Google Colab instead, open `notebooks/train_flood_model.ipynb`
and follow it. It runs the same `train.py` and gives back the two files that
go in `artifacts/`.

`infer()` loads `artifacts/flood_model.joblib` when it exists and falls back
to the old heuristic when it does not. `RiskScore.model_version` says which
one produced a row.

## The seasonal outlook

`flood_outlook` scores months that have not happened yet by feeding the
model the ECMWF seasonal rainfall forecast, one ensemble member at a time,
and reporting the mean and spread. Two cautions on top of the ones above:

- The forecast rainfall is used raw. It is a different model from the ERA5
  record the flood model was trained on, and no past forecasts are available
  to measure and remove the difference between them. If the forecast model
  runs wetter than ERA5, the probabilities are too high.
- ONI and DMI for future months are today's values carried forward.

Its results are stored in `RiskOutlook`, not `RiskScore`, so a
forecast-driven score can never become the dashboard's "latest". The
dashboard shows them only when a month is picked on the period bar, labels
them as an outlook, and gives no relocation advice from them. Run it once
per level:

```bash
docker compose run --rm web python manage.py flood_outlook
docker compose run --rm web python manage.py flood_outlook --level sub_county
```
