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

Each model's settings are searched over a small grid with `GridSearchCV`
on the same year folds (see "The settings search" below), and the searched
settings are used only where they beat the fixed ones on held-out years.

The winner's probabilities are then calibrated (Platt or isotonic, see
`calibration.py`) if that lowers the held-out Brier score. This targets the
overconfidence in the top band noted below.

The figures below are the second 6 October 2026 run on Google Colab,
through `notebooks/train_flood_model.ipynb`: 14,523 county-months from 1996
to 2021, of which 1,406 (9.7%) have a recorded flood. The saved model and
`metrics.json` are the files that run downloaded. It is the first run with
the settings search. The search kept the Random Forest and Logistic
Regression at their fixed settings, so their scores are the same as in the
2 and 5 October runs on a laptop and the first Colab run, and the saved
model gives the same probability for every training row. Gradient Boosting
changed settings and scores higher than before.

The notebook is committed with its outputs, so the charts can be read on
GitHub without running anything. The same charts are in `docs/figures/`.

| Model | ROC AUC | Average precision | Brier |
|---|---|---|---|
| Random Forest (selected) | 0.787 | 0.254 | 0.0798 |
| Logistic Regression | 0.766 | 0.239 | 0.0830 |
| Gradient Boosting | 0.760 | 0.232 | 0.0811 |
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
| Gradient Boosting | 0.298 | 0.152 |
| Season only | 0.202 | 0.105 |

So the Random Forest's lead over the season-only model is 1.5 times when
folds are averaged, against 2.1 times pooled. Quote both. Averaged over
folds the tuned Gradient Boosting is level with the Random Forest, with a
much wider spread: it wins three folds and loses three.

Treating a probability above a band edge as a warning, on held-out years:

| Warn above | Floods caught | Floods missed | False alarms | Recall | Precision |
|---|---|---|---|---|---|
| 5% (Moderate and up) | 1,243 | 163 | 6,232 | 88.4% | 16.6% |
| 10% (High and up) | 1,008 | 398 | 3,689 | 71.7% | 21.5% |
| 20% (Very High) | 585 | 821 | 1,628 | 41.6% | 26.4% |

Some false alarms are floods nobody recorded, and some "floods" are
counties a circle touched that stayed dry, so the precision is a floor.

### The settings search

`PARAM_GRIDS` in `train.py` lists what is searched. Every grid holds the
fixed setting, so the search can also conclude that nothing beats it.
`GridSearchCV` proposes the best setting by average precision on the year
folds. Because it cannot refit the county rainfall normals inside each
fold, its proposal is then scored again with the normals refitted per fold,
next to the fixed setting, and adopted only if it scores higher.

| Model | Grid | Fixed | Searched | Average precision, fixed to searched | Result |
|---|---|---|---|---|---|
| Logistic Regression | `C`: 0.01, 0.1, 1, 10 | 1 | 1 | 0.239 | Fixed setting confirmed |
| Random Forest | `min_samples_leaf`: 5, 20, 50; `max_features`: sqrt, 0.5 | 20, sqrt | 20, sqrt | 0.254 | Fixed setting confirmed |
| Gradient Boosting | `max_depth`: 2, 3; `learning_rate`: 0.02, 0.05, 0.1 | 3, 0.05 | 2, 0.02 | 0.215 to 0.232 | Adopted |

The search did not change which model is selected. It confirmed the Random
Forest's settings and moved Gradient Boosting to shallower, slower trees,
which still leaves it third on the pooled scores. `metrics.json` holds this
under `tuning`, and the same search repeated on 1996 to 2016 under
`final_test.tuning`. `train_model --no-tune` skips it.

### The final test: 2017 to 2021

The figures above come from the same folds that picked the model, which
flatters them. As a check, the years from 2017 on were set aside, the
settings, the model and its calibration were chosen again on 1996 to 2016
alone, and 2017 to 2021 were scored once (2,679 county-months, 181 floods,
6.8%). The boundary is `FINAL_TEST_FROM` in `train.py`. It was fixed before
the result was seen and must not be moved to get a better number.

This table was first produced on 5 October without the settings search. It
was produced again on 6 October with it, which is a second look at the test
years. The boundary and the procedure for choosing did not change between
the two; the earlier figures are in the git history.

| Model | ROC AUC | Average precision | Brier |
|---|---|---|---|
| Gradient Boosting, sigmoid-calibrated (what 1996–2016 chose) | 0.801 | 0.191 | 0.0696 |
| Logistic Regression (not chosen) | 0.852 | 0.270 | 0.0650 |
| Random Forest (not chosen) | 0.782 | 0.186 | 0.0820 |
| Season only | 0.694 | 0.177 | 0.0626 |
| Always predict the 1996–2016 base rate | 0.500 | 0.068 | 0.0643 |

This is a weaker result than the cross-validation, and it should be quoted
alongside it:

- **The choice of model is not stable.** 1996 to 2016 picks Gradient
  Boosting, all years pick Random Forest, and on 2017 to 2021 the Logistic
  Regression does best. The three are too close, and the years too few, to
  say one is better.
