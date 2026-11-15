# Run 20261115T1309Z_55c0130_local

- Config: `hazard_rf_era5_soil_20261115.toml` (hazard)
- Commit: `55c0130e88d3a4a9c6ecc60130dd44af30bda16a`
- Training set SHA-256: `c0d26666fdb6e9b91bcc929beb58718d8b26855f635eb8c1963f9385d47c1d2f`
- Rows: 14523, positives: 1406
- Selected: random-forest, calibration: none
- average_precision on held-out years: 0.266
- Lift over the season-only model: 2.15

## Against the champion

flood-random-forest-20261007 scored 0.2535 on a different training set, so the two are not comparable. Score both on the same training set and folds before deciding anything.

Promotion also needs the guard metrics, a shadow period and a reviewed registry PR
(riskengine/ml/README.md). The final test is closed and was not scored.
