# Chapter 5: System Implementation, Testing and Evaluation

> **Draft, 5 October 2026.** Every figure in this chapter was read from the
> repository, the development database or a test run on that date. Items in
> **[square brackets]** need the author's input before submission: objective
> wording from Chapter 1, requirement wording from Chapter 4, screenshots, and
> anything only the author can confirm. Section numbering follows the AI/ML
> route of the class guide and should be agreed with the supervisor.

## 5.1 Introduction

This chapter describes how the FloodScout design presented in Chapter 4 was
implemented, tested and evaluated. FloodScout is a geospatial flood risk
scoring system for Kenya. It ingests terrain and rainfall data for 47 counties
and 290 sub-counties, estimates the probability that a large flood is recorded
in each region, classifies that probability into four risk categories, and
presents the result on a web map.

The chapter follows the data-to-model workflow. Section 5.2 records the
implementation environment. Section 5.3 describes the datasets and their
preparation. Section 5.4 covers model training and selection. Section 5.5
describes the implemented system that serves the model. Section 5.6 presents
testing, and Section 5.7 the model evaluation. Sections 5.8 and 5.9 cover
version control and the use of AI-assisted tools. Section 5.10 discusses the
results against the objectives and states the limitations.

At the time of writing the system ran in a local development environment only.
It had not been deployed for public access.

## 5.2 Implementation Environment

### 5.2.1 Hardware and Deployment Environment

Development, training and testing were all carried out on one laptop. No
separate staging or production environment existed. Table 5.1 records the
environment that was actually used. It is the only configuration that was
tested, so it is reported as the tested configuration and not as a measured
minimum.

**Table 5.1: Implementation environment**

| Item or layer | Tested specification | Purpose and justification | Evidence |
|---|---|---|---|
| Development machine | HP EliteBook x360 1040 G8, Intel Core i7-1185G7, 16 GB RAM, Windows 11 Pro | Ran the database, the API, the dashboard and model training | `systeminfo` output, 5 October 2026 |
| Container runtime | Docker 29.7.2, Docker Compose 5.5.1 | Ran the database and the API in containers so the geospatial libraries did not have to be installed on Windows | `docker --version` |
| Database server | `postgis/postgis:16-3.4` container (PostgreSQL 16.4, PostGIS 3.4) | Stored region geometry and supported spatial queries | `select version(), postgis_version()` |
| Application server | `python:3.12-slim` container (Python 3.12.15) running the Django development server on port 8000 | Served the REST API | `Dockerfile`, `docker-compose.yml` |
| Client | [Browser name and version used in testing], Vite development server on port 5173 | Displayed the dashboard | [Screenshot or browser "About" page] |
| Model training | Google Colab (free CPU runtime, scikit-learn 1.9.1) through `notebooks/train_flood_model.ipynb`; the same code also runs on the laptop | The training set has 14,523 rows, so no GPU was needed | The executed notebook; `riskengine/ml/artifacts/metrics.json` |
| Network | Internet access during data ingestion only | Open-Meteo, NOAA and the flood archive are fetched over HTTPS; once the data is in the database, scoring and the dashboard work offline | `README.md`, "Full demo from a clean database" |

### 5.2.2 Software, Services and Configuration

Table 5.2 lists the software that was installed and exercised. Versions were
read from the running containers and from the lock files.

**Table 5.2: Software and services**

| Component | Version tested | Role | Reason for selection |
|---|---|---|---|
| Django | 5.2.17 | Application framework, ORM, admin site, management commands | Long-term support release with built-in geospatial support (GeoDjango) |
| Django REST Framework | 3.18.1 | REST API | Serializers and viewsets over the Django models |
| djangorestframework-gis | 1.3.0 | GeoJSON output | Emits regions as GeoJSON features that Leaflet reads directly |
| PostgreSQL / PostGIS | 16.4 / 3.4 | Spatial database | Spatial column types, indexes and distance queries |
| GDAL | 3.10.3 | Geospatial library | Required by GeoDjango; also used to rasterise regions for the drainage fields |
| psycopg | 3.3.6 | Database driver | Current PostgreSQL driver for Django |
| scikit-learn | 1.9.1 (pinned to 1.9.x) | Model training and inference | Provides all three candidate models. Pinned because the saved model only loads reliably under the version that wrote it |
| NumPy / joblib | 2.5.3 / 1.6.0 | Arrays; model serialisation | Dependencies of scikit-learn |
| django-environ | 0.14.0 | Configuration | Reads settings from a `.env` file kept out of the repository |
| django-cors-headers | 4.9.0 | Cross-origin access | Lets the dashboard on port 5173 call the API on port 8000 |
| earthengine-api | 1.7.46 | Optional rainfall source | Used only by `build_training_set --rain-source earth-engine`. Not used for the reported model |
| Leaflet | 1.9.4 | Web map | Open-source map library with no usage fees |
| Vite | 6.x | Front-end build tool | Development server and static build for the dashboard |
| Node.js / npm | 22.14.0 / 10.9.2 | Front-end toolchain | Required by Vite |
| Git / GitHub | 2.49.0 | Version control | See Section 5.8 |

External services, all free and none requiring a key for the reported
configuration, were the Open-Meteo archive, forecast, elevation and seasonal
APIs; the NOAA Climate Prediction Center index files; the Dartmouth Flood
Observatory archive; and the geoBoundaries repository.

