# FloodScout: Model Improvement Plan

Written 12 October 2026. This is the plan for making the flood model more
accurate. It follows from what the evaluation and the experiments log in
`riskengine/ml/README.md` found, and it is ordered by how much each change is
expected to help.

The rules in section 0 of `docs/IMPLEMENTATION_GUIDE.md` apply to all of it:
one branch per phase cut from `develop`, a pull request into `develop`, no AI
attribution in git, and numbers quoted only from files.

---

## 1. Why these changes and not others

The model's limit is its data, not the algorithm. Four findings say so:

| Finding | Where it is recorded | What it rules out |
|---|---|---|
| The settings search confirmed the fixed settings | "The settings search" | More tuning |
| Three different models score within noise of each other, and the best one changes with the years held out | "The final test" | A different algorithm |
| The learning curve is flat from about 17 training years | "Learning curve" | More years of the same archive |
| Four extra rainfall inputs moved average precision by less than the noise | "Rainfall inputs (dropped)" | More features from the same rainfall |

Three findings say where the gain is:

| Finding | Where it is recorded | What it points to |
|---|---|---|
| Two thirds of the counties a circle labels as flooded held no mapped flood water | "The labels against mapped flood water" | Better labels (Phase 3) |
| April 2024 was ranked worse than chance; the water was in arid counties that flood from rain upstream | "Checking against satellite floods" | River and upstream inputs (Phase 2) |
| Point rainfall is ERA5 to 2016 and ECMWF IFS from 2017, and daily scoring reads a third source | "The Colab retrain" | One rainfall record (Phase 1) |

So the plan does not try a neural network, a larger grid search or more
rainfall features. Do not spend time on those.

## 2. Where the model stands

Every phase is compared against these. They are run
`20261007T1353Z_07abff3` (`flood-random-forest-20261007`), read from
`riskengine/ml/artifacts/metrics.json`.

| Measure | Now | A result worth shipping |
|---|---|---|
| Average precision, pooled held-out years | 0.254 | Higher by more than 0.005 |
| Lift over the season-only model, pooled | 2.05 times | Higher |
| Lift over the season-only model, mean of folds | 1.47 times | Higher |
| Floods caught by a warning (High and above) | 71.7% | Not lower |
| Warnings followed by a recorded flood | 21.5% | Higher |
| Final test 2017 to 2021: lift over season only | 1.08 times | Clearly above 1 |
| UNOSAT, counties, both events: ROC AUC | 0.616 | Not lower |
| UNOSAT, April 2024: ROC AUC | 0.430 | Above 0.5 |

No target figure is promised. Whether a large flood is recorded in a county
in a month is a hard thing to predict, and the labels cap how well any model
can score. The aim is a model that clearly beats the calendar on years it
has not seen.

## 3. How every change is judged

These rules are already in use. They are repeated here because the 9 October
retrain showed what happens without them: cross-validation preferred the new
model and two other checks did not.

1. **One change at a time.** Each phase changes one thing and is compared
   with the phase before it.
2. **Cross-validation decides.** `manage.py compare_inputs` scores the Random
   Forest with and without the change, on the same six folds of held-out
   years, as the mean over seeds 0 to 4. A change is kept when pooled average
   precision rises by more than 0.005, or when the measure it targets
   improves and average precision does not fall by more than that.
3. **The final test never decides.** 2017 to 2021 has been scored five times.
   It is rerun only after a change is adopted, reported as a further look,
   and nothing is chosen from it.
4. **A model is shipped only if all three checks agree:** cross-validation is
   better, the final test is not worse, and the UNOSAT check is not worse. If
   they disagree, the run goes in `artifacts/not_shipped/` and the decision
   is taken with the supervisor.
5. **Every attempt gets a row in the experiments log**, kept or dropped.
6. **The shipped model is trained on Colab** from a bundle built by
   `build_colab_bundle`, and its run folder is kept in Google Drive.

## 4. Before starting

- [ ] Merge PR #73. It holds `compare_labels`, the 9 October findings and the
      note that the point rainfall is two datasets. Phases 1 and 3 build on it.
