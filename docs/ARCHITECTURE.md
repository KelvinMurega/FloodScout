# FloodScout architecture

FloodScout is a geospatial flood risk scoring engine (U-CS 10). This file
shows where the engine is in the code, which modules are its reference
clients, and which modules are frozen.

## Tiers

Three tiers, as in the project proposal:

| Tier | Technology | Where |
|---|---|---|
| Data | PostgreSQL 16 with PostGIS 3.4 | `geodata/models.py`, `riskengine/models.py` |
| Application | Django 5.2 and Django REST Framework. The risk engine runs inside this tier: models are trained offline on Google Colab and loaded from `riskengine/ml/artifacts/` | `geodata/`, `riskengine/`, `api/`, `accounts/`, `alerts/` |
| Client | Leaflet.js, built with Vite | `frontend/` |

Training is not a tier: it happens on Colab from `notebooks/`, and only the
model file and its metrics come back into the repo.

## Engine and reference clients

```
              data sources (Open-Meteo, NOAA, Earth Engine, DFO, UNOSAT)
                                   |
                                   v
   geodata/  ---------------->  riskengine/  (the engine)
   regions, grid, inputs        hazard model, scoring, forecast, outlook,
                                grid; susceptibility and exposure (being built)
                                   |
                                   v
                                 api/  (the engine's contract)
                                   |
             +---------------------+---------------------+
             v                     v                     v
         frontend/              alerts/            other modules
     (reference client)    (reference client)   (U-CS 13, U-CS 17)
```

| Module | Role | State |
|---|---|---|
| `geodata/` | Regions, grid cells, static features, rainfall; the data-source clients | Active |
| `riskengine/ml/` | Feature building, training, inference, evaluation | Active |
| `riskengine/` scoring (`scoring.py`, `grid.py`, `forecast.py`, `dry.py`, `season.py`, `inputs.py`) | Computes and stores the scores | Active |
| `api/` | The engine's REST contract | Active (being versioned) |
| `riskengine/briefing.py`, `situation.py`, `advisory.py` | Text written on top of the scores | **Frozen** |
| `alerts/` | Subscriptions, codes, Warning and Watch rules, email and SMS, CAP feed | **Frozen**. Reference consumer of the engine (U-CS 13 owns alerting) |
| `accounts/` | Sign-in, social sign-in, profiles, the Analyst role | **Frozen**. Access control for the API and admin |
| `frontend/` | Dashboard, simple view, themes, phone layout | **Frozen**. Reference client (U-CS 17 owns visualisation) |
| Admin analytics and subscriber pages | Operator tools | **Frozen** |

## The freeze

Frozen modules take security fixes and bug fixes only: no new features, no
restyling, no new endpoints. There are three exceptions, and a PR that uses
one says which in a single line:

1. The detail panel shows the new risk components (hazard, susceptibility,
   exposure, overall), in an existing panel.
2. One new map layer: the static susceptibility map.
3. Changes forced by renaming or versioning the API.

Anything else proposed for a frozen module goes into `docs/FUTURE_WORK.md`.

## Daily flow

1. The scheduler runs `refresh_scores` once a day.
2. `seed_rainfall` pulls the rain for every region and cell.
3. `score_regions`, `score_grid` and `forecast_scores` score them with the
   hazard model and store `RiskScore`, `GridRiskScore` and `RiskForecast`.
4. `issue_alerts` (frozen reference client) reads the stored scores.
5. The API serves the stored scores. Nothing is computed per request
   except the explanations.