The Docker Compose file defined two services. The `db` service ran the PostGIS
image with a named volume for its data and a health check. The `web` service
was built from the project `Dockerfile`, mounted the source directory, read
its settings from `.env` and started only after the database was healthy.
`.env`, the `data/` folders and the Earth Engine credential folder
(`.secrets/`) were excluded from version control by `.gitignore`. An
`.env.example` file with placeholder values was committed in their place.

### 5.2.3 Deployment and Reproducibility

The system was reproduced from a clean database with the following commands,
which are also given in `README.md`. Each command is safe to run again.

```bash
cp .env.example .env
docker compose up -d
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py seed_regions
docker compose run --rm web python manage.py seed_regions --level sub_county
docker compose run --rm web python manage.py seed_terrain
docker compose run --rm web python manage.py seed_rainfall
docker compose run --rm web python manage.py score_regions
docker compose run --rm web python manage.py flood_outlook
cd frontend && npm install && npm run dev
```

The installation was verified by requesting `http://localhost:8000/api/health/`,
which returns `{"status": "ok", "service": "FloodScout API"}`, and by opening
the dashboard at `http://localhost:5173`. The model was reproduced with
`build_training_set` followed by `train_model`. Training used a fixed random
seed (`RANDOM_STATE = 0`), so the same training set gives the same model.

## 5.3 Dataset Description

### 5.3.1 Data Sources

The model was trained on one table built from five public sources. Full
provenance, licences and download addresses are recorded in
`docs/DATA_DICTIONARY.md`.

**Table 5.3: Data sources**

| Data | Source | Coverage | Licence | Role |
|---|---|---|---|---|
| Flood events | Dartmouth Flood Observatory, Global Active Archive of Large Flood Events | 1985 to October 2021; 5,130 events worldwide, 84 reaching Kenya | Free for research with citation | Training label |
| Daily rainfall | ERA5 reanalysis, served by the Open-Meteo archive API | Daily, sampled at each county's representative point | CC BY 4.0 | Model input |
| El Niño index (ONI) | NOAA Climate Prediction Center | Monthly | Public domain | Model input |
| Indian Ocean Dipole index (DMI) | [Confirm publisher from `geodata/services/climate_indices.py`] | Monthly | [Confirm] | Model input |
| Elevation and slope | Copernicus DEM GLO-90, served by the Open-Meteo elevation API | One value per region | [Confirm] | Model input |
| County and sub-county boundaries | geoBoundaries gbOpen, Kenya ADM1 and ADM2, pinned to one upstream commit | 47 counties, 290 sub-counties | Public domain (ADM1), CC BY 3.0 IGO (ADM2) | Spatial units |

None of the sources contains personal data. All are aggregated to county or
sub-county level.

The flood archive was chosen because it is the only openly available record of
Kenyan floods that spans several decades with dates and locations. ERA5 was
chosen because it is one consistent rainfall record over the whole period. It
is a reanalysis, that is, a weather model constrained by observations, and not
a rain gauge measurement.

### 5.3.2 Data Preparation

One record represents one county in one month. The `build_training_set`
command produced it as follows.

1. **Labels.** The archive gives each flood a centre point and an affected
   area, but no outline. Each flood was therefore placed as a circle of that
   area around its centre. A county-month was labelled `flood = 1` when a
   circle touched the county and the event overlapped the 30 days ending on
   the last day of the month.
2. **Rainfall inputs.** For the same 30 days the command computed total
   rainfall (`rain_30d`), the wettest seven consecutive days (`rain_7d_max`),
   rainfall in the 30 days before (`rain_prev_30d`), and the ratio of
   `rain_30d` to the county's normal for that calendar month
   (`rain_30d_ratio`). A window with too many missing days was recorded as
   unknown, not as dry.
3. **Climate indices.** Each row received the ONI and DMI values that had been
   published by its date, so the model never saw an index it could not have
   had at the time. Two further inputs carry the same indices in October to
   December only and are zero otherwise, because both indices affect the
   short rains far more than the long rains.
4. **Static inputs.** Elevation, slope, the month encoded as sine and cosine,
   and the logarithm of the county's area. Area is a control, not a cause: a
   flood placed as a circle touches a large county more often than a small
   one.
5. **Exclusions.** Rows before 1996 were removed. The archive records almost
   no Kenyan floods before then, so a wet month in those years was labelled
   "no flood" because nothing was reported, not because nothing happened.

**Table 5.4: Final training set**

| Property | Value |
|---|---|
| Records | 14,523 (47 counties × 309 months) |
| Period | January 1996 to September 2021 |
| Positive records (flood recorded) | 1,406 (9.68%) |
| Negative records | 13,117 (90.32%) |
| Inputs | 13 |
| Built | 2 October 2026 |

The classes are imbalanced at roughly one positive to nine negatives. No
resampling or synthetic data was used. The imbalance was handled in the choice
of metrics (Section 5.4.2).

[Insert Table 5.5: three or four sample rows from
`data/processed/training_set.csv`, with a sentence on what they show.]

### 5.3.3 Partitioning and Leakage Control

The data was not split at random. Records from the same month are close copies
of one another: they share one El Niño value, one regional weather system and
often one flood that covers many counties. A random split would place some of
those copies in training and others in validation, and the model would be
rewarded for recognising the month.

Whole years were therefore held out together, using six-fold grouped
cross-validation with the year as the group. Each fold held out four or five
complete years (2,256 to 2,820 records) and trained on the rest.

Two further controls were applied inside each fold:

