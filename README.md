# FloodScout

Machine Learning-Based Geospatial Risk Scoring Engine for Flood Monitoring in Kenya.

Final year capstone project — Murega Kelvin Mutwiri (166914), ICS 4B, School of
Computing and Engineering Sciences, Strathmore University. Supervised by
Dr. Deperias Kerre.

This repo implements the design described in the project proposal, Chapter 4
(System Analysis and Design): a four-tier system — PostgreSQL + PostGIS
(Data Tier), a scikit-learn flood model (Machine Learning Tier), a Django
REST API (Application Tier) and a Leaflet.js dashboard (Client Tier).

## Project layout

```
FloodScout/
├── floodscout/     Django project config (settings, root urls, wsgi/asgi)
├── accounts/       Custom User model (System Administrator / End User
│                   roles), token sign-in and the administrator permission
├── geodata/        Region, GeospatialFeature, EnvironmentalData models,
│                   the seed_* commands and the data-source clients
├── riskengine/     RiskScore and RiskOutlook models, scoring and the
│                   scoring commands, plus riskengine/ml/ for the
│                   scikit-learn training and inference code, the trained
│                   model and its metrics (see riskengine/ml/README.md)
├── api/            REST endpoints tying the above together (IR-01)
├── frontend/       Leaflet dashboard, a standalone Vite app
├── data/           raw/ processed/ external/ — see .gitignore, these are
│                   not committed; each data source is documented in
│                   docs/DATA_DICTIONARY.md instead
├── notebooks/      train_flood_model.ipynb, the Colab training run with
│                   its evaluation charts
└── docs/           Data dictionary, plans, the Chapter 5 draft and figures
```

The four Django apps map directly onto the Class Diagram and ERD in Chapter 4
(Sections 4.3.4 and 4.4.1), so a model here should always trace back to an
entity there — see the docstring on each model.

## Prerequisites

- Python 3.12+ (project targets Django 5.2 LTS)
- Docker Desktop with WSL2 backend (recommended path), **or** PostgreSQL 16 +
  PostGIS 3.4 installed natively if you'd rather not use Docker
- Node.js, for the dashboard in `frontend/`
- Optional: a Google Earth Engine account registered against project
  `floodscout-166914`, only for building the training set with county-mean
  rainfall (see below)

## Getting started (Docker — recommended)

```bash
cp .env.example .env          # then edit DJANGO_SECRET_KEY at minimum
docker compose up --build
```

This starts Postgres/PostGIS and the Django dev server together. On first
run, in another terminal:

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

The superuser is also a System Administrator on the dashboard, where it can
sign in and rescore. To make an ordinary account an administrator, set its
**Role** in the Django admin (Users). Anyone can read the map without an
account; `api/tests.py` tests that boundary.

Then check:
- http://localhost:8000/api/health/ → `{"status": "ok", ...}`
- http://localhost:8000/admin/ → Django admin, log in with the superuser above

### If port 8000 is already taken

Common on Windows, where a vendor service often owns 8000. Docker cannot bind
the port, so the `web` container stays in `created` and nothing listens — with
no obvious error unless you look. Set `WEB_PORT` in `.env` to a free port:

```bash
echo "WEB_PORT=8010" >> .env
docker compose up -d
docker compose ps            # web should read 0.0.0.0:8010->8000/tcp, running
```

The container always listens on 8000 internally; only the host port moves, so
every URL above becomes `http://localhost:<WEB_PORT>/...`. If you run the
dashboard, point `VITE_API_BASE_URL` in `frontend/.env.local` at the same port.

## Getting started (without Docker)

