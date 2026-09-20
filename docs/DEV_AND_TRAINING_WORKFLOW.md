# FloodScout — Development & Training Workflow

This is the practical "where does work actually happen" picture the
module lecturer sketched on the whiteboard: local machine for code,
GitHub for version control, and Google Drive + Colab for anything
data/training-related that's too large or too GPU-hungry for either of
those. It sits alongside `docs/IMPLEMENTATION_PLAN.md` (the *what*, sprint
by sprint) as the *how*.

## The four pieces

```
 local machine  <───push/pull───>  GitHub
      |                              |
 (lightweight                   main / develop /
  coding, dataset                feature / hotfix
  prep scripts)                  branches — see
      |                          "Git workflow" in
      |                          IMPLEMENTATION_PLAN.md
      v
 Google Drive   <──mounted at──>  Google Colab
 (datasets,                       (actual model
  checkpoints,                     training — GPU)
  training logs)
```

**Local machine** — lightweight coding only: Django app code, the
`geodata/services/earth_engine.py`-style data pipeline scripts, dataset
prep/cleaning scripts, tests. Nothing GPU-heavy runs here. This is also
where an AI coding assistant (Claude, in this project's case) is used
day to day — that's a normal, expected part of the workflow, not
something to hide, and the existing `docs/` folder is the right place to
keep a short note on how AI assistance was used if the module asks for
one (mirrors the `AI_USE_LOG.md` already referenced from the README).

**GitHub** — code and configuration only, never data or model artifacts.
The repo's existing `.gitignore` already keeps `data/raw|processed|external`
and `__pycache__`/`.venv` out, which is exactly right for this split; it
should be extended to also exclude `riskengine/ml/*.joblib` and any
`*.ckpt`/`*.pt`/`*.h5` model files once training starts producing them —
those belong in Drive (below), not in git, for the same reason raw data
doesn't: they're large binary blobs that bloat a repository and don't
diff usefully. Branching model is the three-tier `main`/`develop`/
`feature`+`hotfix` flow described in `IMPLEMENTATION_PLAN.md`'s Git
workflow section.

**Google Drive** — the canonical store for everything too large for git:
datasets (raw and processed), training logs, and model checkpoints.
Suggested folder layout, mirroring the repo's own `data/` structure so
the two stay easy to reason about together:

```
FloodScout/
├── data/
│   ├── raw/            # exact mirror of the repo's data/raw — the actual files, since git only keeps the folder
│   ├── processed/
│   └── external/
├── checkpoints/         # one subfolder per training run, e.g. rf_2026-09-20/
└── logs/                 # training logs, one file or folder per run
```

Checkpoint sizes worth planning storage around: individual model
checkpoints landed in the ~1.5–2 GB range on the whiteboard's numbers
(consistent with saving a full Random Forest/Gradient Boosting ensemble
plus its training state rather than just final weights) — Drive's free
15 GB tier covers a handful of runs comfortably, but it's worth pruning
older checkpoints once a run is confirmed obsolete, and keeping only the
selected model's checkpoint long-term once Sprint 3 picks a winner.

**Google Colab** — where training actually runs, using Drive-mounted
storage:

```python
from google.colab import drive
drive.mount('/content/drive')

# Pull just the training code (or the whole repo — it's small without data/checkpoints)
!git clone https://github.com/KelvinMurega/FloodScout.git
%cd FloodScout
!git checkout develop
!pip install -r requirements.txt

DATA_DIR = '/content/drive/MyDrive/FloodScout/data'
CKPT_DIR = '/content/drive/MyDrive/FloodScout/checkpoints'
LOG_DIR  = '/content/drive/MyDrive/FloodScout/logs'
# training script reads from DATA_DIR, writes to CKPT_DIR / LOG_DIR
```

Once Sprint 3 selects a final model, the *serialized model file itself*
(the small `.joblib` FloodScout actually needs at inference time — not
the full checkpoint/training-state directory) is what gets copied into
`riskengine/ml/` for the Django app to load, or — if it's kept as a
separate service, per the four-tier System Architecture diagram's
Machine Learning Tier (Chapter 4, Section 4.4.2) — uploaded to wherever
that tier ends up hosted (see below). Either way, the *training* run
stays entirely in Colab/Drive; only the finished artifact crosses back
into the deployed system.

## Deployment & hosting (Sprint 6)

The whiteboard also names several concrete hosting/deployment options
worth deciding between now rather than at the last minute, since the
choice affects how the Machine Learning Tier is packaged in Sprint 3:

- **Application + Data Tier hosting** (the Django/DRF app and
  PostgreSQL+PostGIS): AWS, Azure, or Google Cloud are all viable; for a
  student project's budget and simplicity, a small managed Postgres +
  a single container/VM (e.g. AWS Lightsail, Azure App Service free
  tier, or GCP Cloud Run + Cloud SQL) is enough — no need for a
  multi-node setup.
- **Machine Learning Tier hosting**: either bundle the final model
  straight into the Django app (simplest — one deployable unit, fine at
  this project's scale) or serve it separately via a **HuggingFace
  Space/Inference Endpoint** if keeping it as its own service is
  preferred for architectural cleanliness matching the four-tier
  diagram. Worth picking one explicitly in the Sprint 6 deployment notes
  rather than leaving it implicit.
- **DNS/CDN**: Cloudflare (free tier) in front of whichever host is
  chosen, for TLS and basic caching.
- **Domain**: register via Namecheap; a `.studio` or similar short
  domain works, or check whether the university offers a subdomain
  under its own `.ac.ke`/`.edu`-style domain for student capstone
  projects, which can look more credible for a final-year defense than a
  generic TLD.

None of this needs deciding immediately — it belongs in Sprint 6's
"Deployment notes (target platform TBD)" line in `IMPLEMENTATION_PLAN.md`
— but the model-packaging choice (bundled vs. separate ML Tier service)
is worth settling before Sprint 3 finishes, since it changes what
`riskengine/ml/`'s `infer()` function needs to look like.

## Why this split matters for the defense

Being able to explain, concretely, why data/checkpoints live in Drive
and not git, why training happens in Colab and not on a laptop, and why
the branch model has a `develop` integration tier — is itself something
examiners ask about in a systems project. Keeping this document current
as those decisions firm up (especially the Sprint 6 hosting choice) means
that answer is already written down rather than improvised at the
defense.
