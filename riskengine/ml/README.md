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

The dashboard does not show the probability as a percentage. It shows it as
a multiple of that usual chance ("about 3.2 times the usual chance"), which
the API serves as `times_usual` beside `predicted_value`. On the final test
below, the bands kept their order but the probabilities ran about twice too
high, so the ordering is what the evidence supports. `times_usual()` in
`infer.py` divides by `base_rate` from `metrics.json`.

## Explaining one prediction

`explain.py` splits a single prediction into a part per input, exactly:
a forest's probability is the mean of its trees' leaf values, and each
split on the path to a leaf moves the value by an amount charged to the
input it tested (Saabas, 2014; Palczewska et al., 2014). The base value is
the forest's before any split, about the share of flood months, so

    probability = base + the sum of the inputs' parts

to floating-point precision, which the tests check against `predict_proba`.
The parts are summed into seven groups a reader recognises (recent rain,
the month before, El Niño, the Indian Ocean Dipole, time of year, terrain,
county size) and served by `GET /api/risk-scores/<id>/explain/` in times the
usual chance, with a sentence written from them (`riskengine/why.py`).

It says how the model used each input for that prediction, not what causes
floods. Two cautions: the parts of correlated inputs, such as the four
rain inputs, are shared out by which one each tree happened to split on, so
the groups are more reliable than single inputs; and county size is a
control for how floods were recorded (see below), so its part is about the
labels, not the land.

In October 2026 it showed that most of the country's High scores came from
El Niño in the short rains, while the rain that had fallen so far was
holding them down, and the dashboard now says so for each place.

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
| `open-meteo` (default) | One representative point per county: ERA5 to 2016, ECMWF IFS from 2017 (see "The Colab retrain" in the experiments log) | 1 point per region |
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

The figures below are the 7 October 2026 run on Google Colab, through
`notebooks/train_flood_model.ipynb`: 14,523 county-months from 1996 to 2021,
of which 1,406 (9.7%) have a recorded flood. The saved model and
`metrics.json` are the files that run wrote to Google Drive. The run id is
`20261007T1353Z_07abff3` and it was built from commit `07abff3`.

Two runs were made on 7 October. The first (`20261007T1204Z_e3be7cb`)
repeated the second 6 October run with the same data and seed, to record the
commit and keep the outputs in Drive. The second, reported here, added the
repeated seeds, the learning curve and the computing budget below. Every
figure the 6 October run reported is identical in both; only the model
version, the `provenance` block and the new entries differ. The browser lost
its connection to Colab near the end of the second run, so the committed
notebook has no output under its last three cells (the partial dependence
chart, the coefficient table and the save message). The run itself finished:
its Drive folder holds the model, the metrics and all eleven charts,
including `10_partial_dependence.png`. The 6 October run was the first
with the settings search. The search kept the Random Forest and Logistic
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

| Warn above | Floods caught | Floods missed | False alarms | Recall | Precision | F1 | Accuracy | Balanced accuracy |
|---|---|---|---|---|---|---|---|---|
| 5% (Moderate and up) | 1,243 | 163 | 6,232 | 88.4% | 16.6% | 0.280 | 56.0% | 70.4% |
| 10% (High and up) | 1,008 | 398 | 3,689 | 71.7% | 21.5% | 0.330 | 71.9% | 71.8% |
| 20% (Very High) | 585 | 821 | 1,628 | 41.6% | 26.4% | 0.323 | 83.1% | 64.6% |

Some false alarms are floods nobody recorded, and some "floods" are
counties a circle touched that stayed dry, so the precision is a floor.
Accuracy rises with the threshold only because nine rows in ten have no
flood; balanced accuracy, the mean of recall and specificity, does not
reward that.

### Repeated runs

The Random Forest and Gradient Boosting draw random numbers, so each was
cross-validated again under seeds 0 to 4, on the same folds and at the
settings kept (`seed_variation` in `metrics.json`). Logistic Regression has
nothing random at its settings. Pooled over all held-out rows:

| Model | Average precision, mean (sd) over 5 seeds | Lowest to highest | ROC AUC, mean (sd) |
|---|---|---|---|
| Random Forest | 0.2516 (0.0027) | 0.2486 to 0.2542 | 0.7834 (0.0024) |
| Gradient Boosting | 0.2339 (0.0013) | 0.2321 to 0.2355 | 0.7613 (0.0027) |
| Logistic Regression | 0.2385 (deterministic) | | 0.7655 |

The seed moves the Random Forest's average precision by about 0.003. Its
lowest seed (0.2486) is still above Logistic Regression (0.2385) and above
every Gradient Boosting seed, so the pooled ranking does not depend on the
seed. Which years are held out matters far more: the standard deviation
across folds is 0.098. The saved model is seed 0, which scores 0.2535, a
little above the five-seed mean of 0.2516.

### Learning curve

In each fold the Random Forest was fitted on a quarter, a half, three
quarters and all of the training years, sampled as whole years with a fixed
seed, and scored on the same held-out years (`learning_curve` in
`metrics.json`, `docs/figures/11_learning_curve.png`):

| Share of training years | Years (mean) | Rows (mean) | Average precision, mean (sd) over 6 folds |
|---|---|---|---|
| 25% | 6.0 | 3,314 | 0.217 (0.141) |
| 50% | 11.0 | 6,134 | 0.255 (0.094) |
| 75% | 16.7 | 9,306 | 0.309 (0.119) |
| 100% | 21.7 | 12,102 | 0.297 (0.098) |

Average precision rises from 6 to about 17 training years and is flat after
that: the step from 75% to 100% (0.309 to 0.297) is a tenth of the spread
across folds. A few more years of the same kind of record would probably not
help much. The limit is more likely the inputs and the labels. This is one
draw of which years are sampled, so the shape is indicative only.

