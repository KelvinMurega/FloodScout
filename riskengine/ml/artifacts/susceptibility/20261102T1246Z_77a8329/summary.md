# Run 20261102T1246Z_77a8329

- Config: `susceptibility_v1.toml`
- Commit: `77a83299bc16f256fb62cd440664ac99895fcf6e`
- Sample SHA-256: `7d45d82705eabdb5060db9b1fe3ea3932938ef1721418ae610f3d9ee5d24119c`
- Rows: 117350, events: 25
- Selected: hist-gradient-boosting[learning_rate=0.05,max_leaf_nodes=31,max_iter=300,min_samples_leaf=100,early_stopping=False]
- Within-county ROC AUC, leave one event out: 0.7918
- The same, spatial blocks: 0.7761
- Best baseline, hand: 0.7176
- Signs as expected: False
- Decision: ship the model

## UNOSAT test (the one look, 9 Nov 2026, commit `e7c9dcf`)

Scored once on FL20231124KEN and FL20240426KEN (VIIRS), protocol section 8. Full numbers in `unosat_test.json`.

Within-county ROC AUC:

| Scored by | Pixels (64 county-events) | Pixels away from lakes | Sub-counties by expected flooded area (43 county-events) |
|---|---|---|---|
| **This model** | **0.7404** | 0.6804 | **0.9145** |
| HAND | 0.7527 | 0.7801 | 0.8562 |
| 100-year flood zone | 0.6143 | 0.6135 | 0.9014 |
| Distance to river | 0.6987 | 0.7005 | 0.8505 |
| Sub-county area | | | 0.8384 |
| Current engine | | | 0.461 |

- **Sub-counties:** the model ranks flooded sub-counties within a county far better than the current engine (0.461) and better than every baseline, including area alone. Its lead over the flood zone is small (0.014).
- **Pixels:** the model does **not** beat HAND, and away from lakes it is clearly behind it (0.680 against 0.780). Development showed the opposite (0.7918 against 0.7176). So at 250 m the model's extra inputs did not carry over to these two events.
- UNOSAT dry pixels are not cloud-checked under protocol v1, so dry is weaker evidence than in development.
- This was the one look for this version. The counter in `SUSCEPTIBILITY_PROTOCOL.md` is now 1.
