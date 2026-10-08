# FloodScout — Implementation Plan

This plan breaks the build described in the proposal (Chapter 4: System
Analysis and Design) into sprints. Each sprint item is built on its own
feature branch off `develop` and merged in via a pull request after
review — see the "Git workflow" note at the end, and
`docs/DEV_AND_TRAINING_WORKFLOW.md` for how local coding, GitHub, and
Colab/Drive-based model training fit together. Status is updated as work
lands; this file is the single source of truth for "what's next."

> **Status, 5 October 2026.** Phases 0 to 4 of the build order below are
> done: the walking skeleton, real terrain and rainfall, a trained model and
> the swap from the placeholder scorer. The sprint checklists further down
> and the "Immediate next actions" list describe the repository as it was on
> 20 September and are kept as a record. What remains is in
> `docs/IMPLEMENTATION_GUIDE.md`, which has been the current task list since
> 7 October; its "Progress" section says what is completed and what remains.
> `docs/GAP_CLOSURE_PLAN.md` is the plan it took over from.

## Current status (as of 20 September 2026)

`main`, `develop` and `feat/seed-region-boundaries` are all at `9fb01ad`
("feat(setup): scaffold initial django project"), which has the Django 5.2
+ GeoDjango + DRF + PostGIS scaffold and all four models
(`accounts.User`, `geodata.Region`/`GeospatialFeature`/
`EnvironmentalData`, `riskengine.RiskScore`) matching the corrected
Chapter 4 ERD and Class Diagram. **Four** feature branches sit directly on
`9fb01ad`, none merged, and they should go into `develop` in this order
(note the real branch names use slashes — the hyphenated names are the
`.worktrees/` folders, not the branches):

1. `feat/earthengine-setup` — the Earth Engine client wrapper
   (`geodata/services/earth_engine.py`, SRTM + CHIRPS, with mock-based
   tests) plus `earthengine-api` in `requirements.txt`, and a
   docker-compose volume so Earth Engine credentials survive rebuilds.
   It goes first because Sprint 2's ingestion work builds directly on it.
   Its tip commit `72a204c` is **not yet pushed** — push before opening
   the PR.
2. `fix/authtoken-app` — adds `rest_framework.authtoken` to
   `INSTALLED_APPS`. This isn't a nice-to-have: `REST_FRAMEWORK`
   already lists `TokenAuthentication` as a default authentication class
   without the app registered, so any authenticated API call will fail
   until this merges.
3. `feat/api-serializers-viewsets` — read-only serializers, ViewSets, and
   router URLs for all four models (`RegionViewSet`,
   `GeospatialFeatureViewSet`, `EnvironmentalDataViewSet`,
   `RiskScoreViewSet`, plus a `latest/` action on risk scores). This is
   most of Sprint 4 already done — see the updated Sprint 4 section below.
   **Two conflicts surface here**, both against `feat/earthengine-setup`:
   `requirements.txt` (keep both `earthengine-api` and
   `djangorestframework-gis`) and `geodata/tests.py` (keep both branches'
   tests).
4. `docs/implementation-plan` — this file.

Two small documentation-consistency items worth a quick pass once the
branches above are merged: `README.md`'s three-tier description of the
architecture is now out of date (Chapter 4 was corrected to a four-tier
architecture — Client, Application, Machine Learning, Data — matching
the System Architecture diagram), and there are several stray backup
files inside `.git/` (`_s*.bak`, `_stale*.bak`) left over from an earlier
troubleshooting session that are safe to delete but worth clearing out
before they cause confusion.

## Build order — working system first, model second

The sprint sections below trace to the proposal's Chapter 3 sprint plan
and stay as the record of *what* gets built. This section governs *when*,
and it deliberately differs from reading those sections top to bottom.