### Computing budget

The 7 October run took 1,236 seconds on the free Colab runtime: 2 cores of
an Intel Xeon at 2.20 GHz, Python 3.13.16 (`compute` in `metrics.json`,
`run.json` in the run folder).

| Stage | Seconds |
|---|---|
| Cross-validation of the three models, settings search included | 444 |
| Final test (the same on 1996 to 2016) | 390 |
| Repeated seeds | 251 |
| Permutation importance | 75 |
| Learning curve | 71 |
| Fitting the saved model | 4 |

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
the two; the earlier figures are in the git history. The two 7 October runs
scored the test years again, with the procedure for choosing unchanged, and
returned the 6 October figures exactly. No choice was made from them, and
the repeated seeds and the learning curve use cross-validation only.

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
  number. Most of the June figure comes from how a flood is assigned to a
  month, not from the model: 76 of the 88 are there only because of two
  long-rains floods, of 2003 and 2005, whose recorded end is 4 June and
  3 June. Each touches the 30 days ending 30 June by three or four days,
  so every county under it is labelled as flooded in June (see the
  experiments log).
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

### SHAP against the app's explanations

SHAP, named in the proposal, was run on 10 November 2026 as a check, not
in the app (`manage.py shap_check`; results in `artifacts/shap_check.json`
and `artifacts/shap_summary.png`). TreeSHAP (Lundberg et al., 2020) gives
Shapley values for the same trees `explain.py` walks. Its parts also add
up to the probability, and unlike the tree-path method it does not depend
on which correlated input a tree split on first. Both were run on the
shipped model (`flood-random-forest-20261007`) for the 2,679 final-test
rows (2017 to 2021), and on the 503 of them at or above 0.2, the top
probability band. Nothing was retrained.

| | all rows | high-risk rows |
|---|---|---|
| Same factor group first | 86.7% | 86.5% |
| Rank correlation of the 7 groups, median (10th percentile) | 0.96 (0.82) | 0.96 (0.86) |
| Mean difference per group, against the mean size of a SHAP part | 0.006 / 0.023 | 0.012 / 0.043 |
| Same single input first (of 13) | 62.5% | 73.2% |

- **The groups agree; single inputs less so.** That is the caution in
  "Explaining one prediction" above, now measured: the dashboard shows
  groups, which is the level the two methods agree at.
- **Overall ranking.** Time of year, recent rain and El Niño are the top
  three in SHAP, the tree-path method and permutation importance alike.
  Below that they differ: SHAP puts the Indian Ocean Dipole fifth, while
  permutation importance puts it last (shuffling it does not lower the
  held-out AUC). The model uses the Dipole, but on the final-test years
  that use did not help it rank flood months.
- **A bug it found.** Before the check, 18 of the 2,679 tree-path
  explanations missed the probability by up to 0.001. scikit-learn compares
  inputs as float32, and a float64 value just above a threshold could go
  the other way in `explain.py`. Inputs are now cast to float32;
  `test_a_value_on_a_threshold_takes_the_forests_path` covers it, and every
  row now adds up to within 1e-15.

The app keeps the tree-path method: it needs no extra library and is fast
enough for a request. `shap` and `matplotlib` are in
`requirements-analysis.txt`, not in the app image.