- The county rainfall normal behind `rain_30d_ratio` was recomputed from the
  training years only, so a held-out year never contributed to its own normal.
- Missing-value imputation and feature scaling were part of the model
  pipeline, so they were fitted on the training folds only.

## 5.4 Model Training and Selection

### 5.4.1 Baselines and Candidate Models

Two baselines were defined before any model was compared:

- **Base rate:** always predict 9.68%, the overall share of flood records.
- **Season only:** a logistic regression that knows the month and nothing
  else. A model that cannot beat this has learned only that floods happen in
  the rainy season.

Three candidate models from Chapter 3 were trained on the 13 inputs.

**Table 5.6: Candidate models and settings**

| Model | Preprocessing | Settings |
|---|---|---|
| Logistic Regression | Median imputation, standard scaling | `max_iter = 1000` |
| Random Forest | Median imputation | 300 trees, minimum 20 samples per leaf, `random_state = 0` |
| Gradient Boosting | Median imputation | 200 trees, depth 3, learning rate 0.05, subsample 0.8, `random_state = 0` |

The settings were fixed in `riskengine/ml/train.py`. A hyperparameter search
was not carried out [see Section 5.10.3; Chapter 3 proposed GridSearchCV].

### 5.4.2 Selection Criterion

Models were compared on three measures computed on held-out years:

- **Average precision**, the area under the precision-recall curve. This was
  the selection criterion because it is informative when positives are rare.
- **ROC AUC**, the probability that a flood record is ranked above a
  non-flood record.
- **Brier score**, the mean squared error of the predicted probability, where
  lower is better. It measures whether a predicted 20% means 20%.

Accuracy was not used. A model that always predicts "no flood" would score
90.3% accuracy on this data and be useless.

### 5.4.3 Experiment Runs

Two full training runs were recorded. The first is kept because the second is
only understandable against it.

**Table 5.7: Training runs**

| | Run 1, 1 October 2026 | Run 2, 2 October 2026 |
|---|---|---|
| Period | 1985 to 2021 | 1996 to 2021 |
| Records / positives | 20,727 / 1,468 (7.08%) | 14,523 / 1,406 (9.68%) |
| Inputs | 9 | 13 (added DMI, the two short-rains inputs and area) |
| Rainfall normal | Computed once over all years | Recomputed inside each fold |
| Selected model | Logistic Regression | Random Forest |
| Average precision | 0.160 | 0.254 |
| ROC AUC | 0.726 | 0.787 |
| Average precision ÷ base rate | 2.26 | 2.62 |

The two runs used different records, so their raw scores are not directly
comparable. The ratio to the base rate is the fairer comparison.

A third experiment was run on 1 October and rejected. River distance,
flood-zone share and built-up share were added as inputs. Held-out average
precision moved only from 0.160 to 0.164, and the model learned both shares
with the wrong sign: more built-up area and more flood-zone area meant a lower
flood probability. The cause was traced to the labels. The most built-up
counties are also the smallest, and small counties are touched by fewer flood
circles. Adding county area as a control reversed the built-up sign, which
confirmed the explanation. The three inputs were left out of the model and
remain in the database for display only.

### 5.4.4 Saved Artefacts

Training wrote two files to `riskengine/ml/artifacts/`. `flood_model.joblib`
holds the fitted pipeline, the list of inputs in order, the probability bands,
the county rainfall normals and the two index tables, so scoring needs no
network access. `metrics.json` holds the results reported in Section 5.7. The
model version, `flood-random-forest-20261006`, is stored on every score the
model produces. `metrics.json` also records the SHA-256 of the training set,
the random seed and the scikit-learn version of the run.

The saved model was trained on Google Colab on 6 October 2026. The training
set and the training code were uploaded as one bundle, built from commit
`51e4de2`, and the notebook `notebooks/train_flood_model.ipynb` ran the same
`train.py` that the application uses. The notebook is kept in the repository
with its outputs, and its charts are in `docs/figures/`. The run repeated the
2 and 5 October runs made on the laptop with the same data and seed. The
Random Forest and Logistic Regression scores were identical, the Gradient
Boosting scores differed in the fourth decimal place, and the saved model
gave the same probability for every training record.

## 5.5 System Implementation

The implementation follows the four tiers of the architecture in Chapter 4:
data, machine learning, application and client.

### 5.5.1 Database Implementation

The entity-relationship design of Chapter 4 was implemented as Django models
in three applications.

**Table 5.8: Implemented tables**

| Table | Purpose | Rows in the development database, 5 October 2026 |
|---|---|---|
| `accounts.User` | One user table with a `role` field (System Administrator or End User) | [count] |
| `geodata.Region` | Counties and sub-counties with their geometry (EPSG:4326) and area in km² | 337 (47 + 290) |
| `geodata.GeospatialFeature` | Elevation, slope, river distance, flood-zone share and built-up share per region | 337 |
| `geodata.EnvironmentalData` | Daily rainfall per region | 21,905 (337 regions × 65 days, 1 August to 4 October 2026) |
| `riskengine.RiskScore` | Probability, risk category, date and model version per region | 1,105 |
| `riskengine.RiskOutlook` | Forecast-driven probability per region per month ahead | 1,011 (337 regions × 3 months) |

Uniqueness constraints on (level, name) for regions, on region for geospatial
features and on (region, date) for scores make every ingestion command safe to
run twice without duplicating rows.

The implementation departs from the Chapter 4 design in four places.
[Chapter 4 diagrams should be updated to match.]