The problem with building strictly in sprint order is that the riskiest,
slowest item in the whole project — generating historical flood labels
from Sentinel-1 (see `docs/TRAINING_DATA_RESEARCH.md`) — sits upstream of
the only part anyone can actually look at. Build in that order and the
dashboard is the last thing to exist, gated behind the one task most
likely to slip. Build a thin working slice first and every later sprint
becomes an upgrade to something already running.

So: get the full loop working end-to-end with a **placeholder** risk
scorer, then replace the placeholder with the trained model without
touching anything around it.

**Phase 0 — unblock (days).** Create `develop`, merge the three open
branches into it, verify `docker compose up --build` + `migrate` +
`createsuperuser` actually run. Nothing new is built here; this just
closes out Sprint 1.

**Phase 1 — walking skeleton (the priority).** One vertical slice
through all four tiers, with real geometry but no real model:

- [ ] Seed `Region` with **real** Kenya county boundaries (Kisumu first) —
      pull from GADM or HDX, load via a management command. Real geometry
      matters even at this stage; a map of fake polygons proves nothing.
- [ ] `riskengine/ml/infer.py` exposing the **final interface**,
      implemented as a placeholder:
      `infer(region, date) -> (predicted_value, risk_category)`.
      First implementation is a transparent rule of thumb (e.g. score
      from elevation and recent rainfall where available, otherwise a
      fixed value) — no scikit-learn yet.
- [ ] Every `RiskScore` row records which scorer produced it
      (`model_version`, e.g. `"heuristic-stub-v0"`), surfaced in the API
      response. This is what stops a placeholder from being mistaken for
      a result, by the examiner or by you.
- [ ] Minimal Leaflet page: fetch regions as GeoJSON, fetch latest risk
      score per region, colour the polygons. Read-only, no login yet.

At the end of Phase 1 there is a demoable system: pick a region, see a
colour-coded risk map, backed by a real spatial database. The API half of
this is **already built** on `feat-api-serializers` — `RegionSerializer`
already emits GeoJSON Features and `RiskScoreViewSet.latest/` already
answers "what's the risk here". The genuinely missing pieces are only the
three above: boundary seeding, the stub scorer, and the map page.

**Phase 2 — real features (Sprint 2).** Ingest SRTM elevation/slope and
CHIRPS rainfall through `geodata/services/earth_engine.py` so
`GeospatialFeature` and `EnvironmentalData` hold real values. The map
keeps working throughout; only the inputs get better.

**Phase 3 — labels and training (Sprint 3).** The research-heavy part:
Sentinel-1 label generation per `docs/TRAINING_DATA_RESEARCH.md`, then
model training in Colab per `docs/DEV_AND_TRAINING_WORKFLOW.md`. This is
the part that can slip without the rest of the project stalling, which is
the whole point of the ordering.

**Phase 4 — swap in the model.** Replace the body of `infer()` with the
trained model, bump `model_version` to something real. If the interface
was honoured in Phase 1, nothing in the API or frontend changes. Keep the
stub in the codebase as the documented fallback for when a region has no
usable data — the Activity Diagram's "data not available" branch needs
*some* behaviour anyway.

**Phase 5 — harden (Sprints 4–6 remainder).** Write endpoints with the
`is_administrator()` permission boundary, login UI, test coverage, CI,
deployment.

**One caveat worth planning for:** Chapter 3 of the proposal describes
sprints in the research order (data → features → model → dashboard →
testing). Building in the order above means the implementation chapter
will need a short, explicit paragraph explaining that a vertical-slice
build order was adopted to de-risk delivery, with the research sprints
still completed in full. That is a defensible and fairly standard
engineering decision — but it reads much better as a stated choice than
as an unexplained discrepancy an examiner notices first.

## Sprint 1 — Environment & Project Setup

- [x] Django 5.2 + GeoDjango + DRF + PostGIS scaffold (`accounts`,
      `geodata`, `riskengine`, `api` apps; models mirror the ERD in
      Section 4.4.1 and Class Diagram in 4.3.4)