`artifacts/metrics.json` holds the current figures: `final_test` for the
final test table, `folds` and
`fold_summary` for the fold table, `threshold_errors` for the warning table,
`tuning`, `error_breakdown` and `permutation_importance` for the three
sections above, and
`provenance` for the commit, the run id, the SHA-256 of the training set,
the seed and the scikit-learn version the run used. A Colab run is not a git
checkout, so `build_colab_bundle` writes the commit into the bundle as a
`COMMIT` file and `train.git_commit()` reads it there. The current model is
run `20261007T1353Z_07abff3`, from commit `07abff3`. The 6 October run
recorded no commit; its bundle was built from commit `51e4de2`.

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
`flood-random-forest-20261006` and `flood-random-forest-20261007` give the
same probabilities, so the run was not repeated:

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
- **The biggest remaining gain is better labels.** Against the floods the
  Global Flood Database mapped, a third of the counties a circle labels as
  flooded held mapped flood water (see "The labels against mapped flood
  water"). That database maps only 17 of the 62 floods placed on Kenya from
  2000 to 2018, so it measures the problem and does not solve it.
- **Severity classes are not modelled.** DFO grades floods 1, 1.5 and 2, but
  only three Kenyan events are class 2. The model predicts whether a large
  flood is recorded, not how bad.

## Experiments log

Each attempt to improve the model is one controlled change, judged on the
year-held-out cross-validation and never on the final test. It gets a row
below whether it helped or not.

### Baseline, 9 October 2026

The figures every experiment is compared against. They are run
`20261007T1353Z_07abff3` (`flood-random-forest-20261007`), read from
`artifacts/metrics.json` and `artifacts/unosat_validation_sub_county.csv`.
A warning is High or Very High (above 10%).

| Measure | Baseline | Where it is stored |
|---|---|---|
| Average precision, pooled held-out rows | 0.254 | `comparison` |
| Average precision, mean (sd) of six folds | 0.297 (0.098) | `fold_summary` |
| ROC AUC, pooled | 0.787 | `comparison` |
| Brier score, pooled | 0.0798 | `comparison` |
| Lift over the season-only model, pooled | 2.05 times | `lift_over_season_only` |
| Floods caught by a warning | 1,008 of 1,406 (71.7%) | `threshold_errors` |
| Warnings followed by a recorded flood | 21.5% | `threshold_errors` |
| June floods missed | 86 of 88 | `error_breakdown.by_month` |
| Floods missed in Nairobi, Kilifi, Lamu and Kwale | 33 of 71 (46%) | `error_breakdown.by_county` |
| Very High: probability given against floods recorded | 31.2% against 26.4% | `band_calibration` |
| Final test, 2017 to 2021: lift over the season-only model | 1.08 times | `final_test` |
| Final test: Very High given against recorded | 33.5% against 17.6% | `final_test.band_calibration` |
| UNOSAT, counties, both events: ROC AUC | 0.616 | `unosat_validation.csv` |
| UNOSAT, sub-counties ranked within their county: ROC AUC | 0.461 | `unosat_validation_sub_county.csv` |

The two final-test rows are here to show where the model stands. They are
not used to decide whether a change is kept.

### How a change is judged

`manage.py compare_inputs` (`riskengine/ml/experiments.py`) scores the
Random Forest with and without the inputs under trial, on the same folds of
held-out years, and saves nothing. Every figure is the mean over seeds 0 to
4. A change is kept when pooled average precision rises by more than 0.005,
about twice the standard deviation between seeds, or when the measure it
targets improves and average precision does not fall by more than that.

```bash
docker compose run --rm web python manage.py compare_inputs rain_60d rain_90d
docker compose run --rm web python manage.py compare_inputs --county-mean-rain
```

With no inputs added the command gives average precision 0.2516 (0.0027),
the figure in "Repeated runs" above, so it measures the same thing as the
training run. The comparisons in the log were run on the training set
built on point rainfall on 2 October (SHA-256 `367173d3...`, the one the
7 October model was trained on). It is kept in `data/processed/` as
`training_set_point_20261002.csv`, because `training_set.csv` was rebuilt
on county-mean rainfall on 9 October for the retrain. The counts below are means over five seeds, so they differ a
little from the baseline table, which is seed 0 alone (June floods missed:
86.2 against 86).

### Log

"Before" and "after" are pooled held-out average precision, with the
measure the change targets beside it.

| Date | What changed | Before | After | Kept or dropped |
|---|---|---|---|---|
| 9 Oct 2026 | Added `rain_60d` and `rain_90d`, rainfall over 60 and 90 days, so that June still sees the long rains | 0.2516; 86.2 of 88 June floods missed | 0.2499; 85.8 of 88 | Dropped |
| 9 Oct 2026 | Added `rain_3d_max` and `heavy_days`, the wettest three days and the days with 50 mm or more, for short intense rain | 0.2516; 32.4 of 71 floods missed in Nairobi, Kilifi, Lamu and Kwale | 0.2482; 31.0 of 71 | Dropped |
| 9 Oct 2026 | Rainfall measured as the mean over the county (ERA5-Land, Earth Engine) in place of one point (Open-Meteo); labels and other inputs unchanged | 0.2516; 3,686 false alarms, 412.8 floods missed | 0.2713; 3,598 false alarms, 385.8 floods missed | Kept in cross-validation, then retrained on Colab and **not shipped**: see "The Colab retrain" |
| 9 Oct 2026 | Labels: a flood counts for the window it began in, and for a later one only with seven days in it (on county-mean rainfall) | 0.2713 on the current labels; lift over season only 2.20 | 0.2566 on the new labels; lift 2.36. Trained on the current labels and scored on the new: 0.2672 | Not adopted |
| 15 Oct 2026 | Point rainfall asked for as ERA5 every year (`models=era5`), in place of the service's choice of ERA5 to 2016 and IFS from 2017; labels and other inputs unchanged | 0.2516; 3,686 false alarms, 412.8 floods missed | 0.2579; 3,636 false alarms, 415.8 floods missed | Kept: the record is one dataset. Final test waits for the Colab retrain: see "One rainfall record" |
| 21 Oct 2026 | Added `soil_moisture_start`: water in the top 28 cm of soil the day before the window, ERA5-Land averaged over the county (Earth Engine) | 0.2579; 415.8 floods missed, 3,636 false alarms | 0.2678; 376.2 floods missed, 3,705 false alarms | Kept in cross-validation; waits for the retrain with the river inputs: see "Soil moisture" |
| 21 Oct 2026 | Added `soil_moisture_max` as well, the wettest day inside the window | 0.2678 (`soil_moisture_start` alone) | 0.2689; 370.0 floods missed, 3,722 false alarms | Dropped: +0.0011 over `soil_moisture_start` alone, within the noise |
| 21 Oct 2026 | Labels: a flood the Global Flood Database mapped labels the counties that held 5 km² or more of its water, not its circle; other floods keep the circle (on point ERA5 rainfall) | 0.2579 on the current labels | 0.2229 on the new labels. On the 322 mapped rows: trained on current 0.4383, on new 0.4580, on new with circle labels at half weight 0.4832; every ROC AUC there below 0.5 | Not adopted: see "Labels from mapped flood water" |

#### County-mean rainfall (kept)

| Measure | One point | County mean |
|---|---|---|
| Average precision, pooled | 0.2516 (0.0027) | 0.2713 (0.0018) |
| Average precision, mean of six folds | 0.2940 (0.0043) | 0.3006 (0.0022) |
| ROC AUC, pooled | 0.7834 (0.0024) | 0.7980 (0.0011) |
| Brier score, pooled | 0.0801 (0.0003) | 0.0791 (0.0001) |
| Floods missed, of 1,406 | 412.8 | 385.8 |
| False alarms | 3,686 | 3,598 |
| June floods missed, of 88 | 86.2 | 84.0 |
| Floods missed in Nairobi, Kilifi, Lamu and Kwale, of 71 | 32.4 | 29.2 |

Mean (standard deviation) over seeds 0 to 4. Average precision rises by
0.0197, about four times the threshold, and it is the first change that
both catches more floods and raises fewer false alarms. Against the
season-only model's 0.1235 the lift goes from 2.04 to 2.20 times. The gain
averaged by fold is smaller (0.007), which suggests most of it is better
ranking across years, not within one.

A county is tens of kilometres across and one point is one grid cell of it,
so the mean is the better measure of how much rain the county received.
`build_training_set --rain-source earth-engine` already builds a training
set this way; this is the first measurement of it recorded here.

The training set was then rebuilt with `--rain-source earth-engine` and the
model retrained on Colab. The next section has what that run showed.

#### The Colab retrain on county-mean rainfall (not shipped)

Run `20261009T1051Z_8058da0`, from commit `8058da0`, on 9 October 2026:
1,314 seconds on the free Colab runtime. Its `metrics.json`, `run.json`
and satellite check are kept in
`artifacts/not_shipped/20261009T1051Z_8058da0/`; the model, the charts and
the executed notebook are in the run's folder in Google Drive. **The
shipped model is still `flood-random-forest-20261007`.**

Cross-validation, pooled over held-out years:

| Model | ROC AUC | Average precision | Brier |
|---|---|---|---|
| Gradient Boosting (selected, depth 2, learning rate 0.02) | 0.785 | 0.278 | 0.0784 |
| Random Forest | 0.799 | 0.274 | 0.0790 |
| Logistic Regression | 0.774 | 0.242 | 0.0828 |
| Season only | 0.565 | 0.124 | 0.0873 |

The three checks do not agree:

| Check | Shipped model (7 October, point rainfall) | This run (county-mean rainfall) |
|---|---|---|
| Cross-validation: selected model, average precision | Random Forest 0.254 | Gradient Boosting 0.278 |
| Cross-validation: Very High, given against recorded | 31.2% against 26.4% | 30.2% against 29.8% |
| Cross-validation: false alarms above 10% | 3,689 | 3,126 |
| Final test 2017 to 2021: the model the earlier years chose | Gradient Boosting 0.191, 1.08 times season only | Gradient Boosting 0.177, 1.00 times |
| Final test: Random Forest, not chosen | 0.186 (ROC AUC 0.782) | 0.209 (ROC AUC 0.822) |
| Final test: Logistic Regression, not chosen | 0.270 (ROC AUC 0.852) | 0.262 (ROC AUC 0.873) |
| UNOSAT, counties, both events: ROC AUC | 0.616 | 0.427 |
| UNOSAT, November 2023 and April 2024 | 0.744 and 0.430 | 0.690 and 0.327 |

- **Cross-validation, the criterion fixed in advance, prefers this run.**
  Its probabilities are also better calibrated there and it raises fewer
  false alarms.
- **The final test does not.** The model the earlier years choose is no
  better than the calendar on 2017 to 2021. The two models not chosen
  both rank those years better than before, so the rainfall helped them
  and the choice of model did not.
- **The satellite check is worse**, most of all for April 2024, where the
  model ranks counties worse than chance.
- **The choice of model changed again**, from Random Forest to Gradient
  Boosting, on a lead of 0.004 in average precision. Over five seeds the
  two are 0.2784 (0.0032) and 0.2713 (0.0019).

With one check for and two against, the run is not shipped. That is a
decision to take with the supervisor, not one cross-validation makes
alone. This was the fifth look at the final test years; nothing was chosen
from it.

**Why the checks disagree: the point rainfall is two datasets.** Comparing
the two training sets showed that point and county-mean rainfall agree
closely until 2016 and much less from 2017:

| Years | Correlation of `rain_30d`, point against county mean |
|---|---|
| 1996 to 2016, year by year | 0.81 to 0.92 |
| 2017 | 0.64 |
| 2018 | 0.72 |
| 2019 | 0.71 |
| 2020 | 0.60 |
| 2021 | 0.73 |

The cause is the Open-Meteo archive. Asked for no particular model, it
serves ERA5 for dates before 2017 and the ECMWF IFS analysis at 9 km from
2017 on. For Kisumu in April 2018 it returns 279 mm by default, which is
its `ecmwf_ifs` figure, and 351 mm when ERA5 is named; for April 2015 the
default and ERA5 are the same 335 mm. `fetch_archive_rainfall` named no
model, so every training set built with `--rain-source open-meteo` before
15 October 2026 is ERA5 to 2016 and IFS from 2017. Until then this file and
the code said ERA5 throughout. The request now names ERA5: see "One
rainfall record" below.

What follows from it:

- **The final test boundary sits on the change of dataset.** 2017 was
  chosen as the boundary without knowing this. A model chosen on 1996 to
  2016 has seen only ERA5, and is then tested on IFS rainfall. Part of the
  weak final test reported above for the shipped model may be this, and not
  only a failure to carry over to later years. It cannot be told apart
  without rebuilding the point rainfall with ERA5 named throughout.
- **The shipped model's last five training years are IFS.** The rainfall
  it is scored on each day comes from Open-Meteo's forecast service, which
  is operational model output and not ERA5 either. Which model that service
  uses for Kenya has not been checked.
- **A model trained on county-mean ERA5-Land would be scored on points
  from that forecast service**, which it has never seen. The satellite check above avoided
  that by reading ERA5-Land for 2023 and 2024, and still came out worse,
  so the mismatch is not the reason for that result.
- **The two rainfall records disagree about the events themselves.** For
  the 30 days to 28 April 2024 the county means average 342 mm and the
  points 204 mm, and across counties the two correlate at 0.47 (0.25 for
  November 2023).

A one-off check, not kept as a command, fitted each of the three models on
each training set and scored the two UNOSAT events (ROC AUC, November
2023, April 2024, both):

| Model | Point rainfall | County-mean rainfall |
|---|---|---|
| Random Forest | 0.742, 0.430, 0.615 | 0.851, 0.277, 0.506 |
| Gradient Boosting, tuned | 0.682, 0.513, 0.555 | 0.690, 0.327, 0.427 |
| Logistic Regression | 0.647, 0.614, 0.638 | 0.450, 0.335, 0.393 |

On county-mean rainfall every model ranks April 2024 worse than chance,
and the Random Forest ranks November 2023 better than anything else
tried. One reading, not tested: the water mapped in April 2024 includes
Turkana and Garissa, arid counties where a flood comes down a river from
rain that fell elsewhere. A model that reads each county's own rainfall
more accurately would rank such an event worse, not better, which points
at the inputs (no rain upstream, no rivers) more than at the rainfall
measurement. It is two events, and the cautions in "Checking against
satellite floods" apply.

What to do next, in order:

1. Rebuild the point rainfall for 2017 to 2021 with ERA5 named, so the
   whole record is one dataset, and repeat the final test. This is the
   cleanest answer to how much of the weak final test was the change of
   dataset.
2. Decide the scoring rainfall to match: the daily refresh would have to
   read a source consistent with training.
3. Only then choose between point and county-mean rainfall, and between
   the three models, with the supervisor.

#### One rainfall record: ERA5 named throughout (kept)

Step 1 above, done on 15 October 2026. `fetch_archive_rainfall` now sends
`models=era5`, and `build_training_set --rain-model` records the model in
`training_set.meta.json` (`best_match` rebuilds the older sets). The point
training set was rebuilt and kept as
`data/processed/training_set_point_era5_20261015.csv`.

Only 2017 to 2021 changed. Against the 2 October point set, `rain_30d`
moved by a median of 0 mm and at most 6.4 mm in 1996 to 2016, and by a
median of 21.6 mm and up to 446 mm in 2017 to 2021. The flood labels are
identical. The correlation with county-mean rainfall is now in the earlier
range every year:

| Year | Point (2 October, IFS from 2017) against county mean | Point (ERA5 throughout) against county mean |
|---|---|---|
| 1996 to 2016 | 0.81 to 0.92 | 0.81 to 0.92 |
| 2017 | 0.64 | 0.85 |
| 2018 | 0.72 | 0.89 |
| 2019 | 0.71 | 0.89 |
| 2020 | 0.60 | 0.84 |
| 2021 | 0.73 | 0.91 |

Cross-validation with `compare_inputs --county-mean-rain`, mean
(standard deviation) over seeds 0 to 4:

| Measure | Point, IFS from 2017 | Point, ERA5 throughout | County mean |
|---|---|---|---|
| Average precision, pooled | 0.2516 (0.0027) | 0.2579 (0.0028) | 0.2713 (0.0018) |
| Average precision, mean of six folds | 0.2940 (0.0043) | 0.2930 (0.0039) | 0.3006 (0.0022) |
| ROC AUC, pooled | 0.7834 (0.0024) | 0.7863 (0.0017) | 0.7980 (0.0011) |
| Brier score, pooled | 0.0801 (0.0003) | 0.0798 (0.0002) | 0.0791 (0.0001) |
| Floods missed, of 1,406 | 412.8 | 415.8 | 385.8 |
| False alarms | 3,686 | 3,636 | 3,598 |
| June floods missed, of 88 | 86.2 | 86.4 | 84.0 |
| Floods missed in Nairobi, Kilifi, Lamu and Kwale, of 71 | 32.4 | 29.0 | 29.2 |

Pooled average precision rises by 0.0063, just beyond the 0.005 threshold;
the mean of folds does not move. The gain is in ranking across years,
which is what a consistent record should change. County-mean rainfall is
still ahead, by 0.0134 instead of 0.0197, so about a third of its earlier
lead was the change of dataset.

**Not yet known:** how much of the weak final test was the change of
dataset. That needs the Colab retrain on this set, and is the sixth look at
the final test years. The scoring rainfall (step 2) is also still open.

#### Rainfall inputs (dropped)

What the first two results show:

- **Neither change moved average precision beyond the noise** (-0.0017 and
  -0.0034, against a threshold of 0.005). Both caught about 17 more floods
  of 1,406 and raised about 40 to 80 more false alarms of 3,686, which is
  the same model warning slightly more often, not ranking better.
- **June cannot be fixed with rainfall inputs, because most of it is not a
  rainfall error.** The 88 June floods fall in four years: 39 in 2003, 42
  in 2005, 3 in 2007 and 4 in 2008. All 39 of 2003 come from one flood
  recorded from 21 April to 4 June. In 2005, 41 of the 42 lie under a
  flood recorded from 3 May to 3 June, and for 37 of them it is the only
  flood. A county-month is labelled as flooded when a flood overlaps its
  30 days at all, so three or four days at the start of June label the
  whole of June: 76 of the 88 rows. Across the 88, June itself was drier
  than usual (median 33 mm against 41 mm in June months with no flood)
  while the month before was much wetter (152 mm against 80 mm). The
  model's low June probability is a fair reading of such a month. The
  remedy is in how a flood is assigned to a month, for example by
  requiring a minimum number of days of overlap. That was tried next: see
  "Assigning a flood to a month" below.
- **`heavy_days` has almost nothing to count.** ERA5 rainfall at about
  25 km reaches 50 mm in a day on 0.1% of days, and 3% of county-months
  have any such day. A city flood from an hour of rain is not in this
  record at any threshold, which fits the result for Nairobi and the coast.
- **This agrees with the learning curve:** the limit is the labels, not the
  rainfall inputs or the number of years.

The four inputs are still worked out by `rainfall_features` and listed as
`CANDIDATE_FEATURES` in `features.py`, so they can be tried again against
better labels.

### Soil moisture (kept in cross-validation)

Phase 2, step 3 of `docs/MODEL_IMPROVEMENT_PLAN.md`. Ground that is already
wet floods sooner, and the rain inputs say how much fell, not how much the
ground still held. `soil_moisture_start` is the water in the top 28 cm of
soil (ERA5-Land layers 1 and 2, 0 to 7 and 7 to 28 cm, weighted by depth)
on the day before the window, averaged over the county by Earth Engine.
`soil_moisture_max` is the wettest day's value inside the window. Run on 21
October 2026 on the point ERA5 training set:

```bash
docker compose run --rm web python manage.py compare_inputs soil_moisture_start
docker compose run --rm web python manage.py compare_inputs soil_moisture_start soil_moisture_max
```

| Measure | Current inputs | + `soil_moisture_start` | + both |
|---|---|---|---|
| Average precision, pooled | 0.2579 (0.0028) | 0.2678 (0.0022) | 0.2689 (0.0035) |
| Average precision, mean of six folds | 0.2930 (0.0039) | 0.3025 (0.0026) | 0.3045 (0.0048) |
| ROC AUC, pooled | 0.7863 (0.0017) | 0.7936 (0.0019) | 0.7957 (0.0018) |
| Brier score, pooled | 0.0798 (0.0002) | 0.0790 (0.0002) | 0.0789 (0.0002) |
| Floods missed, of 1,406 | 415.8 | 376.2 | 370.0 |
| False alarms | 3,636 | 3,705 | 3,722 |
| June floods missed, of 88 | 86.4 | 86.4 | 85.4 |
| Floods missed in Nairobi, Kilifi, Lamu and Kwale, of 71 | 29.0 | 28.2 | 27.2 |

Mean (standard deviation) over seeds 0 to 4.

- **`soil_moisture_start` is the first input since county-mean rainfall to
  pass the threshold.** Pooled average precision rises by 0.0099, about
  twice the 0.005 used, and the mean of folds by 0.0095, so the gain is
  within years as well as across them. It catches about 40 more floods
  for about 70 more false alarms.
- **The wettest day inside the window adds nothing beyond it** (+0.0011),
  as expected: most of what it says is already in `rain_30d`. Dropped.
- **Not yet in the model.** Adopting it needs the daily refresh, the
  forecast and the outlook to read soil moisture as well, and a retrain on
  Colab. The plan does that once for every input kept in Phase 2, after
  the river inputs have been tried (Phase 5).

### River discharge (download running)

Phase 2, step 1. GloFAS river discharge (v4, 5 km, through Open-Meteo's
flood service, from 1 January 1997) at a point on each county's largest
river: the HydroRIVERS reach inside the county with the highest long-term
mean discharge, not in a lake, moved onto the GloFAS cell whose flow best
matches that reach (`geodata/services/river_points.py`,
`open_meteo.river_cell`). The points are in
`data/raw/discharge/river_points_county.json`. Garissa reads the Tana,
Busia and Siaya the Nzoia, Kisumu the Nyando.

Two compromises are recorded here so the result is read with them:

- Baringo's river cell carries Lake Baringo, whose rise from 2020 was
  itself a flood, and Nairobi's carries the Athi just below the city.
- Turkana and Marsabit share one point, on the Kerio near Lake Turkana,
  because the county boundary runs through the lake.

The download is about 30,000 Open-Meteo calls, three days of the free
quota, so `manage.py build_discharge --budget 4500 --every-hours 24` takes
it a day's budget at a time and leaves the rest for the daily refresh.
Started on 21 October 2026. The three inputs under trial are
`discharge_30d_max`, `discharge_30d_mean` and `discharge_rise` (the
window's peak against the river's mean over the year before, which needs no
normal fitted per fold). Then:

```bash
docker compose run --rm web python manage.py compare_inputs discharge_30d_max discharge_rise
docker compose run --rm web python manage.py compare_inputs soil_moisture_start discharge_30d_max discharge_rise
```

### Assigning a flood to a month (not adopted)

The June result above suggested a change to the labels: a flood counts for
the 30 days it began in, and for a later 30 days only when at least seven
of its days fall in them. `compare_labels` works the labels out again under
that rule and compares, on county-mean rainfall. Run on 9 October 2026.

```bash
docker compose run --rm web python manage.py compare_labels --min-overlap-days 7 --county-mean-rain
```

Changing the labels changes what is predicted, so there are three columns
and no single before and after.

| | Current labels | New labels | Trained on current, scored on new |
|---|---|---|---|
| County-months with a flood | 1,406 | 1,198 | 1,198 |
| Base rate | 0.0968 | 0.0825 | 0.0825 |
| Season-only average precision | 0.1235 | 0.1085 | 0.1117 |
| Average precision, pooled | 0.2713 | 0.2566 | 0.2672 |
| Average precision, mean of six folds | 0.3006 | 0.2926 | 0.2999 |
| ROC AUC, pooled | 0.7980 | 0.8204 | 0.8201 |
| Lift over the base rate | 2.80 | 3.11 | 3.24 |
| Lift over the season-only model | 2.20 | 2.36 | 2.39 |
| False alarms | 3,598 | 2,826 | 3,691 |
| June floods, and how many are missed | 88, 84 missed | 12, 12 missed | 12, 8.4 missed |

- **The rule removes 208 flood labels of 1,406 (15%)**, every one of them
  the last one to six days of a flood that began in an earlier window.
  June goes from 88 floods to 12.
- **The model is better than its headline figures say.** Scored against
  labels without those tails, the model as it is trained now reaches ROC
  AUC 0.820 against 0.798, and 3.24 times the base rate against 2.80. Part
  of what was counted as error was the labels.
- **Training on the new labels does not help.** The model trained on the
  current labels predicts the new ones at least as well as the model
  trained on them (average precision 0.2672 against 0.2566). The tail of a
  flood still tells the model that a place was wet, so removing it takes
  away a little signal along with the noise.
- **So the rule is not adopted.** `MIN_OVERLAP_DAYS` in `build_training_set`
  stays at 1 and the labels are unchanged. Adopting it would change the
  target, and with it every figure in this file, for no gain in what the
  model learns. The finding is reported here instead.
- **The 12 June floods that remain are still missed** by a model trained on
  the new labels. With 12 of them in 26 years there is too little to learn
  from, and too little to conclude from.

A stricter first version of the rule (seven days in the window, or half of
a shorter flood, with no exception for the window a flood began in) was
tried first and did worse: it also removed the month in which a flood
began late, which is the month whose rain caused it. Average precision on
its own labels was 0.2231, and the model trained on the current labels
again predicted them better (0.2473).

### The labels against mapped flood water

Not a change to the model: a measurement of the labels it learns from.
`check_labels_gfd` takes the floods the Global Flood Database mapped from
MODIS (`docs/DATA_DICTIONARY.md`, section 6) and compares, event by event,
the counties under the circle with the counties that held at least 5 km² of
mapped flood water. Run on 9 October 2026; the rows are in
`artifacts/gfd_label_check.csv`.

```bash
docker compose run --rm web python manage.py check_labels_gfd
```

| | Count |
|---|---|
| Floods placed on Kenya, 2000 to 2018 | 62 |
| Of those, mapped by the database | 17 |
| County labels from the 62 floods | 615 |
| County labels from the 17 mapped floods | 139 |
| Of the 139, counties that held mapped flood water | 46 (33%) |
| Of the 139, counties with none mapped | 93 |
| Counties with mapped flood water outside the circle | 52 |

- **Two thirds of the counties a circle labels as flooded held no mapped
  flood water**, and more counties held it outside a circle (52) than
  inside one (46). The flood of April 2002 is the clearest case: its circle
  covers 41 counties and water was mapped in 5.
- **This is an upper estimate of the error.** MODIS at 250 m does not see
  water under cloud or trees, or in narrow and urban channels, so some of
  the 93 did flood. It is still the first number put on the weakness that
  "Known limits" describes.
- **The database cannot replace the circles.** It maps 17 of the 62 floods
  and 139 of the 615 labels (23%), and the largest floods are not among
  them: those of 2003, 2005, 2006, 2010 and 2018. Relabelling a quarter of
  the rows would leave a training set made two ways. The earlier note that
  this database would fix the labels "at the source" was too hopeful.
- **What it leaves.** Labels that say where the water was have to be made
  for the events that matter, from satellite images, which is practical for
  a few regions and not for the country.

### Labels from mapped flood water (not adopted)

Phase 3, steps 1 and 4 of `docs/MODEL_IMPROVEMENT_PLAN.md`. For the 17
floods the Global Flood Database mapped, a county is labelled from the map:
flooded if it held at least 5 km² of mapped flood water, whether the circle
touched it or not, and not flooded if the circle touched it and no water was
mapped. Every other flood keeps its circle (`riskengine/ml/labels.py`). Each
row also records where its label came from, and a second model is trained
with the labels that rest on a circle alone at half weight. Run on 21
October 2026 on the point ERA5 training set, mean over seeds 0 to 4:

```bash
docker compose run --rm web python manage.py check_labels_gfd   # once, for the mapped water
docker compose run --rm web python manage.py compare_labels --mapped-where-available --circle-weight 0.5
```

| | Current labels | New labels | Current, scored on new | New, circle labels at 0.5 |
|---|---|---|---|---|
| County-months with a flood | 1,406 | 1,327 | 1,327 | 1,327 |
| Base rate | 0.0968 | 0.0914 | 0.0914 | 0.0914 |
| Average precision, pooled | 0.2579 | 0.2229 | 0.2377 | 0.2222 |
| ROC AUC, pooled | 0.7863 | 0.7645 | 0.7737 | 0.7709 |
| Floods missed | 415.8 | 462 | 421 | 656.6 |
| False alarms | 3,636 | 3,625 | 3,720 | 2,307 |

158 county-months stop being flooded and 79 become flooded. 322 rows are
labelled from a map, 164 of them flooded; 1,163 flooded rows rest on a
circle alone.

The test the plan sets is the next table: every model scored on the
labels of the 322 mapped rows only, the surest labels there are.

| On the 322 mapped rows | Trained on current | Trained on new | Trained on new, circles at 0.5 |
|---|---|---|---|
| Base rate | 0.5093 | 0.5093 | 0.5093 |
| Season-only average precision | 0.4837 | 0.4552 | 0.4546 |
| Average precision | 0.4383 | 0.4580 | 0.4832 |
| ROC AUC | 0.3796 | 0.3985 | 0.4210 |
| Floods missed, of 164 | 60.8 | 63.2 | 81.8 |
| False alarms | 126 | 121.4 | 98.2 |

- **On the mapped rows every model ranks counties worse than chance.**
  ROC AUC is 0.38 to 0.42, and average precision is below the base rate.
  Within a mapped flood, the counties where the water was are given lower
  scores than the counties under the same circle that stayed dry. This is
  the April 2024 result in "Checking against satellite floods" again, now
  on 17 floods from 2000 to 2018 and not one event.
- **Training on the mapped labels helps there, a little.** Average precision
  rises by 0.020, and by 0.045 with the circle labels at half weight, and
  ROC AUC by 0.02 and 0.04. That is beyond the 0.005 used for the whole
  set, but it is measured on 322 rows from 17 floods, and none of the
  three reaches chance.
- **Over all rows it does worse.** The model trained on the current labels
  predicts the new labels better than the model trained on them (0.2377
  against 0.2229), as it did for "Assigning a flood to a month". Half
  weight for circle labels lowers every probability, so the same 10%
  threshold misses 657 floods of 1,327 in place of 421.
- **So the labels are not adopted.** The flood archive and the rules are
  unchanged.

What it says about the model is the useful part. The water a satellite
mapped is often in a county whose own rainfall does not explain it, and a
model that reads only that rainfall ranks those counties low. That is what
the river and soil inputs of Phase 2 add. The comparison should be rerun
with them in the model: the mapped labels may help once the model has an
input that can tell the wet counties from the dry ones.

### Susceptibility, run 20261102T1246Z_77a8329 (candidate)

The first complete run of `SUSCEPTIBILITY_PROTOCOL.md`, on Colab from commit
`77a8329`, 2 November 2026: 3,547 seconds. The model, `metrics.json`, the
config, the summary and the three charts are in
`artifacts/susceptibility/20261102T1246Z_77a8329/`; the executed notebook is
`notebooks/train_susceptibility.ipynb`. A first run on 1 November crashed in
the sign check after scoring the candidates; this run reused those scores
from the Drive checkpoints, so the numbers are the same.

Within-county ROC AUC, every pixel predicted by a model that never saw its
event:

| | All pixels | Away from large lakes |
|---|---|---|
| Selected: boosting, learning rate 0.05, 31 leaves | **0.7918** | 0.6455 |
| The same, 0.5 degree blocks | 0.7761 | 0.6300 |
| HAND, the best baseline | 0.7176 | 0.6005 |
| Distance to river | 0.616 | 0.5686 |
| 100-year flood zone | 0.627 | 0.5491 |
| Boosting, learning rate 0.05, 15 leaves | 0.7541 | |
| Boosting, learning rate 0.1, 15 and 31 leaves | 0.6718, 0.6437 | |
| Logistic regression, every C | 0.676 | |

- **Rule 1 says ship the model.** 0.7918 is above 0.7176 + 0.03, and 0.7761
  on blocks is not below HAND.
- The model beats HAND on 20 of the 25 events. Brier score 0.00364.
- Permutation importance: elevation 0.183, HAND 0.167, slope 0.157,
  distance to river 0.064, TWI 0.057, flood zone 0.055, upstream area less.

**The sign check failed on TWI, and the reason is the check, not the
physics.** Partial dependence falls with HAND and with distance to river, as
it should, and falls with TWI (−0.037 across the 5th to 95th percentile),
which it should not. Checked afterwards on the sample and the run's model:

- In the data TWI goes the right way: on its own it ranks flooded above dry
  pixels inside county-events at 0.569, and the weighted flooded share rises
  from 0.0016 in the lowest TWI decile to 0.0036 in the highest.
- TWI is ln(upstream area / tan(slope)), and upstream area and slope are
  both features. Rebuilt from them it correlates with TWI at 1.000. Moving
  TWI with the other two held still describes pixels that cannot exist, so
  that curve has no physical reading.
- Moving upstream area and letting TWI follow it, as on the ground, gives a
  U-shaped curve that ends where it starts (−0.0007). The model has no
  wetness effect of its own; HAND and elevation carry it.

Accepted by Kelvin on 2 November 2026. Rule 3 says a reason found does not
reopen rule 1, so the decision stays. A protocol v2 should drop TWI or the
upstream area and slope pair, or check the joint change. The UNOSAT test has
not been scored.

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

The shipped model is trained on Google Colab, through the pipeline. A run
is decided by a committed config in `configs/` (TOML): the training set and
its SHA-256, the inputs, the folds, the settings grids, the seeds, the
primary metric and the promotion margin. `pipeline/hazard.py` checks the
config against the settings `train.py` fixes in code and refuses to run on
any difference, so a change to one is a change to both. The 2017 to 2021
final test is closed: no config can open it, and `train_model` no longer
scores it.

```bash
# Check a config against the code and its training set, without training:
docker compose run --rm web python -m riskengine.ml.pipeline train --model hazard     --config riskengine/ml/configs/hazard_rf_20261007.toml --check
# Commit your work, then pack the code and that config's training set:
docker compose run --rm web python manage.py build_colab_bundle     --config riskengine/ml/configs/hazard_rf_20261007.toml
```

This writes `data/processed/colab_bundle_<commit>.tar.gz`: the committed
code, the config's training set at its own path, a `COMMIT` file, a
`CONFIG` file and the SHA-256 of the training set. It refuses to run with
uncommitted or untracked changes. Put the file in Google Drive under
`MyDrive/FloodScout/data/`, open `notebooks/train_flood_model.ipynb` in
Colab and run it all. The notebook only unpacks the bundle, calls the
pipeline and saves what it wrote, into `MyDrive/FloodScout/runs/<run id>/`:

| File | What it is |
|---|---|
| `flood_model.joblib`, `metrics.json` | The model and its evaluation, in the format of `artifacts/` |
| `config.toml` | The config the run used, byte for byte |
| `summary.md` | The run's primary metric against the served model's, and whether that comparison is fair (same training set) |
| `ledger_line.md` | The run's line for `experiments.md` |
| `figures/` | The charts, which go in `docs/figures/` |
| `run.json` | Run id, commit, bundle, config, training set SHA-256, Python, processor, training time |
| `log.txt` | What the training printed |
| `environment.txt` | `pip freeze` of the Colab session |

The run id is the UTC start time and the first seven characters of the
commit, and `metrics.json` records it. Bring back `metrics.json`,
`config.toml` and `summary.md` into `runs/<run id>/` and the ledger line
into `experiments.md`, for every run, kept or not. The model file stays in
Drive; git ignores it under `runs/`.

A run never replaces the served model. It is a candidate until a promotion
PR copies its `flood_model.joblib` and `metrics.json` into `artifacts/`,
after the comparison, the guard metrics and a shadow period. The same
`python -m riskengine.ml.pipeline train` works locally, writing into
`runs/` and adding its line to `experiments.md` itself.

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
