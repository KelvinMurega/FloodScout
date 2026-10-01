# The flood model

Kept as its own subpackage so `riskengine/models.py` (the Django ORM model)
and the machine-learning code never share a filename.

## What it predicts

The probability that the Dartmouth Flood Observatory would record a **large
flood in a region in the 30 days ending on a date**. That probability is
`RiskScore.predicted_value`. It is banded into the four risk categories:

| Category | Probability | Floods recorded on held-out years |
|---|---|---|
| Low | up to 5% | 2.9% |
| Moderate | 5–10% | 8.4% |
| High | 10–20% | 15.7% |
| Very High | above 20% | 20.7% |

A large flood is recorded in about 7% of county-months, so the bands sit
around that base rate. "Very High" means roughly a one-in-five chance, three
times the usual. It does not mean a flood is expected.

## What it learns from

One row per county per month, January 1985 to September 2021 (20,727 rows,
1,468 with a flood). Sources are in `docs/DATA_DICTIONARY.md`.

| Input | Meaning |
|---|---|
| `rain_30d` | Rainfall over the 30 days, mm |
| `rain_7d_max` | Wettest 7 consecutive days in those 30, mm |
| `rain_prev_30d` | Rainfall over the 30 days before, mm |
| `rain_30d_ratio` | `rain_30d` over the county's normal for that month |
| `oni` | El Niño index (ONI) as published by that date |
| `month_sin`, `month_cos` | Time of year |
| `elevation`, `slope` | Terrain at the region's representative point |

`features.py` builds this row and is the only place that does, for training,
for `score_regions` and for `flood_outlook`.

## How well it does

Three models are compared by cross-validation that holds out whole years,
because rows from the same month are near-copies of each other and a random
split would flatter all three.

| Model | ROC AUC | Average precision | Brier |
|---|---|---|---|
| Logistic Regression (selected) | 0.726 | 0.160 | 0.0639 |
| Random Forest | 0.706 | 0.144 | 0.0655 |
| Gradient Boosting | 0.714 | 0.155 | 0.0672 |
| Always predict the base rate | 0.500 | 0.071 | 0.0658 |

Read this as modest skill. The model ranks wet, flood-prone months above
dry ones about twice as well as chance, and its Low/Moderate/High bands
line up with what happened. It is not a precise forecast, and above 20% it
is overconfident (it says 31% on average where 21% flooded).

`artifacts/metrics.json` holds the current figures; the table above is the
run of 1 October 2026.

## Known limits

- **Labels are coarse.** The flood archive gives a centroid and an area, not
  an outline, so each flood is placed as a circle. Some counties inside the
  circle did not flood.
- **Labels depend on reporting.** The archive has almost no Kenyan floods
  before 1996 and under-records 1997–98. The model inherits that.
- **Trained on counties, applied to sub-counties.** A sub-county is scored
  from its own rainfall and terrain against its county's normal rainfall.
  That has not been validated at sub-county scale.
- **Drainage is measured but not used.** `seed_drainage` fills river
  distance, flood-zone share and built-up share for every region, and they
  were tried as inputs on 1 October 2026. Held-out average precision moved
  from 0.160 to 0.164, and the model learned both shares backwards (more
  built-up and more flood-zone meant less flood). The cause is the labels.
  A flood is placed as a circle, so a large county is hit more often than a
  small one, and the built-up counties (Mombasa, Nairobi, Vihiga, Kisii)
  are the smallest. Adding county area as a control flipped built-up to
  the expected sign, which confirms it. Used as they stand, the inputs
  would lower the score of the places that drain worst, so they are left
  out. Using them properly needs labels that say where inside a county it
  flooded, such as the UNOSAT satellite flood extents for 2023 and 2024.
- **No Indian Ocean Dipole.** It drives the short rains as much as El Niño
  does (2019), and is only seen indirectly through rainfall.
- **Severity classes are not modelled.** DFO grades floods 1, 1.5 and 2, but
  only three Kenyan events are class 2. The model predicts whether a large
  flood is recorded, not how bad.

## Running it

```bash
docker compose run --rm web python manage.py build_training_set   # downloads and caches the data
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
- ONI for future months is today's value carried forward.

Its results are stored in `RiskOutlook`, not `RiskScore`, so a
forecast-driven score can never become the dashboard's "latest". The
dashboard shows them only when a month is picked on the period bar, labels
them as an outlook, and gives no relocation advice from them. Run it once
per level:

```bash
docker compose run --rm web python manage.py flood_outlook
docker compose run --rm web python manage.py flood_outlook --level sub_county
```
