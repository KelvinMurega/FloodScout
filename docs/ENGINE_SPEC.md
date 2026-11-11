# FloodScout engine specification

FloodScout is a geospatial flood **risk scoring engine** for Kenya (capstone
U-CS 10). It turns rainfall, climate indices, terrain and recorded floods
into a risk score for every county, sub-county and grid cell, every day, and
serves those scores through a REST API. The dashboard and the alerts are
reference clients of that API; they are not the engine.

This file says what the engine computes and what it does not. It is the
contract other modules can rely on. The status column says what is built
today and what is still being built.

## 1. The risk framework

The engine follows the standard disaster-risk framing (UNDRR terminology;
IPCC AR5): **Risk = Hazard x Exposure x Vulnerability**. It computes Hazard
and Exposure, splits Hazard into a temporal part and a spatial part
(susceptibility), and leaves Vulnerability out of scope.

For a place `p` inside county `c`, over the next 30 days `w`:

```
P(p floods in w) = P(a large flood happens in c during w)      # hazard
                 x P(p is under water | a flood happens in c)  # susceptibility

risk_index(p, w) = P(p floods in w) x exposure(p)
```

| Component | Question it answers | Varies with | Spatial unit | Status |
|---|---|---|---|---|
| **Hazard** | When is a large flood likely in this county? | Time (rain, season, ocean indices) | County, applied to its sub-counties and grid cells | Built: Random Forest `flood-random-forest-20261007`, with the dry gate |
| **Susceptibility** | Inside the county, where does the water go? | Place only (static) | 250 m pixel, aggregated to sub-county and county | Built: gradient boosting, run `20261102T1246Z_77a8329`, served per region at `regions/<id>/susceptibility/`. Context only: it does not change the hazard score or the alerts |
| **Exposure** | How many people and buildings are there? | Place only (static) | 100 m pixel, aggregated | Not started |
| **Risk index** | Combined hazard, susceptibility and exposure | Time and place | Every unit | Not started |

Once exposure exists, every response will carry all the components, so a
consumer can use hazard alone, susceptibility alone, or the combined index.

## 2. Inputs

| Input | Source | Refreshed |
|---|---|---|
| Daily rainfall per region and grid cell | ECMWF via Open-Meteo | Daily |
| 7-day rainfall forecast | Open-Meteo | Daily |
| El Niño (ONI) and Indian Ocean Dipole (DMI) | NOAA CPC and NOAA PSL | Monthly |
| Elevation, slope | Copernicus DEM GLO-90 via Open-Meteo | Once |
| Distance to river, 100-year flood zone, built-up share | OpenStreetMap, JRC/GloFAS, GHSL | Once |
| Boundaries | geoBoundaries KEN ADM1 and ADM2 | Once |
| Susceptibility features (HAND, elevation, slope, upstream area, TWI, distance to river, 100-year flood zone) and training floods | MERIT Hydro, JRC Global Surface Water, Copernicus GloFAS, Global Flood Database, via Earth Engine | When the model is retrained |
| Official warnings, named in alerts | Kenya Meteorological Department CAP feed | Hourly |
| Exposure layers (planned) | WorldPop, GHSL | Not started |

Sources, versions and licences: `docs/DATA_DICTIONARY.md` (and
`docs/DATA_SOURCES.md` for the static layers, once they exist).

## 3. Outputs

For each region and day, the engine stores and serves:

| Field | Meaning | Unit |
|---|---|---|
| `predicted_value` | Hazard: the chance that a large flood is recorded in the county within the next 30 days | Probability, 0 to 1 |
| `times_usual` | The same chance as a multiple of the national base rate (0.097) | Ratio |
| `times_season` | The same chance as a multiple of the chance in an ordinary year for this county and month | Ratio |
| `risk_category` | Low, Moderate, High or Very High, from the model's probability bands. A dry window (under 25 mm in 30 days, or under half the county's normal) is capped at Moderate | Category |
| `model_version` | The model that produced the score | Text |
| `hazard`, `susceptibility`, `exposure`, `risk_index`, `components_version` (planned) | The components of section 1 | Probability, index, people |

The engine also produces a 7-day forecast (`RiskForecast`), a three-month
outlook (`RiskOutlook`) and a 0.25 degree grid (`GridRiskScore`).

