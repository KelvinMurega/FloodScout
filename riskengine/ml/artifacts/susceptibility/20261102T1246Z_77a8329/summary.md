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

The UNOSAT test was not scored in this run.