- `Region` gained a `level` and a `parent` so that counties and sub-counties
  share one table and one scoring path.
- `GeospatialFeature` gained `flood_zone_fraction` and `built_up_fraction`.
- `RiskScore` gained `model_version`, so that a score from the early
  placeholder rule can never be mistaken for a score from the trained model.
- `RiskOutlook` is a new table. Scores based on forecast rainfall are kept
  apart from scores based on observed rainfall.

### 5.5.2 Data Ingestion Modules

Ingestion was implemented as Django management commands.

**Table 5.9: Management commands**

| Command | Input | Output |
|---|---|---|
| `seed_regions` | geoBoundaries GeoJSON | `Region` rows; areas computed in EPSG:21037 |
| `seed_terrain` | Open-Meteo elevation API | Elevation and slope per region |
| `seed_drainage` | OpenStreetMap rivers, Copernicus flood hazard map, GHSL built-up layer | Three drainage fields per region |
| `seed_rainfall` | Open-Meteo daily rainfall | `EnvironmentalData` rows for the last 61 days |
| `build_training_set` | Flood archive, ERA5 rainfall, ONI, DMI | `training_set.csv` |
| `train_model` | `training_set.csv` | `flood_model.joblib`, `metrics.json` |
| `score_regions` | Database contents and the saved model | One `RiskScore` per region for a date |
| `flood_outlook` | ECMWF seasonal forecast and the saved model | `RiskOutlook` rows |
| `validate_unosat` | UNOSAT satellite flood extents | Model scores for 2023 and 2024 (Section 5.7.6) |

The computed county areas were checked against the areas geoBoundaries
publishes and agreed to within 0.08% over all 47 counties.

### 5.5.3 Machine Learning Tier

`riskengine/ml/features.py` is the single place where a model input row is
built. Training, daily scoring and the seasonal outlook all call it, so the
model is given inputs at scoring time in exactly the form it was trained on.

`riskengine/ml/infer.py` exposes one function,
`infer(region, date) -> (predicted_value, risk_category)`. The API and the
dashboard depend only on this function. It was first implemented as a simple
weighted rule on rainfall and elevation so that the rest of the system could
be built and demonstrated, and was later replaced by the trained model without
changing any caller. The rule remains as the fallback when no model file is
present.

The probability is classified into four categories using fixed bands.

**Table 5.10: Risk categories**

| Category | Probability of a recorded large flood in 30 days |
|---|---|
| Low | up to 5% |
| Moderate | above 5% to 10% |
| High | above 10% to 20% |
| Very High | above 20% |

The bands sit around the 9.68% base rate. "Very High" therefore means well
above the usual chance. It does not mean a flood is expected.

### 5.5.4 Application Tier

The REST API was implemented with Django REST Framework under `/api/`.

**Table 5.11: API endpoints**

| Endpoint | Returns |
|---|---|
| `GET /api/health/` | Service status |
| `GET /api/regions/` | Regions as a GeoJSON feature collection; filters `level`, `parent`, `focus_region` |
| `GET /api/focus-regions/` | Five flood-prone groupings of counties |
| `GET /api/geospatial-features/`, `GET /api/environmental-data/` | Stored inputs, filtered by `region` |
| `GET /api/risk-scores/latest-by-region/` | The latest score for every region in one database query |
| `GET /api/risk-scores/lower-risk-neighbours/?region=<id>` | Adjacent regions with a lower risk category |
| `GET /api/risk-outlook/`, `GET /api/risk-outlook/months/` | The seasonal outlook |

All endpoints are read-only. Anonymous users may read; the default permission
class is `IsAuthenticatedOrReadOnly`. The user model distinguishes
administrators from end users, but administrator write endpoints and a login
screen had not been built at the time of writing (Section 5.10.3).

### 5.5.5 Client Tier and User Workflow

The dashboard is a separate Vite and Leaflet application in `frontend/`. It
communicates with the API over HTTP only.

One complete task proceeds as follows.

1. `seed_rainfall` stores the latest daily rainfall for all 337 regions.
2. `score_regions` builds an input row per region, obtains a probability from
   the model and stores a `RiskScore` with the model version.
3. The dashboard loads and requests the county boundaries, the latest scores
   and the focus regions in parallel.
4. Counties are coloured by risk category. [Figure 5.1: national view.]
5. The user selects a focus region, for example Lake Basin, and the dashboard
   requests and draws its sub-counties. [Figure 5.2: focus-region view.]
6. The user selects a region to see its probability, category, model version
   and, for High or Very High, lower-risk neighbouring regions.
   [Figure 5.3: region detail panel.]
7. The user picks a future month on the period bar to see the seasonal
   outlook, which is labelled as an outlook and gives no relocation guidance.
   [Figure 5.4: outlook view.]

[Add one screenshot of an error state, for example the dashboard when the API
is not running.]

## 5.6 Testing

### 5.6.1 Test Strategy and Acceptance Criteria

Testing was automated with the Django test framework and run inside the `web`
container against a temporary PostGIS database, so no test touches stored
data. External services were replaced with fixed responses, which makes the
tests repeatable without network access.

**Table 5.12: Test levels**

| Level | What it checked | Example |
|---|---|---|
| Unit | Single functions with no database | Slope calculation, feature row construction, calibration |
| Integration | Management commands writing through the ORM to PostGIS | `seed_regions`, `score_regions` |
| System (API) | HTTP requests through the full Django stack | `latest-by-region`, region filters |
| Model | Training logic and the saved model file | Fold normals, baselines, artefact loading |

