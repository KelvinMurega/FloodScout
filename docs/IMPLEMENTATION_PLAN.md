# FloodScout — Implementation Plan

This plan breaks the build described in the proposal (Chapter 4: System
Analysis and Design) into sprints. Each sprint item is built on its own
feature branch and merged into `main` via a pull request after review —
see the "Git workflow" note at the end. Status is updated as work lands;
this file is the single source of truth for "what's next."

## Sprint 1 — Environment & Project Setup

- [x] Django 5.2 + GeoDjango + DRF + PostGIS scaffold (`accounts`,
      `geodata`, `riskengine`, `api` apps; models mirror the ERD in
      Section 4.4.1 and ClassDiagram in 4.3.4)
- [x] Docker Compose (`db` = postgis/postgis:16-3.4, `web` = custom
      Dockerfile with GDAL/GEOS/PROJ)
- [x] Git repository + GitHub (`KelvinMurega/FloodScout`, private) with a
      branch-per-feature + pull-request workflow (mirrors the convention
      studied from `IS-PROJECT-2026/E-seller`)
- [x] Google Earth Engine noncommercial project registration
      (`floodscout-166914`)
- [x] Earth Engine Python client wrapper (`geodata/services/earth_engine.py`)
      for SRTM elevation and CHIRPS rainfall, with mock-based tests
- [x] `rest_framework.authtoken` registered so `TokenAuthentication`
      (already declared in `REST_FRAMEWORK`) actually works
- [ ] Verify `docker compose up --build` end-to-end on the dev machine,
      run initial `migrate` + `createsuperuser`
- [ ] Run `earthengine authenticate` once on the dev machine

## Sprint 2 — Data Ingestion & Geodata Population

- [ ] Management command to seed `Region` rows from a Kenya county
      boundaries shapefile/GeoJSON (`geom`, `county_name`, `area_size`)
- [ ] Ingestion pipeline built on `geodata/services/earth_engine.py`:
      SRTM elevation + slope into `GeospatialFeature`, CHIRPS daily
      rainfall history into `EnvironmentalData`
- [ ] Distance-to-river computed from a hydrology/rivers layer (candidate:
      HydroSHEDS or OSM waterways clipped to Kenya)
- [ ] Sentinel-1 flood-extent pulls for historical flood events, used as
      training labels in Sprint 3
- [ ] `docs/DATA_DICTIONARY.md` documenting each field's source, units,
      and refresh cadence (the `data/` folder itself stays out of git —
      see `.gitignore`)

## Sprint 3 — Feature Engineering & ML Model

- [ ] `notebooks/` — EDA and feature engineering against the ingested
      data (Chapter 3 methodology)
- [ ] Model comparison (candidates per the proposal: Random Forest,
      Gradient Boosting) for both `predicted_value` (regression) and
      `risk_category` (classification) — FR-03/FR-04
- [ ] SHAP explainability on the selected model
- [ ] Persist the trained model under `riskengine/ml/` (joblib) with an
      `infer(region) -> (predicted_value, risk_category)` function that
      `riskengine/models.py`'s `RiskScore` creation will call

## Sprint 4 — REST API Layer

- [ ] Serializers for `Region`, `GeospatialFeature`, `EnvironmentalData`,
      `RiskScore`
- [ ] Read endpoints: list/retrieve regions, latest risk score per
      region, historical risk scores for a region (IR-01)
- [ ] Write endpoints restricted to `is_administrator()` users (role
      check from `accounts.User`), anonymous/end-user access read-only
      (matches `IsAuthenticatedOrReadOnly` already in `REST_FRAMEWORK`)
- [ ] API tests (DRF `APITestCase`) for each endpoint and the permission
      boundary

## Sprint 5 — Frontend Dashboard

- [ ] Leaflet.js map (separate small app/static bundle — Presentation
      Tier per the Architecture diagram), county polygons coloured by
      `risk_category`
- [ ] Wire the map to the Sprint 4 API (`CORS_ALLOWED_ORIGINS` already
      set for `localhost:5173`)
- [ ] Minimal login UI for administrator actions

## Sprint 6 — Testing, CI/CD, Deployment & Documentation

- [ ] Raise test coverage across all four apps (models, services, API)
- [ ] GitHub Actions workflow: run `python manage.py test` (and a
      linter) on every pull request into `main`
- [ ] Deployment notes (target platform TBD — document once chosen)
- [ ] Final write-up: update `README.md` status section, cross-check
      implementation against Chapter 4 before the project defense

## Git workflow

Every checkbox above becomes one feature branch off an up-to-date `main`
(`feat/<scope>-<description>`, or `fix/...` for bug fixes), committed with
a `type(scope): description` message, pushed, and opened as a pull request
into `main`. Review before merge, then merge with a regular merge commit —
no direct pushes to `main` once Sprint 1 is closed out. This mirrors the
workflow observed in `IS-PROJECT-2026/E-seller` and is what the module
lecturer expects.