## 4. Spatial units

| Unit | Count | Notes |
|---|---|---|
| County | 47 | The unit the hazard model was trained on |
| Sub-county | 290 | Scored with its own rain and terrain, through the county model |
| Grid cell | 768 | 0.25 degrees (about 28 km), scored from the rain at its centre |
| Point | Any latitude and longitude in Kenya | Planned: `GET /api/v1/risk/point` returns the cell and region values |

## 5. Update cadence

| What | When |
|---|---|
| Rainfall pull, scores, grid, 7-day forecast | Daily (`refresh_scores`, from the scheduler). Missed days are caught up, up to a month back |
| Seasonal outlook | By hand, when a new monthly forecast is out (`flood_outlook`) |
| KMD warnings | Hourly, and before alerts are issued (`fetch_kmd_warnings`) |
| Susceptibility layer | When `notebooks/train_susceptibility.ipynb` ships a new run and `build_susceptibility` sums it up per region |
| Models | Only when a challenger wins the fixed comparison of its config and is recorded in the experiments ledger (see `riskengine/ml/README.md`) |

## 6. API

Today the engine is read at `https://api.floodscout.me/api/` (locally
`http://127.0.0.1:8010/api/`): `risk-scores/`, `risk-forecast/`,
`risk-outlook/`, `risk-grid/`, `regions/`, `regions/<id>/susceptibility/`
and `kmd-warnings/`. Reading is open; rescoring needs a System
Administrator.

Not started (refocus Phase E): a versioned contract under `/api/v1/` with an
OpenAPI 3.1 schema at `/api/v1/schema/`, read-only API keys for consuming
modules, and `docs/INTEGRATION.md` for other teams. The current paths stay
as aliases.

## 7. How good it is

Hazard model, cross-validated by year (1996 to 2021, 14,523 county-months):

| Measure | Result |
|---|---|
| ROC AUC | 0.787 |
| Average precision | 0.254 (base rate 0.097) |
| Against a season-only model | 2.05 times its average precision |
| 2017 to 2021 final test, against the season-only model | About 1.08 times. This test is now closed and will not be scored again |
| Within-county ranking, sub-county ROC AUC against UNOSAT April 2024 extents | 0.46, no better than chance |

The last line is why susceptibility was added: the hazard model knows
*when* but cannot tell places inside a county apart. The susceptibility
model was trained under a protocol committed before training
(`riskengine/ml/SUSCEPTIBILITY_PROTOCOL.md`) and scored once against the
UNOSAT extents of November 2023 and April 2024 (within-county ROC AUC):

| Scored by | Sub-counties | 250 m pixels |
|---|---|---|
| Susceptibility model | 0.915 | 0.740 |
| HAND | 0.856 | 0.753 |
| Sub-county area alone | 0.838 | |
| Hazard model | 0.461 | |

It ranks sub-counties well, but on single pixels it does not beat HAND.
That test is now closed and will not be scored again.

The hazard model's explanations (`riskengine/ml/explain.py`) agree with
SHAP on the final-test rows: the same leading group of inputs 86.7% of the
time, median rank correlation 0.96 (`riskengine/ml/artifacts/shap_check.json`).

## 8. Known limits

- The training labels are Dartmouth Flood Observatory events, each placed as
  a circle, not an outline. The hazard model is therefore county-level.
- A flood fed by rain upstream, outside the county (the Tana, for example),
  is only seen through the county's own rain.
- Probabilities run high on later years: read the scores as a ranking
  against the usual chance, not as exact odds.
- Open-Meteo's free tier limits the grid to 0.25 degrees.

## 9. Out of scope

- **Vulnerability** (poverty, housing quality, coping capacity). No
  validated, current, sub-county dataset exists to score it honestly.
- **Real-time hydraulics**: no river routing, no flood depth, no inundation
  modelling.
- **Routing between counties**: upstream rain does not flow into the score
  of a county downstream.
- **Issuing official warnings.** The CAP feed is marked `Exercise`;
  FloodScout is not a warning authority.
- Alerting, the dashboard and the data pipeline as products. Other capstones
  own them (U-CS 13, U-CS 17, U-CS 15). FloodScout's versions are
  reference clients; see `docs/ARCHITECTURE.md`.