The acceptance criterion for the suite was that every test passes. Tests
covered expected behaviour, invalid input and upstream failure. User
acceptance testing and performance testing had not been carried out
(Section 5.10.3).

### 5.6.2 Test Cases and Results

The suite contained 116 tests. On 5 October 2026 all 116 passed in 57.6
seconds (`docker compose run --rm web python manage.py test`). Table 5.13
lists representative cases. [Confirm each requirement identifier against
Chapter 4.]

**Table 5.13: Representative test cases**

| ID | Traceability | Scenario and conditions | Expected result | Actual result |
|---|---|---|---|---|
| TC-01 | DR-01, regions | Load a county supplied as a plain polygon | Stored as a MultiPolygon in EPSG:4326 | Pass |
| TC-02 | DR-01, regions | Compute county area | Area in km², not square degrees | Pass |
| TC-03 | DR-01, regions | Run `seed_regions` twice | No duplicate rows | Pass |
| TC-04 | DR-01, regions | Request an unknown county name | Command error, nothing written | Pass |
| TC-05 | DR-01, terrain | Elevation service fails | Command error, nothing written | Pass |
| TC-06 | DR-02, rainfall | Service returns null rainfall for a day | Day dropped, not stored as zero | Pass |
| TC-07 | DR-02, rainfall | Service returns HTTP 429 (rate limit) | Wait and retry; give up after the retry budget | Pass |
| TC-08 | FR-03, features | Window with too many missing days | Recorded as unknown, not as dry | Pass |
| TC-09 | FR-03, model | Wet region against dry region | Wet region scores higher | Pass |
| TC-10 | FR-03, model | Region with no data | Still receives a valid category | Pass |
| TC-11 | FR-04, classification | Every category produced | One of the four defined categories | Pass |
| TC-12 | FR-03, scoring | Run `score_regions` twice for one date | One row per region, stamped with the model version | Pass |
| TC-13 | IR-01, API | `GET /regions/` | GeoJSON feature collection, 47 counties by default | Pass |
| TC-14 | IR-01, API | `latest-by-region` for all regions | One row per region, in a single database query | Pass |
| TC-15 | IR-01, API | Malformed `level`, `parent` or `month` parameter | HTTP 400 | Pass |
| TC-16 | IR-01, API | `latest/` for a region with no score | HTTP 404 | Pass |
| TC-17 | IR-01, API | `lower-risk-neighbours` | Adjacent regions only, lower category only, least risky first | Pass |
| TC-18 | Model integrity | Rainfall normals inside a fold | Use training rows only | Pass |
| TC-19 | Model integrity | Rows before 1996 | Dropped from training | Pass |
| TC-20 | Model integrity | Saved model file | Loads and scores a county and a sub-county | Pass |
| TC-21 | IR-02, outlook | Outlook rows in the API | Carry no relocation advice | Pass |

The full list is in `geodata/tests.py`, `geodata/tests_sampling.py`,
`riskengine/tests.py` and `riskengine/tests_model.py`. The `accounts` and `api`
applications had no tests of their own.

### 5.6.3 Defects and Corrective Action

**Table 5.14: Defects found and corrected**

| ID | Defect | Found by | Correction | Retest |
|---|---|---|---|---|
| D-01 | Token authentication was configured but its application was not installed, so authenticated requests failed | Review of settings | Registered `rest_framework.authtoken` (commit `a31c669`) | Pass |
| D-02 | `prediction_date` was set automatically on insert, so scoring for a chosen date could not be recorded and repeat runs added duplicate rows | `score_regions` tests | Made the field settable and added a uniqueness constraint (migration `0002`) | TC-12 pass |
| D-03 | Rainfall normals were computed over all years, so held-out years contributed to their own input | Review of training code | Normals recomputed inside each fold (commit `edb3ef5`) | TC-18 pass |
| D-04 | Years before 1996 labelled wet months as "no flood" because floods were not reported | Inspection of the label distribution by year | Rows before 1996 excluded | TC-19 pass |
| D-05 | Drainage inputs were learned with the wrong sign | Inspection of model coefficients | Inputs removed from the model; county area added as a control | See Section 5.4.3 |

[Add any defects found during supervisor demonstrations.]

## 5.7 Model Evaluation

All results in this section are on held-out years: each record was scored by a
model that had not seen its year.

### 5.7.1 Comparison with Baselines

**Table 5.15: Held-out performance, pooled over all 14,523 records**

| Model | ROC AUC | Average precision | Brier score |
|---|---|---|---|
| Base rate | 0.500 | 0.097 | 0.0874 |
| Season only | 0.565 | 0.124 | 0.0873 |
| Logistic Regression | 0.766 | 0.239 | 0.0830 |
| **Random Forest (selected)** | **0.787** | **0.254** | **0.0798** |
| Gradient Boosting | 0.750 | 0.215 | 0.0856 |

Source: `riskengine/ml/artifacts/metrics.json`.

The Random Forest had the best score on all three measures and was selected.
Its average precision was 2.62 times the base rate and 2.05 times the
season-only baseline. The second figure matters more, because it shows that
rainfall, the ocean indices and terrain add information beyond the time of
year.

### 5.7.2 Variation Across Folds

**Table 5.16: Average precision in each held-out fold**