- **The chosen model barely beat the calendar.** Its average precision is
  1.08 times the season-only model's (0.191 against 0.177). Without the
  settings search it was 0.87 times. It ranks well (ROC AUC 0.80) and its
  bands are in the right order: 0.4%, 1.8%, 5.3% and 17.6% of Low,
  Moderate, High and Very High county-months flooded.
- **The probabilities are too high.** Floods were recorded in 6.8% of these
  county-months against 10.3% before, and every model scores a worse Brier
  than always saying the earlier base rate. Very High said 33% where 18%
  flooded.

The saved model is still the Random Forest fitted on every year. Switching
to the Logistic Regression because it won here would turn the test into a
second round of model selection, so that is a decision to take with the
supervisor and to state openly, not one this result makes by itself.

### Where it is wrong

Treating High and Very High as a warning (above 10%), on held-out years,
`error_breakdown` in `metrics.json` splits the 398 missed floods and 3,689
false alarms by calendar month and by county.

- **June is missed almost entirely.** 86 of the 88 June floods had no
  warning. The model has learned the long rains as March to May and treats
  June as dry season, so a flood that runs on into June is not seen. April
  and May miss 15% each; August and September miss about half of a small
  number.
- **A third of false alarms are in April and May** (1,272 of 3,689). In the
  long rains most counties are above 10% whether or not a flood is recorded.
- **By county, misses are highest in Kakamega, Nairobi, Kilifi, Lamu and
  Kwale** (44% to 49% of their recorded floods) and lowest in Kirinyaga,
  Baringo and Kisumu (about 15%). Nairobi and the coast flood from short,
  intense rain that a 30-day total at one point describes poorly.
- **False alarms are highest in the wet western highlands** (Kericho, Uasin
  Gishu, Siaya, Nandi, Bungoma, Trans Nzoia: a warning in about a third of
  all months with no flood recorded).

### What it leans on

`permutation_importance` in `metrics.json` is how far held-out average
precision falls when one input is shuffled, measured on the years each fold
never saw and averaged over the six folds.

| Input | Fall in average precision | Standard deviation over folds |
|---|---|---|
| `month_sin` | 0.092 | 0.072 |
| `rain_30d_ratio` | 0.038 | 0.030 |
| `rain_prev_30d` | 0.031 | 0.013 |
| `oni` | 0.028 | 0.061 |
| `month_cos` | 0.019 | 0.016 |
| `dmi_short_rains` | 0.013 | 0.019 |
| `elevation` | 0.011 | 0.012 |
| `log_area` | 0.008 | 0.008 |
| `rain_7d_max` | 0.007 | 0.012 |
| `rain_30d` | 0.005 | 0.023 |
| `slope` | 0.004 | 0.004 |
| `oni_short_rains` | -0.005 | 0.009 |
| `dmi` | -0.018 | 0.043 |

Read it this way:

- **The time of year matters most**, then rainfall against the county's
  normal and how wet the month before was. The three direct rainfall inputs
  carry much the same information, so each one's fall is small: shuffling
  `rain_30d` leaves `rain_30d_ratio` intact.
- **The ocean indices are unstable.** ONI helps a lot in some folds and
  hurts in others (the standard deviation is twice the mean). The plain DMI
  has a negative mean: on unseen years the model did slightly better
  without it. Its short-rains version is the one that helps. The forest
  splits on both indices more than on anything else (they top its built-in
  impurity ranking), which is the model fitting year-to-year patterns that
  do not carry over, and is why this table and not that ranking is quoted.
- **Terrain adds little**, as expected when every row of a county has the
  same elevation and slope.

The notebook also draws partial dependence for the four most important
inputs (`docs/figures/10_partial_dependence.png`). The direction is the
expected one in each: probability rises towards April, with rainfall above
normal, with a wetter previous month, and on the El Niño side of ONI.

SHAP, named in the proposal, was not used. Permutation importance answers
the same question (which inputs the predictions depend on), needs no
further dependency, and is measured on held-out years, which SHAP values
of a fitted model are not.

`artifacts/metrics.json` holds the current figures: `final_test` for the
final test table, `folds` and
`fold_summary` for the fold table, `threshold_errors` for the warning table,
`tuning`, `error_breakdown` and `permutation_importance` for the three
sections above, and
`provenance` for the commit, the SHA-256 of the training set, the seed and
the scikit-learn version the run used. The commit is empty for a Colab run,
because the uploaded bundle is not a git checkout. The 6 October bundle was
built from commit `51e4de2`.

## Checking against satellite floods

`validate_unosat` scores the model on months it never saw (2023 and 2024)
against flood extents UNOSAT mapped from satellite, which say where water
actually was rather than placing a circle. The products are listed in
`riskengine/ml/unosat_events.json` and documented in
`docs/DATA_DICTIONARY.md`, section 5.

```bash
docker compose run --rm web python manage.py validate_unosat riskengine/ml/unosat_events.json
```

