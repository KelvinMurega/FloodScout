# FloodScout

**A machine learning-based geospatial risk scoring engine for flood monitoring in Kenya.**

FloodScout scores the flood risk of each of Kenya's 47 counties and 290
sub-counties every day, and shows the result on an interactive map. The
scores come from a model trained on 26 years of recorded floods, rainfall,
climate indices and terrain.

Final year capstone project by Murega Kelvin Mutwiri, School of Computing
and Engineering Sciences, Strathmore University. Supervised by
Dr. Deperias Kerre.

![The FloodScout dashboard, showing flood risk for every county in Kenya](docs/figures/screen_01_national.png)

## Contents

- [What it does](#what-it-does)
- [How it is built](#how-it-is-built)
- [Getting started](#getting-started)
- [Loading the data](#loading-the-data)
- [The dashboard](#the-dashboard)
- [The model](#the-model)
- [Tests and checks](#tests-and-checks)
- [Project layout](#project-layout)
- [Status](#status)
- [Documentation](#documentation)
- [Licence and access](#licence-and-access)

## What it does

- **Scores every region daily.** Each county and sub-county gets a risk
  category (Low, Moderate, High or Very High) from the last 30 days of
  rainfall, the El Niño and Indian Ocean Dipole indices, the time of year
  and the terrain. A scheduler refreshes the scores once a day.
- **Shows the scores on a map.** Anyone can open the dashboard without an
  account, search for a place by name, and select a region to see its score.
- **Looks back and ahead.** The dashboard keeps the scores of earlier days
  with a 30-day history chart, and gives a seasonal outlook for the next
  three months.
- **Explains each score.** The detail panel lists the inputs the model was
  given for a region, each against its usual value.
- **Reports its own accuracy.** A model performance panel shows how the model
  did on years it was not trained on, including where it is weak.
- **Exports.** The view on screen can be printed as a report or downloaded
  as a CSV file.
- **Lets an administrator rescore.** A System Administrator can sign in and
  score every region again, from the dashboard or the Django admin site.

## How it is built

Four tiers, as designed in Chapter 4 of the project proposal:

| Tier | Technology | Where |
|---|---|---|
| Data | PostgreSQL 16 with PostGIS 3.4 | `geodata/`, `riskengine/models.py` |
| Machine learning | scikit-learn (Random Forest), trained on Google Colab | `riskengine/ml/`, `notebooks/` |
| Application | Django 5.2 and Django REST Framework | `api/`, `accounts/`, `riskengine/` |
| Client | Leaflet.js, built with Vite | `frontend/` |

The whole stack runs in Docker. GitHub Actions runs the lint, both test
suites and the dashboard build on every pull request.

## Getting started

### Prerequisites

- Docker Desktop with the WSL2 backend (recommended), **or** Python 3.12,
  PostgreSQL 16 and PostGIS 3.4 installed natively
- Node.js, for the dashboard in `frontend/`
- Optional: a Google Earth Engine account, only for building a training set
  on county-mean rainfall (see [Earth Engine](#earth-engine-optional))

### With Docker (recommended)

```bash
cp .env.example .env          # then edit DJANGO_SECRET_KEY at minimum
docker compose up --build
```

This starts PostGIS, the Django development server and the daily refresh.
On the first run, in another terminal:

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

Then check:

- http://localhost:8000/api/health/ returns `{"status": "ok", ...}`
- http://localhost:8000/admin/ opens the Django admin; sign in with the
  superuser

The superuser is also a System Administrator on the dashboard. To make an
ordinary account an administrator, set its **Role** in the Django admin
(Users).

### If port 8000 is already taken

This is common on Windows, where a vendor service often owns port 8000.
Docker cannot bind the port, so the `web` container stays in `created` and
nothing listens, with no obvious error. Set `WEB_PORT` in `.env` to a free
port:

```bash
echo "WEB_PORT=8010" >> .env
docker compose up -d
docker compose ps            # web should read 0.0.0.0:8010->8000/tcp, running
```

The container always listens on 8000 internally; only the host port moves,
so every URL above becomes `http://localhost:<WEB_PORT>/...`. Point
`VITE_API_BASE_URL` in `frontend/.env.local` at the same port.

### Without Docker

Only do this if PostgreSQL, PostGIS and the GDAL, GEOS and PROJ system
libraries are already installed. This is fiddly on native Windows; WSL2 is
much easier.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # WSL2, Linux or macOS
pip install -r requirements.txt
cp .env.example .env          # set DB_HOST=localhost, edit DJANGO_SECRET_KEY
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Loading the data

These commands fill an empty database with real data and score it. Run them
in order; each is safe to run again.

```bash
docker compose up -d
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py seed_regions                      # 47 county boundaries
docker compose run --rm web python manage.py seed_regions --level sub_county   # 290 sub-counties
docker compose run --rm web python manage.py seed_terrain                      # elevation and slope
docker compose run --rm web python manage.py seed_rainfall                     # daily rainfall, last 61 days
docker compose run --rm web python manage.py score_regions                     # one score per region for today
docker compose run --rm web python manage.py flood_outlook                     # county outlook, next three months
docker compose run --rm web python manage.py flood_outlook --level sub_county
```

`seed_terrain` and `seed_rainfall` need network access. Everything after
them works offline, because the values are in the database.

While the stack is up, the `scheduler` service keeps the map current. It
runs `refresh_scores` (`seed_rainfall`, then `score_regions`) when it starts
and once a day after that. Each run also scores any days since the last
scored one, up to a month back, so days the computer was off still appear on
the map. To refresh by hand:

```bash
docker compose run --rm web python manage.py refresh_scores
```

On Windows, Docker Desktop does not start on its own unless told to, and the
scheduler runs only while it does. To have the scores refresh without
opening anything, register the scheduled task once:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\install-scheduler-task.ps1
```

It starts Docker and the scheduler when you sign in and every morning at
07:00, or as soon as the computer is on if it was off at 07:00. It writes to
`scheduler-task.log`. Remove it with
`Unregister-ScheduledTask -TaskName "FloodScout daily refresh" -Confirm:$false`.

The seasonal outlook is not part of the daily refresh. Run `flood_outlook`
when a new monthly forecast is out.

### Where the data comes from

All of it is real and public. Nothing is simulated, and a value that could
not be measured is left empty.

| Data | Source |
|---|---|
| County and sub-county boundaries | geoBoundaries (KEN ADM1 and ADM2), pinned to a commit |
| Elevation and slope | Open-Meteo elevation API (Copernicus DEM GLO-90) |
| Daily rainfall | ECMWF, served by Open-Meteo |
| El Niño index (ONI) | NOAA Climate Prediction Center |
| Indian Ocean Dipole index (DMI) | NOAA Physical Sciences Laboratory |
| Recorded floods, for training | Dartmouth Flood Observatory, 1996 to 2021 |
| Satellite flood extents, for checking only | UNOSAT, November 2023 and April 2024 |

`docs/DATA_DICTIONARY.md` documents each source, its fields and its licence.
The `data/` folder is not committed; `build_training_set` downloads and
rebuilds it.

## The dashboard

The Leaflet map is a standalone Vite app in `frontend/`, served on
http://localhost:5173 in development. With the API running and the database
loaded:

```bash
cd frontend
npm install
npm run dev
```

`frontend/README.md` covers the API address and why the client is a separate
app and not a Django template.

## The model

A Random Forest that gives the chance of a large flood being recorded in a
region in a 30-day window. It was trained on Google Colab from
`notebooks/train_flood_model.ipynb`, on 14,523 county-months from 1996 to
2021.

It was compared with Logistic Regression and Gradient Boosting, and with two
baselines, by cross-validation that holds out whole years:

| Measure | Result |
|---|---|
| ROC AUC | 0.787 |
| Average precision | 0.254, against 0.097 for always giving the usual chance |
| Against a model that knows only the month | 2.05 times its average precision |
| Floods caught by a warning (High and above) | 71.7% |
| Warnings followed by a recorded flood | 21.5% |

**Read this as modest skill.** The model orders regions by risk better than
the season alone does, but it is not a precise forecast. On the 2017 to 2021
final test it was only 1.08 times the season-only model, and against
satellite flood maps it ranked one of two floods well and the other badly.
The main limit is the flood records, which place each flood as a circle and
not as an outline.

`riskengine/ml/README.md` has the full evaluation, the known limits and a log
of every experiment. Read it before presenting a result. Every score and API
response carries the `model_version` that produced it, and the dashboard's
model performance panel shows these figures from `GET /api/model-performance/`.

### Earth Engine (optional)

Only `build_training_set --rain-source earth-engine` uses Earth Engine, to
average rainfall over each county. The default source is Open-Meteo and
needs no account. To use it, set `EE_PROJECT_ID` in `.env` and authenticate
once per machine:

```bash
docker compose run --rm web earthengine authenticate --auth_mode=notebook
```

## Tests and checks

```bash
docker compose run --rm web python manage.py test   # Django tests
ruff check .                                        # lint; rules are in ruff.toml
cd frontend && npm test                             # dashboard tests
```

GitHub Actions runs all three, and builds the dashboard, on every pull
request into `develop` and `main` (`.github/workflows/ci.yml`). It uses the
same `docker-compose.yml`, so CI tests what runs locally. `api/tests.py`
checks that reading is open to everyone and rescoring is for an
administrator only.

## Project layout

```
FloodScout/
├── floodscout/     Django project config (settings, root urls, wsgi/asgi)
├── accounts/       Custom User model (System Administrator and End User
│                   roles), token sign-in and the administrator permission
├── geodata/        Region, GeospatialFeature and EnvironmentalData models,
│                   the seed_* commands and the data-source clients
├── riskengine/     RiskScore and RiskOutlook models, scoring and the
│                   scoring commands, plus riskengine/ml/ for the training
│                   and inference code, the trained model and its metrics
├── api/            REST endpoints tying the above together
├── frontend/       Leaflet dashboard, a standalone Vite app
├── notebooks/      train_flood_model.ipynb, the Colab training run with
│                   its evaluation charts
├── data/           raw/, processed/ and external/; not committed
└── docs/           The data dictionary and the figures
```

The four Django apps map onto the Class Diagram and ERD in Chapter 4
(Sections 4.3.4 and 4.4.1), so a model here traces back to an entity there.
The docstring on each model says which.

## Status

As of 12 October 2026.

| Area | State |
|---|---|
| Data pipeline | Done. 47 counties and 290 sub-counties with real terrain, rainfall and drainage. |
| Model and evaluation | Done. Trained on Colab, cross-validated by year, tested on 2017 to 2021 and checked against satellite extents. |
| API and dashboard | Done. Daily scores, seasonal outlook, search, history, inputs, report and CSV export. |
| Model performance panel | Built on `feat/dashboard-model-performance`; not yet merged. |
| Checks | Done. Lint, Django tests and dashboard tests and build run on every pull request. |
| Deployment | Not started. The system runs locally only. |
| Performance and user testing | Not started. Both need the deployed system. |
| Report | In progress. Chapter 5 is drafted; the other chapters are open. |

## Documentation

| Document | What it holds |
|---|---|
| `riskengine/ml/README.md` | The model: inputs, evaluation, limits and the experiments log |
| `docs/DATA_DICTIONARY.md` | Every data source, its fields and its licence |
| `docs/figures/` | The evaluation charts and the dashboard screenshots |
| `frontend/README.md` | The dashboard: configuration and design choices |

## Licence and access

Copyright © 2026 Murega Kelvin Mutwiri. All rights reserved.

This repository is private. It is shared with the project supervisor and the
examiners of Strathmore University for assessment of the capstone project.
No licence is granted to copy, modify or redistribute the code, the trained
model or the documents.

The datasets the system reads belong to their publishers and keep their own
licences, which are listed with each source in `docs/DATA_DICTIONARY.md`.
