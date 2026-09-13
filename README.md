# FloodScout

Machine Learning-Based Geospatial Risk Scoring Engine for Flood Monitoring in Kenya.

Final year capstone project — Murega Kelvin Mutwiri (166914), ICS 4B, School of
Computing and Engineering Sciences, Strathmore University. Supervised by
Dr. Deperias Kerre.

This repo implements the design described in the project proposal, Chapter 4
(System Analysis and Design): a three-tier system — a Leaflet.js dashboard
(Presentation Tier), a Django REST API (Application Tier), and PostgreSQL +
PostGIS (Data Tier) — with a scikit-learn model behind the API doing the
actual flood risk scoring.

## Project layout

```
FloodScout/
├── floodscout/     Django project config (settings, root urls, wsgi/asgi)
├── accounts/       Custom User model (System Administrator / End User roles)
├── geodata/        Region, GeospatialFeature, EnvironmentalData models
├── riskengine/      RiskScore model, plus riskengine/ml/ for the actual
│                   scikit-learn training & inference code (later sprint)
├── api/            REST endpoints tying the above together (IR-01)
├── data/           raw/ processed/ external/ — see .gitignore, these are
│                   not committed; each Sprint 1 data source is documented
│                   in docs/ instead
├── notebooks/      Jupyter notebooks for exploratory data analysis / model
│                   comparison (Chapter 3 methodology)
└── docs/           Supporting docs (data dictionary, API notes, etc.)
```

The four Django apps map directly onto the Class Diagram and ERD in Chapter 4
(Sections 4.3.4 and 4.4.1), so a model here should always trace back to an
entity there — see the docstring on each model.

## Prerequisites

- Python 3.12+ (project targets Django 5.2 LTS)
- Docker Desktop with WSL2 backend (recommended path), **or** PostgreSQL 16 +
  PostGIS 3.4 installed natively if you'd rather not use Docker
- A Google Earth Engine account registered against project `floodscout-166914`
  (already done — see `AI_USE_LOG.md` in the proposal folder)

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

Then check:
- http://localhost:8000/api/health/ → `{"status": "ok", ...}`
- http://localhost:8000/admin/ → Django admin, log in with the superuser above

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

Once `earthengine-api` is added (next setup step), authenticate once per
machine with:

```bash
earthengine authenticate
```

then in code:

```python
import ee
ee.Initialize(project="floodscout-166914")
```

## Status

Repo and Django scaffold created as part of Sprint 1 setup (see
`FloodScout_Sprint1_Implementation_Plan.docx`). Models mirror the Chapter 4
design; views/serializers/ML pipeline are implemented sprint by sprint.