| Fold (held-out years) | Records / floods | Season only | Logistic Regression | Random Forest | Gradient Boosting |
|---|---|---|---|---|---|
| 1 (1996, 2002, 2008, 2014, 2020) | 2,820 / 232 | 0.287 | 0.247 | 0.288 | 0.301 |
| 2 (2001, 2007, 2013, 2019, 2021) | 2,679 / 243 | 0.078 | 0.166 | 0.168 | 0.144 |
| 3 (2000, 2006, 2012, 2018) | 2,256 / 307 | 0.252 | 0.480 | 0.351 | 0.238 |
| 4 (1999, 2005, 2011, 2017) | 2,256 / 149 | 0.070 | 0.171 | 0.195 | 0.162 |
| 5 (1998, 2004, 2010, 2016) | 2,256 / 248 | 0.318 | 0.314 | 0.365 | 0.324 |
| 6 (1997, 2003, 2009, 2015) | 2,256 / 227 | 0.207 | 0.220 | 0.413 | 0.262 |
| **Mean (standard deviation)** | | 0.202 (0.106) | 0.266 (0.118) | 0.297 (0.098) | 0.239 (0.073) |

Source: `folds` and `fold_summary` in `metrics.json`.

Performance varied widely between folds. The Random Forest's average
precision ranged from 0.168 to 0.413, and its ROC AUC from 0.719 to 0.872
(mean 0.799, standard deviation 0.055). It beat the Logistic Regression in
five folds of six and the season-only baseline in five, with fold 1 a tie. The
advantage over the season-only baseline is smaller when folds are averaged
(0.297 against 0.202, a ratio of 1.47) than when all records are pooled
(2.05). Both figures are reported because the pooled figure alone would
overstate the gain. With only six folds, the ranking of the Random Forest
above the Logistic Regression is suggestive and not conclusive.

### 5.7.3 Calibration

Two calibration methods, Platt scaling and isotonic regression, were tested on
the selected model. Neither lowered the held-out Brier score (0.0802 and
0.0814 against 0.0798 uncalibrated), so no calibration was applied.

**Table 5.17: Predicted and observed flood rate by risk category**

| Category | Held-out records | Mean predicted probability | Floods actually recorded |
|---|---|---|---|
| Low | 7,048 | 1.9% | 2.3% |
| Moderate | 2,778 | 7.3% | 8.5% |
| High | 2,484 | 14.2% | 17.0% |
| Very High | 2,213 | 31.2% | 26.4% |

The categories were correctly ordered: each had a higher observed flood rate
than the one below it, and a Very High record was about eleven times more
likely to have a recorded flood than a Low record. The model slightly
under-predicted in the lower three categories and over-predicted in the top
one, where it stated 31% on average and 26% occurred.

### 5.7.4 Error Analysis

**Table 5.18: Confusion matrix, treating High and Very High as a flood warning (probability above 10%)**

| | Flood recorded | No flood recorded |
|---|---|---|
| **Warning** | 1,008 (true positives) | 3,689 (false positives) |
| **No warning** | 398 (false negatives) | 9,428 (true negatives) |

Source: `threshold_errors` in `metrics.json`. At this threshold recall was
71.7%, precision 21.5% and specificity 71.9% (F1 0.33). Restricting the warning to Very High (above 20%) raised precision
to 26.4% and specificity to 87.6%, and lowered recall to 41.6%.

The two kinds of error have different consequences. A false negative is a
flood in a region shown as Low or Moderate. This is the more serious error for
an early-warning tool, and 398 of 1,406 recorded floods (28.3%) were missed at
the 10% threshold. A false positive is a warning with no recorded flood.
Roughly four warnings in five were of this kind. Some of these are label
errors and not model errors: a flood may have occurred and not been recorded,
and the circle method marks some counties as flooded that were not. The
measured precision is therefore a lower bound of unknown tightness.

[Recommended addition: break the false negatives down by county and by season
to see whether they concentrate in particular places or months.]

### 5.7.5 Final Test on 2017 to 2021

The results above come from the same folds that were used to select the
model, which makes them somewhat optimistic. A final test was therefore run.
The years from 2017 onwards were set aside. The model and its calibration
were selected again by the same cross-validation on 1996 to 2016 alone
(11,844 records), fitted on those years, and then scored once on 2017 to 2021
(2,679 records, 181 floods, 6.8%). The rainfall normals were computed from
1996 to 2016 only. The boundary year was fixed in the code before the result
was seen.

**Table 5.19: Final test on 2017 to 2021**

| Model | ROC AUC | Average precision | Brier score |
|---|---|---|---|
| Base rate of 1996 to 2016 | 0.500 | 0.068 | 0.0643 |
| Season only | 0.694 | 0.177 | 0.0626 |
| **Gradient Boosting, sigmoid-calibrated (selected on 1996 to 2016)** | **0.767** | **0.153** | **0.0753** |
| Logistic Regression (not selected) | 0.852 | 0.275 | 0.0674 |
| Random Forest (not selected) | 0.782 | 0.186 | 0.0820 |

Source: `final_test` in `metrics.json`.

**Table 5.20: Predicted and observed flood rate by category, 2017 to 2021**

| Category | Records | Mean predicted probability | Floods actually recorded |
|---|---|---|---|
| Low | 428 | 3.9% | 0.9% |
| Moderate | 769 | 7.3% | 1.8% |
| High | 800 | 14.3% | 6.0% |
| Very High | 682 | 35.3% | 16.9% |