- [ ] Merge the model performance panel, so a retrain shows on the dashboard.
- [ ] Agree with the supervisor that the phases below may be run, and which
      model is deployed meanwhile (issues #36 and #47).

---

## Phase 1: One rainfall record (about 1 day)

**Why.** The archive request names no model, so training rainfall is ERA5 to
2016 and IFS from 2017. The final test boundary sits exactly on that change.
Until the record is one dataset, the weak final test cannot be read: it may
be the model failing on later years, or only the change of rainfall.

**What to change.**

1. `geodata/services/open_meteo.py`: give `fetch_archive_rainfall` a `model`
   argument and send `models=era5`. Keep the default behaviour available so
   the 2 October training set can still be reproduced.
2. `build_training_set`: a `--rain-model era5` option, written to
   `training_set.meta.json` with the rain source. The rainfall cache file
   names must include the model, or the old IFS files will be read back.
3. Rebuild the point training set. Only 2017 to 2021 should change.
4. `riskengine/tests_model.py`: a test that the model name reaches the
   request and the cache name.

**How to check.**

- Correlation of `rain_30d` between the new point set and the county-mean
  set, year by year. It was 0.60 to 0.73 for 2017 to 2021 and 0.81 to 0.92
  before. It should now be in the earlier range for every year.
- `compare_inputs` with no inputs added, on the new set, against 0.2516.
- Retrain on Colab and read the final test.

**Done when** the experiments log has a row for it, and the README states how
much of the weak final test was the change of dataset.

**Then decide the scoring rainfall.** `seed_rainfall` reads Open-Meteo's
forecast service, which is neither ERA5 nor IFS analysis. Check which model
it serves for Kenya and write it down. If it differs much from ERA5, the
daily scores are read from rainfall the model was not trained on, and
Phase 4 becomes more important.

---

## Phase 2: River and upstream inputs (2 to 3 days)

**Why.** The model sees only the rain that fell on a county. Garissa, Tana
River and Turkana flood from rain that fell in the highlands days earlier.
That is the likely reason April 2024 was ranked worse than chance, and it is
the cheapest change that adds new information.

**What to change.**

1. **River discharge.** A new client in `geodata/services/` for Open-Meteo's
   flood service (GloFAS river discharge, daily, from 1984). Confirm the
   endpoint, the coverage for Kenya and the licence before building on it,
   and add it to `docs/DATA_DICTIONARY.md`.
   - Sample it at the point on the county's largest river, not the
     representative point. `data/external/drainage/rivers.geojson` already
     holds the rivers.
   - Candidate inputs in `CANDIDATE_FEATURES`: `discharge_30d_max`,
     `discharge_30d_mean`, and `discharge_ratio`, the mean over the county's
     normal for that month. The normal is refitted per fold, as the rainfall
     normal is.
2. **Upstream rainfall.** For each county, the rainfall averaged over the
   river basin that drains into it. HydroBASINS gives the basins; Earth
   Engine can average ERA5-Land over them. Candidate input: `rain_30d_upstream`.
3. **Soil moisture.** ERA5-Land volumetric soil water at the start of the
   window. Candidate input: `soil_moisture_start`.

**How to check.** One `compare_inputs` run per group, in the order above:

```bash
docker compose run --rm web python manage.py compare_inputs discharge_30d_max discharge_ratio
docker compose run --rm web python manage.py compare_inputs rain_30d_upstream
docker compose run --rm web python manage.py compare_inputs soil_moisture_start
```

Read three things from each: pooled average precision, floods missed in
Garissa, Tana River, Turkana and Wajir, and the UNOSAT April 2024 ROC AUC.

**Watch for.**

- GloFAS discharge is itself a model driven by ERA5 rainfall. It may add
  less than a gauge would.
- A county with no large river has no discharge to read. Leave it missing;
  do not invent a value.
- Whatever is added must also be available every day for scoring, and for
  the seasonal outlook. An input that exists only historically cannot ship.

**Done when** each group has a row in the experiments log, and the inputs
that are kept are in `FEATURE_NAMES`, `seed_*` fills them and
`riskengine/inputs.py` shows them in the dashboard.

---

## Phase 3: Better flood labels (1 to 2 weeks)

**Why.** This is the largest expected gain and the most work. A flood is
placed as a circle around a centre point. Against mapped flood water, 93 of
139 county labels held none, and 52 counties held water outside any circle.
The model is trained on answers that are wrong about two times in three.

**What to change, in steps. Stop after any step that does not help.**

1. **Use mapped extents where they exist** (2 days). For the 17 floods the
   Global Flood Database maps, label a county from the mapped water (at least
   5 km², as `check_labels_gfd` does) and not from the circle. Keep the circle
   for the other 45.
   - New option on `build_training_set`: `--labels mapped-where-available`.
   - Add a `label_source` column, so the two kinds of label can be told apart.
2. **Map the large recent floods from radar** (3 to 5 days). Sentinel-1 in
   Earth Engine sees through cloud. For each large flood from late 2014 on,
   compare the weeks of the flood with the same weeks of a dry year and keep
   the water that is new. `docs/TRAINING_DATA_RESEARCH.md` has the method and
   a list of events. Start with 2018, 2019 and 2020, which are in the
   training years, then 2023 and 2024 as a check on the UNOSAT extents.
3. **Add county-level reports** (2 to 3 days). ReliefWeb and EM-DAT name the
   counties affected. Use them to confirm or remove circle labels for the
   floods no satellite mapped (2003, 2005, 2006, 2010), and to extend the
   record past 2021.
4. **Weight the rows** (half a day). Give mapped labels full weight and
   circle labels less (`sample_weight` in `train.py`), instead of treating
   every label as equally sure. Try 0.5 first.

**How to check.** Changing the labels changes what is predicted, so there is
no single before and after. Use the three columns `compare_labels` already
prints: current labels, new labels, and trained on current but scored on
new. The new labels are worth adopting only if a model **trained** on them
beats the model trained on the current ones, when both are scored on the
mapped labels.

**Watch for.**

- A training set labelled two ways. Report results for mapped rows and
  circle rows separately as well as together.
- MODIS and VIIRS miss narrow and urban flooding and anything under cloud.
  A county with no mapped water is not proof of no flood. Sentinel-1 is
  better but still misses flash floods that drain within a day.
- Adopting new labels changes the base rate, the bands and every figure in
  the report. Do it once, at the end of the phase, not step by step.

**Done when** the experiments log says which steps were adopted, and
`docs/DATA_DICTIONARY.md` documents each new source and its licence.

---

## Phase 4: A rainfall record made for the region (3 to 4 days, optional)

**Why.** ERA5 is a global model at about 25 km. It reaches 50 mm in a day on
0.1% of days, so the short intense rain that floods Nairobi and the coast is
not in it. CHIRPS blends satellite and rain gauges at about 5 km, runs from
1981, and was built for East Africa.

**What to change.** A third value for `--rain-source`, reading CHIRPS daily
from Earth Engine, averaged over the county as `earth-engine` does now.

**How to check.** `compare_inputs` against the Phase 1 rainfall, and the
floods missed in Nairobi, Kilifi, Lamu and Kwale (33 of 71 today).

**Watch for.** The final CHIRPS product arrives about three weeks late, and
the dashboard scores today. Either score from the preliminary product, or
train on CHIRPS and keep it out of the daily refresh. Decide this before
building anything: a model cannot ship on rainfall the scheduler cannot read.

Do this phase only if Phase 1 shows the scoring rainfall is a problem, or if
time remains after Phase 3.

---

## Phase 5: Retrain, calibrate and ship (1 to 2 days)

**What to do.**

1. Build the training set with every change that was kept.
2. `build_colab_bundle`, upload to Drive, run `notebooks/train_flood_model.ipynb`.
   Stay on the tab until the last cell has run: the 7 October notebook lost
   its last three outputs when the browser disconnected.
3. Run the three checks in section 3 and fill the table in section 2.
4. **Calibration.** The probabilities ran about twice too high on 2017 to
   2021. Fit the calibration on the most recent years of each training fold
   and not on all of them, and keep it only if the held-out Brier score
   falls. This corrects the size of the probabilities, not the order of the
   regions.
5. If all three checks agree, copy `flood_model.joblib`, `metrics.json` and
   the figures into the repository and commit the executed notebook with
   them. If they disagree, put the run in `artifacts/not_shipped/`.

**Done when** the dashboard's model performance panel shows the new run id,
`score_regions` and `flood_outlook` have been rerun, and `validate_unosat`
has been rerun for counties and sub-counties.

## Phase 6: Bring the documents into step (1 day)

- `riskengine/ml/README.md`: the new figures, and the experiments log.
- `docs/CHAPTER_5_DRAFT.md` and the Word thesis: every number that changed,
  in the same pull request as the model.
- `docs/DATA_DICTIONARY.md`: each new source.
- `docs/IMPLEMENTATION_GUIDE.md`, section 5: the reference numbers.
- Chapter 6: what was tried, what was not, and why.

---

## 5. Order and time

| Order | Phase | Time | Depends on |
|---|---|---|---|
| 1 | Phase 1, one rainfall record | 1 day | PR #73 merged |
| 2 | Phase 2, river and upstream inputs | 2 to 3 days | Phase 1 |
| 3 | Phase 3, better labels | 1 to 2 weeks | Phase 1 |
| 4 | Phase 4, CHIRPS rainfall | 3 to 4 days | Optional |
| 5 | Phase 5, retrain and ship | 1 to 2 days | Whatever was kept |
| 6 | Phase 6, documents | 1 day | Phase 5 |

Phase 1 fits before the Sprint II review and answers an open question in the
README. Phases 2 and 3 are Sprint III work and compete for time with
deployment, performance testing and user testing, which the final submission
also needs. If only one of them can be done, do Phase 2: it is shorter, and
it adds information the model does not have at all.

Anything not reached goes in Chapter 6 as future work, in the order above.
