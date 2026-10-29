"""One way in to training, for every model the engine serves.

    python -m riskengine.ml.pipeline train --model hazard --config <file.toml>

A run is decided entirely by its config in riskengine/ml/configs/: the
training set and its SHA-256, the inputs, the folds, the settings grids,
the seeds, the primary metric and the promotion margin. Nothing that changes
the result is set in a notebook; the Colab notebooks only unpack the code,
call this, and save what it wrote.

A run writes into its own folder under riskengine/ml/runs/ and adds a line
to riskengine/ml/experiments.md, kept or not. It never replaces the model
being served: that happens only when a run is promoted by a reviewed PR.
"""
