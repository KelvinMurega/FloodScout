# FloodScout — Project Structure & Technology Rationale

Why each piece of the stack is here. Written for the kind of question an
examiner asks at a defense — "why Django and not Flask?", "why PostGIS
and not just Postgres?" — where "because the proposal said so" is not an
answer.

## Structure

```
FloodScout/
├── floodscout/          Django project config — settings, root urls, wsgi/asgi
├── accounts/            User model with a role field (System Administrator / End User)
├── geodata/             Region, GeospatialFeature, EnvironmentalData
│   └── services/        earth_engine.py — the Google Earth Engine client wrapper
├── riskengine/          RiskScore model
│   └── ml/              training + inference code (the Machine Learning Tier)
├── api/                 DRF router, health check, the REST surface (IR-01)
├── data/                raw/ processed/ external/ — gitignored, real files live in Drive
├── notebooks/           EDA and model comparison (Chapter 3 methodology)
├── docs/                this file, the implementation plan, data research, workflow
├── Dockerfile           python:3.12-slim + GDAL/GEOS/PROJ system libraries
├── docker-compose.yml   db (postgis/postgis:16-3.4) + web services
├── requirements.txt     pinned Python dependencies
└── .env.example         committed template; the real .env is gitignored
```

The four apps are not an arbitrary split — each one maps onto entities in
the Chapter 4 design, so any model in the code traces back to a diagram:

| App | Chapter 4 origin |
|---|---|
| `accounts` | the USERS entity (ERD 4.4.1) and the `User` base class (Class Diagram 4.3.4), with the two actors from the Use Case Diagram (4.3.1) distinguished by a `role` field rather than two separate tables |
| `geodata` | REGION, GEOSPATIAL_FEATURE, ENVIRONMENTAL_DATA entities (ERD 4.4.1) |
| `riskengine` | RISK_SCORE entity plus the `RiskScoringEngine` class — the Machine Learning Tier of the four-tier System Architecture (4.4.2) |
| `api` | the Application Tier's REST interface (IR-01) |

That traceability is the point. It means "where is the ERD's
`GEOSPATIAL_FEATURE` in your code?" has a one-word answer.

## Why each technology

### Django 5.2 — not Flask or FastAPI

The deciding factor is **GeoDjango** (`django.contrib.gis`), which ships
with Django and gives spatial model fields and spatial queries as
first-class ORM features. `Region.geom` is a `MultiPolygonField`, and a
query like "which region contains this point" is ORM-level rather than
raw SQL. Doing the same in Flask or FastAPI means bolting on GeoAlchemy2
and hand-writing the spatial layer.

Three other things Django gives a solo student project for free that
matter more than they might look: **migrations** (the database schema is
versioned alongside the code, so the ERD in Chapter 4 and the actual
tables can't silently drift apart), the **admin site** (a working CRUD
interface for inspecting seeded regions and risk scores without building
any UI — invaluable during Sprint 2 data ingestion), and a complete
**authentication system**, which the Use Case Diagram's two-actor model
needs and which is genuinely fiddly to get right from scratch.

5.2 specifically because it is the current LTS release — supported
through the life of the project and beyond, which matters for a system
someone might mark or re-run a year from now.

### PostgreSQL + PostGIS — not SQLite, not plain Postgres

PostGIS adds real geometry types, spatial indexing (GiST), and spatial
functions to PostgreSQL. Without it, county boundaries would have to be
stored as text or coordinate blobs and every spatial operation done in
Python — slow, and it throws away the whole reason for using a spatial
database. SpatiaLite (the SQLite equivalent) exists but has weaker
indexing and is awkward to deploy.

Geometry is stored in **EPSG:4326 (WGS 84)** because that is what GeoJSON
and web maps expect, with **EPSG:21037 (Arc 1960 / UTM Zone 36S)**
available for any distance or area calculation that needs real metres
rather than degrees — Kenya-appropriate, so distance-to-river comes out
in units that mean something.

### Django REST Framework + `djangorestframework-gis`

DRF supplies the serializers, viewsets and router that turn models into a
REST API. The GIS extension is what makes it worth using here:
`GeoFeatureModelSerializer` turns a PostGIS geometry column straight into
a **GeoJSON Feature** — which is exactly the format Leaflet consumes.
That single serializer is the bridge between the Data Tier and the Client
Tier, and it means no hand-written geometry-to-JSON conversion anywhere
in the codebase.

### Leaflet.js — not Google Maps

Open-source, no API key, no billing account, and it renders GeoJSON
natively. For a map that draws coloured county polygons over a base
layer, it does everything needed and nothing more. Google Maps would add
a credential and a quota to a student project for no benefit here.

### Google Earth Engine (`earthengine-api`)

The alternative is downloading raw satellite archives — SRTM tiles,
CHIRPS rainfall rasters, Sentinel-1 scenes — and processing gigabytes
locally. Earth Engine holds all of them in one catalogue and, crucially,
computes **server-side**: `get_srtm_elevation()` sends a mean-reduction
over a geometry and gets back a single number, rather than pulling a
GeoTIFF to compute it locally. For a project on a laptop and a student
budget, that difference is decisive.

It is wrapped in `geodata/services/earth_engine.py` rather than called
directly so the `ee` package's global state is initialised in exactly one
place, and so tests can mock it out and run with no network or
credentials.

### Docker Compose — `postgis/postgis:16-3.4`

GDAL, GEOS and PROJ are C libraries that GeoDjango depends on, and
installing them on native Windows is notoriously painful. The container
gives an identical, working spatial database on any machine with one
command, which also means the supervisor can run the project without
fighting the same install. The `Dockerfile` installs the same system
libraries for the web service.

### `django-environ`, `django-cors-headers`, `psycopg[binary]`

`django-environ` reads configuration from `.env`, which is gitignored
while `.env.example` is committed — secrets stay out of version control
but the required settings are documented. `django-cors-headers` is needed
because the Leaflet frontend is served from a different origin in
development (`localhost:5173`), and browsers block cross-origin API calls
without the right headers. `psycopg[binary]` is the current (v3)
PostgreSQL driver, with the `[binary]` extra avoiding a local compile.

### Not yet present, by design

**scikit-learn** is deliberately absent from `requirements.txt` — there is
no ML code yet. It gets added in Phase 3 alongside the first training
code, not before. **SHAP** likewise, for the explainability work.

## Known dependency hazard

`requirements.txt` differs between two unmerged branches, and not
additively:

```
feat/earthengine-setup:  ... psycopg[binary]>=3.2, earthengine-api>=1.0
feat-api-serializers:    ... psycopg[binary]>=3.2, djangorestframework-gis>=1.0
```

Both branches were cut from `main` independently, and each added its own
dependency in the same position — so `feat-api-serializers` effectively
**replaces** `earthengine-api` with `djangorestframework-gis` rather than
adding it. Merge both into `develop` without resolving this and a fresh
`pip install -r requirements.txt` stops installing `ee`, so
`geodata/services/earth_engine.py` fails at import.

**Fix before merging:** the file needs both lines —

```
Django>=5.2,<5.3
djangorestframework>=3.15
django-cors-headers>=4.4
django-environ>=0.11
psycopg[binary]>=3.2
earthengine-api>=1.0
djangorestframework-gis>=1.0
```
