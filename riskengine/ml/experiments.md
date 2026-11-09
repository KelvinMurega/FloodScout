# Experiments ledger

One line per training run and one per look at a test set, kept or not.
`python -m riskengine.ml.pipeline train` adds a run line by itself. The
reasoning behind each line is in the experiments log in `README.md`.

## Looks at held-out test sets

A test set may be scored only as often as its row allows. Every look gets a
line here, with the date, the commit and the result.

| Test set | Allowed | Status |
|---|---|---|
| Hazard final test, 2017 to 2021 | Was once per shipped model | **Closed on 29 October 2026.** About seven looks between 5 and 21 October 2026 (the 9 October retrain was the fifth). Never scored again |
| UNOSAT FL20231124KEN and FL20240426KEN, hazard model | Report only, never used to choose | Scored for `flood-random-forest-20261007` (6 October 2026) and for run `20261009T1051Z_8058da0` |
| UNOSAT FL20231124KEN and FL20240426KEN, susceptibility model | Once per shipped version (`SUSCEPTIBILITY_PROTOCOL.md`) | 1, for run `20261102T1246Z_77a8329` (9 November 2026) |
| UNOSAT FL20230321KEN | 0, kept as a spare | 0 |

| Date | Test set | Run or model | Commit | Result |
|---|---|---|---|---|
| 2026-11-09 | UNOSAT FL20231124KEN and FL20240426KEN, susceptibility model | 20261102T1246Z_77a8329 | `e7c9dcf` | pixels: model 0.7404, hand 0.7527; sub-counties: model 0.9145, area 0.8384, engine 0.461 |

## Experiments without a saved model

Comparisons by `manage.py compare_inputs` and `compare_labels`, on the
training set `367173d3...`, mean over seeds 0 to 4. Details in `README.md`,
"Experiments log".

| Date | Change | Pooled average precision, before to after | Outcome |
|---|---|---|---|
| 2026-10-09 | `rain_60d`, `rain_90d` | 0.2516 to 0.2499 | Dropped |
| 2026-10-09 | `rain_3d_max`, `heavy_days` | 0.2516 to 0.2482 | Dropped |
| 2026-10-09 | County-mean rainfall (Earth Engine) | 0.2516 to 0.2713 | Kept in CV; retrain not shipped |
| 2026-10-09 | Labels by start window | 0.2713 to 0.2566 | Not adopted |
| 2026-10-15 | Point rainfall as ERA5 every year | 0.2516 to 0.2579 | Kept; waits for a retrain |
| 2026-10-21 | `soil_moisture_start` | 0.2579 to 0.2678 | Kept in CV; waits for a retrain |
| 2026-10-21 | `soil_moisture_max` as well | 0.2678 to 0.2689 | Dropped, within the noise |
| 2026-10-21 | Labels from mapped flood water | 0.2579 to 0.2229 | Not adopted |

## Runs

Outcome is `candidate` when a run is written, then `champion`, `shadow`,
`rejected` or `retired` as it is decided.

| Run | Date | Model | Config | Selected | Primary metric, held out | Outcome |
|---|---|---|---|---|---|---|
| 20261007T1353Z_07abff3 | 2026-10-07 | hazard | `hazard_rf_20261007.toml` (written afterwards) | random-forest | average_precision 0.2535 | champion |
| 20261009T1051Z_8058da0 | 2026-10-09 | hazard | none (county-mean rainfall, before the pipeline) | gradient-boosting | average_precision 0.2782 | rejected: final test and UNOSAT disagreed with CV |
| 20261102T1246Z_77a8329 | 2026-11-02 | susceptibility | `susceptibility_v1.toml` | hist-gradient-boosting[learning_rate=0.05,max_leaf_nodes=31,max_iter=300,min_samples_leaf=100,early_stopping=False] | within_county_roc_auc 0.7918 | candidate: rule 1 says ship the model; TWI sign check explained (README); waits for the one UNOSAT look |