- [x] Docker Compose (`db` = postgis/postgis:16-3.4, `web` = custom
      Dockerfile with GDAL/GEOS/PROJ)
- [x] Git repository + GitHub (`KelvinMurega/FloodScout`, private) with a
      branch-per-feature + pull-request workflow (mirrors the convention
      studied from `IS-PROJECT-2026/E-seller`)
- [x] Google Earth Engine noncommercial project registration
      (`floodscout-166914`)
- [x] Earth Engine Python client wrapper (`geodata/services/earth_engine.py`)
      for SRTM elevation and CHIRPS rainfall, with mock-based tests
- [ ] `rest_framework.authtoken` registered so `TokenAuthentication`
      (already declared in `REST_FRAMEWORK`) actually works — built on
      `fix-authtoken-app`, not yet merged into `main`
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

This is the next sprint that hasn't been started — everything above it
either has code on `main` or is waiting on a branch merge.

## Sprint 3 — Feature Engineering & ML Model

- [ ] `notebooks/` — EDA and feature engineering against the ingested
      data (Chapter 3 methodology)
- [ ] Model comparison (Logistic Regression baseline, Random Forest as
      primary model, Gradient Boosting as comparison — Chapter 3,
      Section 3.2.3) for both `predicted_value` (regression) and
      `risk_category` (classification) — FR-03/FR-04
- [ ] Cross-validation, hyperparameter tuning (GridSearchCV), and
      evaluation against Accuracy, Precision, Recall, F1-Score, and
      ROC-AUC (Chapter 3, Section 3.2.4), with particular weight on
      Recall/F1 given imbalanced flood-event data
- [ ] SHAP explainability on the selected model
- [ ] Persist the trained model under `riskengine/ml/` (joblib) and
      replace the body of the Phase 1 placeholder `infer(region, date) ->
      (predicted_value, risk_category)` with it, leaving the signature
      untouched so nothing upstream changes — this is the Machine
      Learning Tier from the corrected System Architecture diagram
      (Chapter 4, Section 4.4.2), kept as its own module so it can later
      run as a separate service without changing the Application or
      Presentation Tiers

## Sprint 4 — REST API Layer

- [x] Serializers for `Region` (GeoJSON `Feature` via
      `GeoFeatureModelSerializer`), `GeospatialFeature`,
      `EnvironmentalData`, `RiskScore` — built on `feat-api-serializers`
- [x] Read endpoints: list/retrieve regions, geospatial features and
      environmental data filterable by `?region=<id>`, plus a
      `risk-scores/latest/?region=<id>` lookup — built on
      `feat-api-serializers`, not yet merged into `main`
- [ ] Write endpoints restricted to `is_administrator()` users (role
      check from `accounts.User`) — not yet built; current ViewSets are
      all `ReadOnlyModelViewSet`. This is what Sprint 2's ingestion
      pipeline will eventually write through, and what the Use Case
      Diagram's System Administrator tasks (Upload Geospatial Datasets,
      Train ML Model, Manage User Access) need
- [ ] API tests (DRF `APITestCase`) for each endpoint and the permission
      boundary — `geodata/tests.py` and `riskengine/tests.py` on
      `feat-api-serializers` already cover the read side; write-endpoint
      tests come with the write endpoints above

## Sprint 5 — Frontend Dashboard

- [ ] Leaflet.js map (separate small app/static bundle — Client Tier per
      the corrected Architecture diagram), county polygons coloured by
      `risk_category`
- [ ] Wire the map to the Sprint 4 API (`CORS_ALLOWED_ORIGINS` already
      set for `localhost:5173`)
- [ ] Minimal login UI for administrator actions
- [ ] This is the `WebGISDashboard` class from the Class Diagram
      (`renderMap()`, `applyRiskLayer(geoJSON)`, `filterByDate(date)`) —
      build the frontend module around those three operations so the
      diagram and the code stay traceable to each other