Only do this if Postgres/PostGIS and the GDAL/GEOS/PROJ system libraries are
already installed natively (this is fiddly on native Windows — WSL2 is much
easier; see the Sprint 1 plan's environment notes).

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # WSL2 / Linux / macOS
pip install -r requirements.txt
cp .env.example .env          # set DB_HOST=localhost, edit DJANGO_SECRET_KEY
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Earth Engine access from Python

Earth Engine is optional. Only `build_training_set --rain-source
earth-engine` uses it, to average ERA5-Land rainfall over each county; the
default source is Open-Meteo and needs no account. To use it, set
`EE_PROJECT_ID` in `.env` and authenticate once per machine:

```bash
docker compose run --rm web earthengine authenticate --auth_mode=notebook
```

`riskengine/ml/README.md` ("Running it") has the full sequence.

## Full demo from a clean database

Four commands bring the whole walking skeleton up with **real** data. Run them
in order; each is safe to re-run.

```bash
docker compose up -d                  # PostGIS, API and the daily refresh
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py seed_regions    # 47 county boundaries
docker compose run --rm web python manage.py seed_regions --level sub_county  # 290 sub-counties
docker compose run --rm web python manage.py seed_terrain    # real elevation + slope (Open-Meteo DEM)
docker compose run --rm web python manage.py seed_rainfall   # real daily rainfall, last 61 days
docker compose run --rm web python manage.py score_regions   # one RiskScore per county and sub-county for today
docker compose run --rm web python manage.py flood_outlook   # county outlook for the next three months
docker compose run --rm web python manage.py flood_outlook --level sub_county
```

Then start the map (see below). `seed_terrain` and `seed_rainfall` need network
access; everything after that works offline, because the values are in the
database.

While the stack is up, the `scheduler` service keeps the map current: it runs
`refresh_scores` (`seed_rainfall` then `score_regions`) when it starts and once
a day after that. The dashboard shows the date the scores are for, and says so
when they are more than two days old. To refresh by hand:

```bash
docker compose run --rm web python manage.py refresh_scores
```

The seasonal outlook is not part of the daily refresh; run `flood_outlook`
when a new monthly forecast is out.

### Where the numbers come from

| Input | Source | Real? |
|---|---|---|
| County boundaries | geoBoundaries KEN ADM1, pinned commit | yes |
| Elevation, slope | Open-Meteo elevation API (Copernicus DEM GLO-90) | yes |
| Daily rainfall | Open-Meteo `precipitation_sum`, past days only | yes |
| Distance to river | not ingested | left null, never invented |
| **Risk score** | `riskengine/ml/infer.py` | trained model, modest skill |

The inputs are measurements. The scorer is a random forest trained on
recorded floods from 1996 to 2021. Its output is the chance that a large
flood is recorded in a region in a 30-day window. `riskengine/ml/README.md`
says how it was trained, how well it does and where it is weak; read it
before presenting a result. Every row and API response carries the
`model_version` that produced it.

## Dashboard (Client Tier)

The Leaflet map lives in `frontend/` as a standalone Vite app, served on
http://localhost:5173 in development (already allowed in
`CORS_ALLOWED_ORIGINS`). With the API running and the database seeded and
scored:

```bash
cd frontend
npm install
npm run dev
```

See `frontend/README.md` for the API-URL configuration and why the client is a
separate app rather than a Django template.

## Tests and checks

```bash
docker compose run --rm web python manage.py test
ruff check .          # pip install ruff; rules are in ruff.toml
```

GitHub Actions runs both on every pull request into `develop` and `main`
(`.github/workflows/ci.yml`), using the same `docker-compose.yml`, so the
check tests what runs locally.

## Status

As of 7 October 2026, on `develop`:

- **Data.** 47 counties and 290 sub-counties, with real terrain, daily
  rainfall and drainage measurements.
- **Model.** A random forest trained on Google Colab on recorded floods from
  1996 to 2021, with cross-validation by year, a final test on 2017 to 2021
  and a check against UNOSAT satellite flood extents. Its skill is modest;
  `riskengine/ml/README.md` has the figures and the limits.
- **API and dashboard.** Risk scores for today, refreshed daily, and a
  seasonal outlook, for counties and sub-counties. Reading needs no account.
  An administrator can sign in and rescore.
- **Checks.** Tests and lint run on every pull request.

Not done yet: deployment (the system runs locally only), a performance test
and user testing. `docs/GAP_CLOSURE_PLAN.md` is the current task list.