The final test gave a weaker result than the cross-validation, in three
respects.

First, the selection was not stable. On 1996 to 2016 the Gradient Boosting
model had the highest cross-validated average precision (0.286, against
0.265 for the Random Forest and 0.226 for the Logistic Regression), whereas
on all years the Random Forest was selected. On the test years the Logistic
Regression, which was not selected in either case, scored best.

Second, the selected model did not beat the season-only baseline on average
precision (0.153 against 0.177). It did retain its ability to rank: the ROC
AUC was 0.767, and the four categories remained in the correct order, with a
Very High record about eighteen times more likely to have a recorded flood
than a Low record. With a warning above 10%, 163 of 181 floods were caught
(recall 90.1%) at a precision of 11.0%.

Third, the probabilities were too high. Floods were recorded in 6.8% of
county-months in 2017 to 2021 against 10.3% in 1996 to 2016, and every
candidate had a worse Brier score than a constant prediction of the earlier
base rate. The flood archive ends in October 2021 and its later years may be
less completely recorded, which would account for part of this, but that was
not established.

The deployed model remained the Random Forest fitted on all years. It was
not replaced by the Logistic Regression on the strength of the test result,
because choosing a model by its test score would make the test part of
model selection. [Agree with the supervisor whether to deploy the Logistic
Regression on the grounds of simplicity and stability, and state the reason
here.]

### 5.7.6 Validation Against Satellite Flood Extents

The final test uses the same flood archive as training. To test the model
against labels that do not come from that archive, the deployed model was
scored against flood water mapped from satellite by the United Nations
Satellite Centre (UNOSAT) for two events after the training period: the short
rains of November 2023 and the long rains of April 2024. Each product is a
national map of flood water from the VIIRS sensor at 375 m resolution, with
permanent water already removed. For each event the model scored all 47
counties for the 30 days ending on the last image date, and a county was
counted as flooded when at least 5 km² of it lay inside the mapped water.

**Table 5.21: Deployed model against UNOSAT flood extents**

| Event | Counties flooded | ROC AUC | Average precision |
|---|---|---|---|
| 19 to 28 November 2023 | 31 of 47 | 0.744 | 0.886 |
| 24 to 28 April 2024 | 26 of 47 | 0.430 | 0.499 |
| Both events | 57 of 94 | 0.616 | 0.740 |

Source: `riskengine/ml/artifacts/unosat_validation.csv`, produced by
`validate_unosat` on 5 October 2026.

The two events gave opposite results. For November 2023 the model ranked the
counties well: those it scored highest, among them Garissa, Tana River, Wajir
and Isiolo, were the ones with the most mapped water. For April 2024 the
ranking was worse than chance. The model gave its highest probabilities to
wet highland counties such as Nyandarua and Bomet, where no flood water was
mapped, and lower probabilities to Turkana, Garissa and Kisumu, where it was.
The same pattern was found when the threshold was raised to 25, 50 and
100 km² (ROC AUC between 0.32 and 0.38 for April 2024), so it does not depend
on the threshold.

Only the ranking can be compared with the earlier results. Water was mapped
in 61% of these county-events, whereas a flood was recorded in 10% of the
training records, so the two labels measure different things and the
probabilities cannot be judged against this one.

The cause of the April 2024 result was not established. Three explanations
are possible. A 375 m sensor detects wide, shallow flooding on arid lowlands
far better than the fast river and urban flooding that affected Nairobi and
Mai Mahiu in that month. Cloud cover over the highlands hides flooding
there, and a county under cloud was counted as dry. Part of the water mapped
in Turkana, Marsabit and Baringo is lake shore beyond the reference
shoreline and not flooding from this event. With 94 county-events from two
floods, the result is indicative and not conclusive.

## 5.8 Version Control and Implementation Evidence