A county counts as flooded when at least 5 km² of it is inside the mapped
flood water (`--min-km2`). Run on 5 October 2026 against
`flood-random-forest-20261005`, with the per-county rows kept in
`artifacts/unosat_validation.csv`. The Colab-trained
`flood-random-forest-20261006` gives the same probabilities, so the run was
not repeated:

| Event | Counties flooded | ROC AUC | Average precision |
|---|---|---|---|
| Short rains, 19 to 28 November 2023 | 31 of 47 | 0.744 | 0.886 |
| Long rains, 24 to 28 April 2024 | 26 of 47 | 0.430 | 0.499 |
| Both | 57 of 94 | 0.616 | 0.740 |

| Band the model gave | County-events | Flooded |
|---|---|---|
| Low | 0 | n/a |
| Moderate | 9 | 56% |
| High | 33 | 48% |
| Very High | 52 | 69% |

Read it this way:

- **November 2023 was ranked well.** The counties the model scored highest
  (Garissa, Tana River, Wajir, Isiolo, Mandera) are where the water was.
- **April 2024 was ranked worse than chance.** The model gave its highest
  probabilities to wet highland counties (Nyandarua, Taita Taveta, Bomet,
  Makueni) where the satellite saw no flood water, and lower ones to
  Turkana, Garissa and Kisumu, where it did. The result holds at 25, 50
  and 100 km² as well (ROC AUC 0.32 to 0.38).
- **Only the ranking can be compared.** "At least 5 km² of water seen" is
  true of 61% of these county-events, where "a large flood recorded" is
  true of 10% of training rows. They are different events, so the Brier
  score the command prints means nothing here.
- **It is 94 county-events from two floods.** Indicative, not conclusive.

Why April 2024 went wrong has not been tested. Three things could each
contribute. The sensor (VIIRS, 375 m pixels) sees wide sheets of water on
arid lowlands far better than fast river and urban flooding in the
highlands, which is what hit Nairobi and Mai Mahiu that month. Cloud hides
the highlands in the rainy season, and a county under cloud is counted as
dry. And the water mapped in Turkana, Marsabit and Baringo is partly lake
shore beyond the reference shoreline, not this event.

The permanent-water layer is already removed by UNOSAT (about 100 km² of
overlap in either extent).

### Sub-counties

The model is trained on counties and applied to sub-counties, each scored
from its own rainfall and terrain against its county's normal. The same two
extents are the only check on that:

```bash
docker compose run --rm web python manage.py validate_unosat riskengine/ml/unosat_events.json --level sub_county
```

Run on 6 October 2026, same 5 km² threshold, rows kept in
`artifacts/unosat_validation_sub_county.csv`:

| Event | Sub-counties flooded | ROC AUC | Average precision |
|---|---|---|---|
| Short rains, November 2023 | 89 of 290 | 0.767 | 0.689 |
| Long rains, April 2024 | 55 of 290 | 0.491 | 0.176 |
| Both | 144 of 580 | 0.654 | 0.465 |
| Ranked within their own county, both events | 43 county-events | 0.461 | n/a |

| Band the model gave | Sub-county-events | Flooded |
|---|---|---|
| Low | 0 | n/a |
| Moderate | 51 | 10% |
| High | 227 | 19% |
| Very High | 302 | 32% |

- **Across the country, sub-counties are ranked about as well as
  counties** (0.654 against 0.616), with the same split between the two
  events.
- **Within a county they are not ranked at all.** The last row takes each
  county where some sub-counties flooded and some did not, and asks whether
  the flooded ones were scored higher. 0.46 is no better than a coin. A
  sub-county's score is right about as often as its county's score is, and
  the difference between two sub-counties of one county carries no
  information.
- That is what the inputs would predict. Sub-counties of one county share
  the season, the ocean indices, the county normal and the county area, and
  ERA5 rainfall at about 25 km is nearly the same across a small county.
  What separates them is terrain at one point, which the model barely uses.
- The same cautions apply as for counties: two events, a coarse sensor, and
  cloud counted as dry. A sub-county is also smaller than many flood
  patches a 375 m sensor can map, and 5 km² is a large share of an urban one.

The dashboard should be read accordingly: the sub-county view shows where a
county's risk sits on the map, not which of its sub-counties is worse.

## Known limits

- **Skill on later years is weaker than cross-validation says.** See "The
  final test" above. Treat the bands as an ordering of regions, not as
  probabilities to quote.

- **Labels are coarse.** The flood archive gives a centroid and an area, not
  an outline, so each flood is placed as a circle. Some counties inside the
  circle did not flood.
- **Labels depend on reporting.** The archive has almost no Kenyan floods
  before 1996 and under-records 1997–98. The model inherits that.
- **Trained on counties, applied to sub-counties.** A sub-county is scored
  from its own rainfall and terrain against its county's normal rainfall.
  Against satellite extents this does not rank sub-counties within a county
  better than chance (see "Sub-counties" above).
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