## Sprint 6 — Testing, CI/CD, Deployment & Documentation

- [ ] Raise test coverage across all four apps (models, services, API)
- [ ] GitHub Actions workflow: run `python manage.py test` (and a
      linter) on every pull request into `main`
- [ ] Deployment notes (target platform TBD — document once chosen)
- [ ] Final write-up: update `README.md` status section, cross-check
      implementation against Chapter 4 before the project defense —
      including the three-tier → four-tier correction noted above

## Git workflow

Three permanent branches, matching the workflow the lecturer sketched out
on the whiteboard (see `docs/DEV_AND_TRAINING_WORKFLOW.md` for the full
picture of local machine ↔ GitHub ↔ Colab/Drive):

- **`main`** — always deployment-ready. Nothing is pushed here directly;
  it only receives merges from `develop` at sprint/release boundaries, or
  a `hotfix/*` branch for an urgent fix.
- **`develop`** — the day-to-day integration branch. Every
  `feat/<scope>-<description>` or `fix/...` branch is cut from an
  up-to-date `develop`, committed with a `type(scope): description`
  message, pushed, and opened as a pull request **into `develop`**, not
  `main`.
- **`hotfix/*`** — cut directly from `main` for something broken in
  production that can't wait for the next `develop → main` merge; merged
  back into both `main` and `develop` so the fix isn't lost on the next
  release.

Review before every merge, then merge with a regular merge commit. This
mirrors the workflow observed in `IS-PROJECT-2026/E-seller` and is what
the module lecturer expects — the `develop` tier is the one addition
needed to match the whiteboard version exactly (the repo's branches so
far were cut straight off `main`; introduce `develop` now and retarget
the three open PRs below at it instead of `main`).

**Immediate next actions**, in order:

1. ~~Create `develop` off `main`~~ — **done** (2026-09-20, from `main`,
   the correct base). Still to do: set it as the repo's default branch on
   GitHub so new PRs target it automatically.
2. Rescue the work that exists outside of git before anything can discard
   it: `stash@{0}` holds the README three-tier → four-tier correction and
   exists nowhere else; `docs/DEV_AND_TRAINING_WORKFLOW.md`,
   `TECH_STACK_RATIONALE.md`, `TRAINING_DATA_RESEARCH.md` and
   `REPO_STATE.md` are untracked and on no branch; and this file's
   rewrite is uncommitted in its worktree. See `docs/REPO_STATE.md`.
3. Push `feat/earthengine-setup` (its tip `72a204c` is local-only), then
   merge it into `develop` via a PR.
4. Merge `fix/authtoken-app` into `develop` — a one-line fix for a real
   bug (`TokenAuthentication` configured without its app registered) that
   unblocks any authenticated endpoint testing.
5. Merge `feat/api-serializers-viewsets` into `develop`, resolving both
   conflicts: `requirements.txt` keeps **both** `earthengine-api` and
   `djangorestframework-gis`; `geodata/tests.py` keeps both branches'
   tests.
6. Merge `docs/implementation-plan` into `develop` so the plan and the
   other `docs/` files are visible there instead of only on a branch.
7. Once `develop` is stable end-to-end (Sprint 1's Definition of Done all
   green), open one PR from `develop` into `main` to close out Sprint 1
   as an actual release.
8. Clean up the leftover git debris inventoried in `docs/REPO_STATE.md` —
   including the live `HEAD.lock`/`index.lock` files inside
   `.git/worktrees/*/`, which will block git operations in those
   worktrees until removed. Confirm with `git status` and `git fsck`.
9. Start **Phase 1** (see "Build order" above), not Sprint 2 — three
   branches off `develop`, in this order:
   `feat/seed-region-boundaries`, `feat/stub-risk-scorer`,
   `feat/leaflet-dashboard`. That gets a working, demoable system before
   any model work begins.