Project repository: [github.com/KelvinMurega/FloodScout](https://github.com/KelvinMurega/FloodScout)
(private; [confirm the supervisor has been given access]).

The repository followed a branch-per-feature workflow. Each piece of work was
developed on a `feat/`, `fix/`, `docs/` or `chore/` branch, committed with a
`type(scope): description` message and merged through a pull request. At the
time of writing the repository held 28 commits across 15 branches and 11 pull
requests, six of them merged. [Figure 5.5: commit graph or pull request list.]

Reproducibility evidence in the repository comprised the `README.md` with
setup, run and demonstration instructions; `requirements.txt` and
`frontend/package-lock.json`; the `Dockerfile` and `docker-compose.yml`;
`.env.example`; six database migrations; the training notebook; and
`metrics.json`. Raw data, credentials and the `.env` file were excluded by
`.gitignore`, and each dataset is documented with its download address so it
can be fetched again.

## 5.9 Author Oversight and Use of AI-Assisted Tools

[The author should edit this declaration so that it matches what was actually
done, and keep it consistent with `AI_USE_LOG.md`.]

Claude (Anthropic), used through the Claude Code tool, assisted with writing
and reviewing source code, tests and project documentation, and with preparing
a first draft of this chapter from the repository contents. Its output was
checked against the running system, the automated test results and the
recorded model metrics. [Give one concrete example of a suggestion that was
checked and accepted or rejected, for example the drainage inputs in Section
5.4.3.] The author made the final decisions and takes full responsibility for
the submitted work.

## 5.10 Results, Discussion and Limitations

### 5.10.1 Results Against the Objectives

[Replace the objective wording with the approved objectives from Chapter 1.]

**Table 5.22: Evidence against each objective**

| Objective | Implemented output | Evidence | Result |
|---|---|---|---|
| [Objective 1: collect and prepare geospatial and environmental data] | Ingestion commands and a 14,523-record training set | Tables 5.3, 5.4, 5.8; TC-01 to TC-07 | Achieved for counties. Sub-county rainfall and terrain are stored but were not used in training |
| [Objective 2: develop a machine learning model that predicts flood risk] | Random Forest selected from three candidates against two baselines | Tables 5.15 to 5.18 | Partly achieved. Cross-validated ROC AUC 0.787 and average precision 0.254, 2.05 times the season-only baseline. On the 2017 to 2021 final test the selected model ranked regions well (ROC AUC 0.767) but did not beat the season-only baseline on average precision, and its probabilities were too high |
| [Objective 3: classify regions into risk categories] | Four probability bands | Table 5.17; TC-11 | Achieved as an ordering. Categories were correctly ordered in cross-validation and on the final test. The probability attached to each category was not reliable on the final test |
| [Objective 4: present risk on a web-based map] | REST API and Leaflet dashboard | Section 5.5.5; TC-13 to TC-17 | Core workflow achieved. Login and administrator functions not built |
| [Objective 5: evaluate the system] | Automated tests and cross-validated model evaluation | Sections 5.6, 5.7 | Partly achieved. Independent test on later years and satellite validation completed; user evaluation outstanding |

### 5.10.2 Discussion

In cross-validation the model has real but modest skill. Knowing the month
alone gives an average precision of 0.124, and adding rainfall, the ocean
indices and terrain roughly doubles it. The final test on 2017 to 2021
(Section 5.7.5) qualifies this: the ability to rank regions carried over to
years the model never saw, but the advantage over the season-only baseline
did not, and the stated probabilities were about twice the observed flood
rates. Against satellite flood extents (Section 5.7.6) the ranking held for
the short rains of 2023 and failed for the long rains of 2024. The two ocean
indices carry most of their information in the short rains, which is
consistent with the model being weaker in the long rains, although this was
not tested directly. The cross-validated figures should therefore be read as an upper
estimate. This agrees with the expectation from the literature in Chapter 2
that antecedent rainfall and the Indian Ocean Dipole drive flooding in East
Africa. [Cite the specific sources from Chapter 2.]

The Random Forest outperformed the Logistic Regression in the second run,
whereas the Logistic Regression was ahead in the first. The likely reason is
that the second run added inputs whose effect depends on other inputs, such as
ocean indices that matter mainly in the short rains, and a tree-based model
can represent such interactions directly. This explanation was not tested.

The wide variation between folds (Table 5.16) and the change of selected
model between 1996 to 2016 and all years show that performance depends on
which years are held out, and that the three candidates cannot be reliably
separated with this amount of data. Fold 2, which contains 2019, was the weakest for
every model. The system is better described as a ranking tool, which
identifies where and when the chance of flooding is raised, than as a forecast
of individual floods. The decision to show a probability with a category, to
store the model version on every score, and to keep forecast-based outlooks
separate from observation-based scores follows from this.

### 5.10.3 Limitations

- **Coarse labels.** Each flood is a circle, not a mapped extent. Some
  counties inside a circle did not flood. This limits both what the model can
  learn and how precisely it can be evaluated, and it is why the drainage
  inputs could not be used.
- **Labels depend on reporting.** A flood that was not reported is labelled as
  no flood. The precision figures are affected as described in Section 5.7.4.
- **Weaker performance on later years.** On the 2017 to 2021 final test the
  selected model did not beat the season-only baseline on average precision
  and over-stated probabilities (Section 5.7.5). The risk categories should
  be read as an ordering of regions and not as calibrated probabilities.
- **Inconsistent result against satellite extents.** The model ranked
  counties well for the November 2023 floods and worse than chance for the
  April 2024 floods (Section 5.7.6). The comparison rests on two events, a
  coarse sensor and no correction for cloud.
- **Model selection is unstable.** Different periods select different
  models. The deployed Random Forest was selected on all years.
- **No hyperparameter search and no explainability analysis.** Chapter 3
  proposed GridSearchCV and SHAP. Model settings were fixed, and feature
  contributions were examined only through logistic regression coefficients
  in the first run.
- **Sub-county scores are not validated.** The model was trained on counties
  and applied to sub-counties using the county's rainfall normal and area.
- **Point rainfall.** Rainfall was sampled at one representative point per
  county, which is a weak description of a large county.
- **Seasonal outlook.** The forecast rainfall comes from a different model
  than the training rainfall and no bias correction was applied.
- **Data sources differ from the proposal.** [Chapter 3 proposed CHIRPS
  rainfall, SRTM elevation and Sentinel-1 flood labels through Google Earth
  Engine. ERA5, Copernicus DEM and the flood archive were used instead.
  State the reason: label generation from Sentinel-1 was the highest-risk
  task and the archive allowed a working system first.]
- **Access control is incomplete.** Roles exist in the user model, but there
  is no login screen and no administrator write endpoint. The API is read-only
  for everyone.
- **Local deployment only.** The system ran on a development server with
  debugging enabled. No performance, load, security or usability testing was
  carried out, and no users other than the author used the system.
- **Advisory text.** The relocation guidance on the dashboard is a fixed
  reading of the risk category. It is a demonstration of the feature and not
  official guidance.

Conclusions, recommendations and future work are presented in Chapter 6.
