# FloodScout architecture

FloodScout is a geospatial flood risk scoring engine (U-CS 10). This file
shows where the engine is in the code and which modules are its reference
clients.

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
     data sources (Open-Meteo, NOAA, Earth Engine, DFO, UNOSAT, KMD, ReliefWeb)
                                   |
                                   v
   geodata/  ---------------->  riskengine/  (the engine)
   regions, grid, inputs,       hazard model, scoring, forecast, outlook,
   KMD warnings                 grid, susceptibility; exposure (not started)
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
| `geodata/` | Regions, grid cells, static features, rainfall, KMD warnings; the data-source clients | Engine |
| `riskengine/ml/` | Training pipeline, hazard and susceptibility models, inference, evaluation, the experiments ledger | Engine |
| `riskengine/` scoring (`scoring.py`, `grid.py`, `forecast.py`, `dry.py`, `season.py`, `inputs.py`, `susceptibility.py`, `live.py`) | Computes and stores the scores; reads the susceptibility layer; keeps the live record | Engine |
| `api/` | The engine's REST contract | Engine |
| `riskengine/briefing.py`, `situation.py`, `advisory.py` | Text written on top of the scores | Reference client |
| `alerts/` | Subscriptions, codes, Warning and Watch rules, email, CAP feed | Reference consumer of the engine (U-CS 13 owns alerting) |
| `accounts/` | Sign-in, social sign-in, profiles, home and saved places, the Analyst role | Access control for the API and admin |
| `frontend/` | Dashboard, simple view, themes, phone layout, offline app | Reference client (U-CS 17 owns visualisation) |
| Admin analytics and subscriber pages | Operator tools | Reference client |

The reference clients were frozen while the engine was refocused; the
freeze was lifted on 5 November 2026. Work on the engine
still comes first.

## Daily flow

1. The scheduler runs `refresh_scores` once a day, and `fetch_kmd_warnings`
   every hour in between.
2. `seed_rainfall` pulls the rain for every region and cell.
3. `score_regions`, `score_grid` and `forecast_scores` score them with the
   hazard model and store `RiskScore`, `GridRiskScore` and `RiskForecast`.
4. `fetch_kmd_warnings` and `fetch_flood_reports` read KMD's warnings and
   ReliefWeb's flood reports.
5. `issue_alerts` (reference client) reads the stored scores and KMD's
   warnings.
6. The API serves the stored scores. Nothing is computed per request
   except the explanations.
