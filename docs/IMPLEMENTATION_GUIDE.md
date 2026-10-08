# FloodScout: Shortcomings and Implementation Guide

Written 7 October 2026, after a review of the repository against the approved
proposal (Chapters 1 to 4), the course guide "Research Writing for CS 2
Projects - 2026" (Chapter 5 guidance, ML Fundamentals tab, Build Phase -
Sprints tab) and the Sprint II announcement of 5 October.

This file is the working plan for finishing the project. It supersedes the
"Status" table in `docs/GAP_CLOSURE_PLAN.md` where they disagree. Work through
the phases in order. Each phase says what to change, where, how to check it,
and what counts as done.

---

## 0. Read this first (rules for whoever does the work, human or AI agent)

1. **Branching.** Every phase is one or more branches cut from `develop`:
   `feat/...`, `fix/...`, `chore/...` or `docs/...`. Open a pull request into
   `develop`. Never commit straight to `develop` or `main`. Hotfixes branch
   from `main`.
2. **Commit messages.** `type(scope): what changed`, written in plain English,
   for example `feat(riskengine): record the git commit in Colab runs`.
3. **No AI attribution anywhere in git.** No `Co-Authored-By` trailers, no
   session links, no "Generated with" footers in commits or pull request
   descriptions. AI assistance is disclosed in `AI_USE_LOG.md` and Chapter 5
   Section 5.10 instead.
4. **Never commit** `.env`, `frontend/.env.local`, anything in `data/`,
   `.secrets/`, or credentials of any kind.
5. **The 2017 to 2021 final test is spent.** It has been scored twice already.
   Do not use it to choose features, settings or models. Any new modelling
   experiment is judged on the six grouped cross-validation folds only. If a
   change is adopted and the final test is rerun, report it as a third look
   and say so in Chapter 5.
6. **Numbers come from files, not memory.** Every figure quoted in the report
   must be readable from `riskengine/ml/artifacts/metrics.json`, a test run,
   or a file under `docs/evidence/`. If a number changes, update
   `docs/CHAPTER_5_DRAFT.md` in the same pull request.
7. **Run the tests before every pull request:**
   `docker compose run --rm web python manage.py test` (about 10 minutes) and
   `ruff check .`. CI runs both on every PR into `develop` and `main`.
8. **Report failures honestly.** An experiment that does not improve the
   model is kept and written up. Do not delete unfavourable runs.
9. The thesis is a Word document kept outside the repository. Chapter 5 text
   in the repository is `docs/CHAPTER_5_DRAFT.md`; keep it in step with the
   Word copy.

---

## 1. Where the project stands

**Working:** ingestion of rainfall, terrain, climate indices and boundaries
for 47 counties and 290 sub-counties; a trained Random Forest compared
against two baselines with year-grouped cross-validation; a final test on
2017 to 2021; validation against UNOSAT satellite flood extents; a REST API
with token sign-in and one administrator action; a Leaflet dashboard;
a daily scheduled refresh; 183 passing tests; CI; tag `v0.2-sprint2`.

**Honest rating (7 October):** engineering 8/10, ML evaluation rigour 8/10,
actual predictive skill 5/10, alignment with the proposal 5/10, overall about
6.5/10. The engineering is ahead of the write-up and of the model's skill.

---

## 2. Shortcomings

Severity: **H** = an examiner or the Sprint II review will notice, **M** =
weakens the report, **L** = polish.

### 2.1 Against the Sprint II brief (due at next week's review)

| ID | Shortcoming | Sev | Fixed in |
|---|---|---|---|
| S-01 | No sprint task list linked to objectives (no GitHub issues or project board) | H | Phase 2 |
| S-02 | Chapter 5 has no screenshots of the working system | H | Phase 9 |
| S-03 | Chapter 4 diagrams (ERD, class, schema, use case) do not show the as-built design | H | Phase 9 |
| S-04 | Deployment requirements not written down | M | Done in Chapter 5 Section 5.11.5; build in Phase 7 |
| S-05 | The "AI suggestion I checked" example existed but was not written up | M | Done in Chapter 5 Section 5.10; mirror in `AI_USE_LOG.md` (Phase 9) |

### 2.2 Against the lecturer's Colab workflow (Build Phase tab, steps 3 to 5)

| ID | Shortcoming | Sev | Fixed in |
|---|---|---|---|
| C-01 | `metrics.json` has `"git_commit": null` for the Colab run, because the uploaded bundle is not a git checkout | H | Done: run `20261007T1204Z_e3be7cb` records commit `e3be7cb` |
| C-02 | The notebook uploads `colab_bundle.tar.gz` to the temporary runtime instead of mounting Google Drive | H | Done in Phase 3 |
| C-03 | No per-run folder in Drive holding the model, metrics, plots, environment and log | H | Done in Phase 3 |
| C-04 | No run identifier connecting an output to its code and data versions | M | Done in Phase 3: the run id is in `metrics.json` and `run.json` |

### 2.3 Against the ML Fundamentals checklist ("Evidence students should prepare")

| ID | Shortcoming | Sev | Fixed in |
|---|---|---|---|
| ML-01 | One training run per configuration; no variation across random seeds | M | Done in Phase 4: `seed_variation` |
| ML-02 | Computing budget (training time, hardware) not recorded | M | Done in Phase 4: `compute` |
| ML-03 | No learning curve by training-set size | M | Done in Phase 4: `learning_curve` |
| ML-04 | Accuracy, F1 and balanced accuracy at the thresholds are computed only in the report, not stored in `metrics.json` | L | Done in Phase 4 |
| ML-05 | Final test set has been looked at twice | M | Cannot be undone; disclosed in Chapter 5 Section 5.7.7. UNOSAT serves as the new independent check |

### 2.4 Against the proposal (Chapters 1 to 4)

| ID | Shortcoming | Sev | Fixed in |
|---|---|---|---|
| P-01 | The "geospatial" claim is weak. Permutation importance ranks month first; terrain adds little; drainage inputs were dropped. The title, objective ii and Table 2.1 promise a model driven by terrain, rivers, land use and soil | H | Phase 5 (optional experiment) and Phase 9 (honest wording in Chapters 1 to 3) |
| P-02 | Data sources differ from Chapter 3 (ERA5 not CHIRPS, Copernicus DEM not SRTM, Dartmouth archive not Sentinel-1/KMD/OCHA) | H | Phase 9 (Chapter 3 rewrite); justification is in Chapter 5 Table "Deviations" |
| P-03 | SHAP proposed, permutation importance used | M | Supervisor sign-off; Phase 9 |
| P-04 | Scope: "selected regions" proposed, whole country built; sub-county scores rank no better than chance within a county | M | Phase 9 (scope wording) |
| P-05 | FR-01: no free-text location search | M | Phase 6 |
| P-06 | FR-05 / wireframes: no date filter or score history, no trend chart, no printable report or export, no "contributing factors" panel | M | Phase 6 |
| P-07 | FR-06 / use case: dataset upload and model training only from the command line | L | Phase 6 (document as a deliberate choice; optional admin action) |
| P-08 | NFR-01 (3-second load) never measured | H | Phase 8 |
| P-09 | NFR-03 usability never evaluated; no user acceptance testing | H | Phase 8 |
| P-10 | NFR-04 availability never measured; system not deployed | M | Phase 7 |
| P-11 | QGIS listed in Section 3.6 but not used anywhere visible | L | Phase 9 (describe or remove) |

### 2.5 Model weaknesses found by the evaluation (report them, fix if time allows)

| ID | Weakness | Evidence |
|---|---|---|
| W-01 | 86 of 88 June floods missed | `error_breakdown.by_month` |
| W-02 | On 2017 to 2021 the selected model beat the season-only baseline by only 1.08 times on average precision | `final_test` |
| W-03 | Probabilities about twice too high on later years | `final_test.band_calibration` |
| W-04 | April 2024 UNOSAT event ranked worse than chance (ROC AUC 0.430) | `unosat_validation.csv` |
| W-05 | Urban and coastal counties (Nairobi, Kilifi, Lamu, Kwale) missed most often | `error_breakdown.by_county` |
| W-06 | Model selection unstable between periods | `final_test.development_comparison` |

### 2.6 Repository hygiene

| ID | Problem | Fixed in |
|---|---|---|
| R-01 | 81 files are checked out with CRLF line endings while the repository stores LF, and nothing pins the rule, so they show as modified on a machine with a different `core.autocrlf` setting. `git diff --ignore-cr-at-eol` is empty | Phase 1 |
| R-02 | A `_to_delete/` folder at the repo root holds a stale `index.lock` | Phase 1 |
| R-03 | PR #9 (Earth Engine SRTM and CHIRPS client) still open with no decision | Done: closed without merging in PR #42 (issue #25) |
| R-04 | No licence file | Done: the repository stays private, so the README has an access statement with all rights reserved instead of a `LICENSE` file (PR #42, issue #26) |
| R-05 | `AI_USE_LOG.md` is referenced by the Chapter 5 draft but does not exist | Phase 9 |

### 2.7 Documentation (the Word thesis)

| ID | Problem |
|---|---|
| D-01 | Title typo on the cover: "Flmood" |
| D-02 | Duplicate references: Auma (n.d.-a / n.d.-b, and it is a 2026 paper), Gudiyangada Nachappa (2020a / 2020b), State of the Climate (n.d.-a / n.d.-b) |
| D-03 | A ReliefWeb URL ends in `?utm_source=chatgpt.com`. Remove the query string |
| D-04 | Medium/Stackademic blog cited for Scrum; the guide warns against non-academic sources. Replace with Schwaber and Sutherland, The Scrum Guide (2020) |
| D-05 | Captions "Figure .:" with no number in Chapter 4; List of Figures numbering does not match the wireframe captions (4.7 to 4.10) |
| D-06 | Typos: "wdecision-makers"; duplicated sentences in 3.4.1 and 3.4.3; stray "Webmasters Kenya" in 1.5 |
| D-07 | Chapters 1 to 4 and the abstract are in future tense; cover still says "Project Proposal, June 2026" |
| D-08 | No feedback-resolution matrix for Semester I comments; Gantt chart must run to end of November 2026 |
| D-09 | No Data (DR-xx) or Interface (IR-xx) requirements in Chapter 4, which the guide asks for |
| D-10 | Chapter 6 (Conclusions, Recommendations, Future Work) not written |

---

## 3. Implementation walkthrough

Estimated effort is for one person. Phases 1 to 4 and the screenshot part of
Phase 9 are the Sprint II priorities. Phases 6 to 8 are Sprint III.

### Phase 1: Repository hygiene (about 1 hour)

Branch: `chore/line-endings-and-cleanup`

1. Stop the CRLF noise. Create `.gitattributes` at the repo root:

   ```gitattributes
   * text=auto eol=lf
   *.bat text eol=crlf
   *.ps1 text eol=crlf
   *.png binary
   *.jpg binary
   *.joblib binary
   *.tar.gz binary
   *.xlsx binary
   *.zip binary
   ```

   Then:

   ```bash
   git add .gitattributes
   git add --renormalize .
   git status          # should now show only .gitattributes, or nothing else
   git commit -m "chore(repo): normalise line endings with .gitattributes"
   ```

   If `git status` still lists the 81 files, run `git diff --ignore-cr-at-eol`
   to confirm they are empty, then `git checkout -- .` to discard the line
   ending changes. Do this only after confirming the diff is empty.
2. Delete `_to_delete/` from the repo root (it only holds a stale lock file).
3. Check `.worktrees/` is ignored: `git check-ignore .worktrees`. If not, add
   `.worktrees/` to `.gitignore`.
4. Decide PR #9. *Done in PR #42:* closed without merging, because the
   Sentinel-1 label work its client was kept for is not planned and nothing
   imports the module. The branch is kept, so the client can be recovered if
   Phase 5.3 takes up the label work.
5. Add a licence. *Done in PR #42:* the repository stays private, so the
   README has a "Licence and access" statement with all rights reserved in
   place of a `LICENSE` file. An open-source licence such as MIT can still be
   added later if the supervisor asks for one; the reverse is not possible.
   The data licences are separate and stay in `docs/DATA_DICTIONARY.md`.

**Done when:** `git status` is clean on `develop` after merge, PR #9 is
resolved, and the README states the access terms.

### Phase 2: Sprint task list linked to objectives (about 1 hour)

Branch: none (GitHub only).

1. Create labels: `objective-iv`, `objective-v`, `sprint-2`, `sprint-3`,
   `report`, `blocked`.
   ```bash
   gh label create objective-iv --color 1f77b4 --description "Develop the scoring engine"
   gh label create objective-v  --color ff7f0e --description "Test and evaluate the engine"
   gh label create sprint-2 --color 2ca02c
   gh label create sprint-3 --color 9467bd
   gh label create report   --color 8c564b
   gh label create blocked  --color d62728
   ```
2. Create one issue per phase below (and one per shortcoming ID where useful),
   each naming its objective and the shortcoming IDs it closes, for example:
   ```bash
   gh issue create --title "Record git commit and use Google Drive in Colab runs" \
     --label objective-v,sprint-2 \
     --body "Closes C-01 to C-04 in docs/IMPLEMENTATION_GUIDE.md. Acceptance: a Colab run writes a Drive folder with model, metrics, plots, pip freeze and log, and metrics.json has a non-null git_commit."
   ```
3. Create a GitHub Project board (Table or Board view) with columns
   Todo / In progress / Done / Blocked and add every issue.
4. Close issues for work already done (UNOSAT validation, final test, CI,
   access control) so the board shows real history.
5. Reference the issue number in each PR description ("Closes #23").

**Done when:** the board can be shown at the review with each item tied to
objective iv or v.

### Phase 3: Colab provenance and Google Drive (about half a day)

Branch: `feat/colab-drive-provenance`. Closes C-01 to C-04.

**3.1 Make the commit travel with the bundle.**

In `riskengine/ml/train.py`, extend `git_commit()` so it falls back to an
environment variable and then to a `COMMIT` file shipped in the bundle:

```python
def git_commit(root: Path = REPO_ROOT) -> str | None:
    """The commit the code came from.

    Order: a real .git checkout, then the FLOODSCOUT_GIT_COMMIT environment
    variable, then a COMMIT file written into the Colab bundle.
    """
    found = _commit_from_git_dir(root)          # the existing logic, moved
    if found:
        return found
    from_env = os.environ.get("FLOODSCOUT_GIT_COMMIT", "").strip()
    if from_env:
        return from_env
    commit_file = root / "COMMIT"
    if commit_file.is_file():
        return commit_file.read_text().strip() or None
    return None
```

Add tests in `riskengine/tests_model.py`: one for the env var, one for the
`COMMIT` file (use a temporary directory as `root`), one that a real checkout
still wins.

**3.2 Build the bundle with a command, not by hand.**

Add `riskengine/management/commands/build_colab_bundle.py` that:

1. refuses to run if `git status --porcelain` is not empty (the bundle must
   match a commit);
2. writes `COMMIT` (output of `git rev-parse HEAD`) and `DATASET_SHA256`;
3. tars `riskengine/`, `geodata/services/`, `requirements.txt`,
   `data/processed/training_set.csv`, `training_set.meta.json`, `COMMIT` and
   `DATASET_SHA256` into `data/processed/colab_bundle_<shortcommit>.tar.gz`;
4. prints the file name and the commit.

Test it with a temporary directory and a mocked `git` call.

**3.3 Rewrite the notebook's setup cells.**

Replace section 2 ("Upload colab_bundle.tar.gz") of
`notebooks/train_flood_model.ipynb` with:

```python
from google.colab import drive
drive.mount("/content/drive")

import os, tarfile, datetime, json, subprocess
from pathlib import Path

DRIVE_ROOT = Path("/content/drive/MyDrive/FloodScout")
DATA_DIR   = DRIVE_ROOT / "data"
RUNS_DIR   = DRIVE_ROOT / "runs"

bundles = sorted(DATA_DIR.glob("colab_bundle_*.tar.gz"))
assert bundles, f"Put colab_bundle_<commit>.tar.gz in {DATA_DIR}"
BUNDLE = bundles[-1]                       # or set explicitly
with tarfile.open(BUNDLE) as t:
    t.extractall("/content/floodscout")
os.chdir("/content/floodscout")

COMMIT = Path("COMMIT").read_text().strip()
os.environ["FLOODSCOUT_GIT_COMMIT"] = COMMIT
RUN_ID = datetime.datetime.utcnow().strftime("%Y%m%dT%H%MZ") + "_" + COMMIT[:7]
RUN_DIR = RUNS_DIR / RUN_ID
(RUN_DIR / "figures").mkdir(parents=True, exist_ok=True)
print("bundle", BUNDLE.name, "commit", COMMIT, "run", RUN_ID)
```

After training, add a final cell that saves everything to `RUN_DIR`:

```python
import shutil, sys, platform, time
shutil.copy(train.ARTIFACT_PATH, RUN_DIR / "flood_model.joblib")
shutil.copy(train.METRICS_PATH, RUN_DIR / "metrics.json")
(RUN_DIR / "environment.txt").write_text(
    subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True).stdout)
(RUN_DIR / "run.json").write_text(json.dumps({
    "run_id": RUN_ID, "commit": COMMIT, "bundle": BUNDLE.name,
    "python": platform.python_version(), "machine": platform.processor() or platform.machine(),
    "seconds": TRAIN_SECONDS,              # set around the training cell with time.perf_counter()
}, indent=2))
```

Save every chart with `fig.savefig(RUN_DIR / "figures" / "<name>.png", dpi=150)`
as well as showing it. Write the training cell's printed output to
`RUN_DIR / "log.txt"` (wrap it with `contextlib.redirect_stdout` into a
`io.StringIO`, then print and save).

**3.4 Bring the run back.**

With Google Drive for desktop installed, copy `flood_model.joblib` and
`metrics.json` from the run folder into `riskengine/ml/artifacts/`, wait for
sync to finish first, then:

```bash
docker compose restart web
docker compose run --rm web python manage.py score_regions
docker compose run --rm web python manage.py flood_outlook
docker compose run --rm web python manage.py flood_outlook --level sub_county
```

Commit the notebook with outputs (in Colab: File > Save a copy in GitHub, or
download and commit) so the recorded code matches the run.

**Done when:** `metrics.json` shows a real `git_commit`; the Drive run folder
holds model, metrics, figures, `environment.txt`, `run.json` and `log.txt`;
`docs/CHAPTER_5_DRAFT.md` Section 5.4.5 names the run ID and commit instead of
the highlighted note.

### Phase 4: Extra ML evidence (about a day)

Branch: `feat/ml-evidence`. Closes ML-01 to ML-04. Uses cross-validation only.

**4.1 Seed variability.** Make the seed a parameter: `train.train(...,
random_state=RANDOM_STATE)` passed through to every estimator that takes one.
Add `--seeds 0 1 2 3 4` to `train_model`. For each seed run the six-fold
cross-validation (no final test), and write to `metrics.json`:

```json
"seed_variation": {
  "seeds": [0, 1, 2, 3, 4],
  "random-forest":      {"average_precision": {"mean": 0.0, "sd": 0.0}, "roc_auc": {"mean": 0.0, "sd": 0.0}},
  "gradient-boosting":  {"...": "..."},
  "logreg":             {"note": "deterministic"}
}
```

Logistic Regression is deterministic, so say so rather than repeating it.
The deployed model stays seed 0.

**4.2 Computing budget.** Time each stage with `time.perf_counter()` and
store `"compute": {"seconds_cross_validation": ..., "seconds_final_fit": ...,
"cpu": platform.processor(), "cores": os.cpu_count()}` in `metrics.json`.

**4.3 Learning curve by training size.** Inside each grouped fold, train the
selected model on 25%, 50%, 75% and 100% of the training *years* (sample whole
years, never rows, with a fixed seed) and score the same held-out fold. Store
`"learning_curve": [{"fraction": 0.25, "average_precision_mean": ..., "sd": ...}, ...]`
and add a notebook chart `11_learning_curve.png`. Interpretation for the
report: if the curve is still rising at 100%, more labelled flood records
would help; if flat, the inputs or labels are the limit.

**4.4 Threshold metrics in the artefact.** Extend each `threshold_errors`
row (cross-validation and `final_test`) with `accuracy`, `f1`,
`balanced_accuracy` and `support_positive` / `support_negative`. Chapter 5
Table "Classification Metrics at the Two Warning Thresholds" already contains
these values; the stored ones must match to three decimals.

**4.5 Tests.** Add tests that the new keys exist, that the learning curve
samples whole years, and that seed 0 reproduces the current scores exactly.

**4.6 Retrain** on Colab through Phase 3's notebook. Cross-validation scores
for seed 0 must equal the current `metrics.json`. If they do not, stop and
find out why before changing anything else.

**Done when:** `seed_variation`, `compute` and `learning_curve` are in
`metrics.json`, the new chart is in `docs/figures/`, and Chapter 5 gains a
short "Repeated runs and computing budget" paragraph and the learning curve
figure.

### Phase 5: Optional modelling experiments (one or two days, supervisor first)

Branch: `feat/june-and-geospatial-experiments`. Addresses W-01, W-05 and P-01.
Agree with the supervisor before starting. Each experiment is one controlled
change, judged on cross-validation against the current model, and written up
whether it helps or not (course guide, Build Phase step 7).

**5.1 June floods (W-01).** Hypothesis: the model treats June as dry because
it only sees 30-day rainfall. Add `rain_60d` and `rain_90d` (and their ratios
to normal, normals refitted per fold) in `build_training_set` and
`riskengine/ml/features.py` (one place, used by training and scoring). Measure
June recall at the 10% threshold and overall average precision.

**5.2 Short intense rain (W-05).** Add `rain_3d_max` (wettest three days) and
`days_over_50mm`. Measure missed share for Nairobi and the coastal counties.

**5.3 Geospatial signal (P-01).** The drainage inputs failed because the
circle labels favour large counties. Two routes, in order of cost:
- Express drainage inputs as interactions with rainfall, for example
  `rain_30d_ratio * flood_zone_fraction`, keeping `log_area` as the control.
  Cheap; may still be confounded.
- Better labels: derive flood extents from Sentinel-1 for a few events through
  Earth Engine (the school account `kelvin.murega@strathmore.edu` is set up,
  and PR #9 holds a client). Expensive; only if time remains after Phase 8.

Rules: keep the inputs list in one place; rerun `pytest`-style model tests;
do not touch the final test to decide. If a change is adopted, retrain via
Phase 3, rescore, and update Chapter 5 Sections 5.4, 5.7 and 5.8.

**Done when:** each experiment has a row in a new "Experiments log" table in
`riskengine/ml/README.md` (date, change, cross-validated AP before and after,
June recall before and after, decision).

### Phase 6: Missing features from Chapter 4 (about two days)

Branch per feature. Closes P-05 to P-07. Keep each small; the wireframes are
the specification.

**6.1 Location search (FR-01).** `frontend/src/main.js`: add a search input
above the region list. Filter the already-loaded GeoJSON features by name
(case-insensitive, accent-insensitive). On Enter or click, call the existing
`select(regionId, { pan: true })`. For a sub-county, load its focus region
first via `showRegion`. No new API needed. Test: typing "kisu" offers Kisumu.

**6.2 Score history and date filter (FR-05).**
- API: add `GET /api/risk-scores/?region=<id>&date_after=YYYY-MM-DD&date_before=YYYY-MM-DD`
  using a `django-filter` FilterSet (or a manual `get_queryset` with
  validation returning HTTP 400 on bad dates, matching TC-15).
- Frontend: in `renderDetail`, request the last 30 days for the selected
  region and draw a small SVG line of `times_usual` (no chart library needed).
  Add a date picker that reloads `latest-by-region` for a chosen past date if
  scores exist for it.
- Tests: filter by region and dates, bad date returns 400, ordering by date.

**6.3 Printable report and export (wireframe 4.10).**
- A "Print report" button that calls `window.print()`, with a print
  stylesheet (`@media print`) that hides controls and shows: title, score
  date, model version, the map, a table of regions sorted by risk, and the
  responsible-use note from Chapter 5 Section 5.11.3.
- A "Download CSV" button that builds a CSV of the visible scores in the
  browser (region, category, times usual, probability, date, model version).

**6.4 Contributing factors (wireframe 4.8).** In the detail panel, show the
inputs the model used for that region and date (rainfall against normal,
previous 30 days, ONI, season) with the region's value and whether it is above
or below that county's usual. Serve them from a new
`GET /api/risk-scores/<id>/inputs/` that calls `features.py` for the stored
date. Label it "Inputs behind this score", not "causes". Do not show a
"model confidence %": the evaluation shows the probabilities are not
calibrated on later years.

**6.5 Administrator tasks (P-07).** Leave dataset upload and training on the
command line and say so in Chapter 4 and 5 as a deliberate choice (training
runs on Colab per the course workflow). Optionally add a Django admin action
"Rescore all regions" so the use case is reachable from the admin site.

**Done when:** each feature has tests, a screenshot in Chapter 5, and the
"Status of Functional and Non-Functional Requirements" table in Chapter 5 is
updated.

### Phase 7: Deployment (Sprint III, one to two days)

Branch: `feat/production-deploy`. Closes P-10 and S-04.

**7.1 Production settings.**
- `floodscout/settings.py` currently defaults `DEBUG` to `True`. Change the
  default to `False` and set `DJANGO_DEBUG=True` in `.env.example` for
  development.
- Require `DJANGO_SECRET_KEY` when `DEBUG` is off (fail at start-up if it is
  the insecure default).
- Add `SECURE_PROXY_SSL_HEADER`, `CSRF_TRUSTED_ORIGINS`,
  `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_HSTS_SECONDS` driven
  by environment variables.
- Run `python manage.py check --deploy` and fix every warning or document why
  it does not apply.

**7.2 Serving.**
- Add `gunicorn` to `requirements.txt`; production command
  `gunicorn floodscout.wsgi:application --bind 0.0.0.0:8000 --workers 3`.
- Build the dashboard with `npm run build` and serve `frontend/dist/` as
  static files (nginx container, or Cloudflare Pages pointing at the API
  domain). Set the API base URL through the Vite env variable at build time.
- Create `docker-compose.prod.yml` with `db` (named volume, no public port),
  `web` (gunicorn), `scheduler` (same image, `refresh_scores` daily) and
  `nginx` (TLS terminated by Cloudflare, or Caddy for automatic certificates).

**7.3 Host.** Options, roughly in order of fit. Check current pricing and
student credit before choosing; these change and were not verified for this
guide:
- A small Linux VM (Azure for Students, AWS, Google Cloud, DigitalOcean) running
  `docker-compose.prod.yml`. Closest to development and keeps PostGIS.
- A managed PostgreSQL with the PostGIS extension plus a container service for
  `web` and `scheduler`.
Record the choice and the reason in Chapter 5.

**7.4 Domain and DNS.** Buy the domain (Namecheap per the lecturer's setup),
move DNS to Cloudflare, proxy `api.<domain>` and `<domain>`, set SSL mode to
Full (strict).

**7.5 Operations.** Health check on `/api/health/` (Cloudflare or an uptime
monitor such as UptimeRobot) to produce an availability figure for NFR-04;
nightly `pg_dump` backup of the database to Drive or object storage; logs
retained for at least a week.

**Done when:** the dashboard and API are reachable over HTTPS on the domain,
`check --deploy` is clean, the scheduler refreshes daily in production, and
an uptime monitor has at least a week of data for Chapter 5.

### Phase 8: Performance and user acceptance testing (Sprint III, two to three days)

Closes P-08 and P-09. Store all evidence under `docs/evidence/`.

**8.1 Performance (NFR-01, 3 seconds).**
- API: a script `scripts/measure_latency.py` that calls
  `/api/regions/`, `/api/risk-scores/latest-by-region/` and
  `/api/regions/?focus_region=lake-basin` 50 times each against the deployed
  system and reports median and 95th percentile.
- Dashboard: run Lighthouse (Chrome DevTools, mobile and desktop) on the
  deployed site three times; record Largest Contentful Paint and Time to
  Interactive.
- Write `docs/evidence/performance.md` with the table, the date, the network
  used, and pass/fail against 3 seconds.

**8.2 User acceptance testing (NFR-03).**
- Participants: three to five people as close as possible to the intended
  users (county disaster officers, Red Cross volunteers, GIS or environment
  students as a fallback). Get informed consent; collect no personal data
  beyond role.
- Tasks (time each, record success, errors and comments):
  1. Find the current risk category for Kisumu County.
  2. Find which sub-county of Lake Basin is shown with the highest risk.
  3. Say how much more likely than usual a flood is in a Very High county.
  4. Find the seasonal outlook for next month.
  5. Find a lower-risk neighbouring county for a High-risk county.
  6. (After Phase 6) Print or export a report.
- After the tasks: the standard 10-item System Usability Scale questionnaire
  and three open questions (most useful, most confusing, what is missing).
- Write `docs/evidence/uat.md`: participants by role, task success rate and
  time, SUS score per person and mean (68 is the usual average), issues found,
  and fixes made. Turn each issue into a GitHub issue.

**Done when:** both evidence files exist and Chapter 5 has a "User acceptance
testing" and a "Performance testing" subsection, and the requirements status
table is updated for NFR-01 and NFR-03.

### Phase 9: Documentation (spread across both sprints)

**9.1 Chapter 5 (Word and `docs/CHAPTER_5_DRAFT.md`).** The Word version
was rebuilt on 7 October around the real objectives and FR/NFR IDs. Fill the
yellow-highlighted items:
- six screenshots: national view, focus region, region detail, outlook,
  error state (stop the web container and reload), pull request list;
- browser and version; number of user accounts
  (`docker compose run --rm web python manage.py shell -c "from accounts.models import User; print(User.objects.count())"`);
- supervisor decisions: Logistic Regression vs Random Forest, SHAP replacement;
- QGIS use; Chapter 2 citations in the discussion; the AI declaration;
- confirm the Copernicus DEM GLO-90 licence wording.
Then update `docs/CHAPTER_5_DRAFT.md` to match the Word text so the repository
copy is not stale. After Phases 3 to 8, add their results.

**9.2 Chapter 4.** Update the ERD, class diagram and schema for:
`Region.level` and `Region.parent`; `GeospatialFeature.flood_zone_fraction`
and `built_up_fraction`; `RiskScore.model_version`; the new `RiskOutlook`
table. Update the class diagram to show `features.py` / `infer()` instead of a
`RiskEngine` class with a model-type attribute, and the use case diagram to
show which administrator tasks are command-line only. Add DR-01.. (data
requirements: sources, update frequency, retention, no personal data) and
IR-01.. (REST/GeoJSON API, Open-Meteo and NOAA integrations). Fix the
"Figure .:" captions.

**9.3 Chapters 1 to 3.** Change proposal language to past tense for what was
done. Rewrite Chapter 3 Sections 3.2.1 to 3.2.4 to describe the data and
methods actually used (or point to the Chapter 5 deviations table). Reword
the scope and objective ii honestly (see P-01). Apply D-01 to D-07. Revise
the abstract last, with the real results.

**9.4 `AI_USE_LOG.md`** at the repo root. One row per material use: date,
tool, task, what was produced, how it was verified, accepted or rejected.
Include the drainage-input example. Keep it consistent with Chapter 5
Section 5.10.

**9.5 Chapter 6.** Conclusions that answer each research question using only
reported evidence (selected model, baseline comparison, final test, UNOSAT
result, June and urban weaknesses, limits of generalisation). Recommendations
per audience (county disaster officers, KMD/NDOC, future students), each
traceable to a result or limitation. Future work: better labels (Sentinel-1
extents), sub-county rainfall at higher resolution, river gauge data from WRA,
calibration on recent years, a proper user study. Do not claim operational
readiness.

**9.6 Appendices.** Gantt chart to the end of November 2026; feedback
resolution matrix (Semester I comment, change made, where); Turnitin report.

---

## 4. Definition of done

### Sprint II review (next week)

- [ ] Phase 1 merged; `git status` clean
- [ ] Phase 2 board shown, issues linked to objectives iv and v
- [ ] Phase 3 merged and one Colab run saved to Drive with a real commit
- [ ] Phase 4 seed variation and learning curve in `metrics.json`
- [ ] Chapter 5 screenshots inserted; Chapter 4 diagrams updated
- [ ] Live demo rehearsed: `docker compose up`, dashboard, select a region,
      outlook, sign in as administrator and rescore, show tests passing in CI
- [ ] Ready to explain: why grouped-by-year validation, why average precision,
      the June weakness, the drainage experiment as the checked AI suggestion

### Sprint III / final submission

- [ ] Phase 6 features merged with tests
- [ ] Phase 7 deployed over HTTPS on the domain, uptime monitored
- [ ] Phase 8 performance and UAT evidence written up
- [ ] Phase 9 Chapters 1 to 6, abstract, appendices, `AI_USE_LOG.md`
- [ ] Final tag `v1.0` on `main`, release notes listing what was delivered

---

## 5. Reference numbers (keep the report consistent with these)

All from `riskengine/ml/artifacts/metrics.json` unless stated.

| Item | Value |
|---|---|
| Training set | 14,523 county-months, Jan 1996 to Sep 2021, 1,406 floods (9.68%), 13 inputs |
| Validation | 6-fold cross-validation grouped by year, normals refitted per fold |
| Selected model | Random Forest, model version `flood-random-forest-20261007`, run `20261007T1353Z_07abff3`, commit `07abff3`, seed 0, scikit-learn 1.9.1 |
| Cross-validation, pooled | ROC AUC 0.787, AP 0.254, Brier 0.0798; base rate AP 0.097; season-only AP 0.124 |
| Lift | 2.62x base rate, 2.05x season-only (pooled); 1.47x season-only (fold mean) |
| Warning above 10% (CV) | recall 71.7%, precision 21.5%, specificity 71.9%, F1 0.330, accuracy 71.9%, balanced accuracy 71.8% |
| Final test 2017 to 2021 | selected Gradient Boosting (on 1996 to 2016): ROC AUC 0.801, AP 0.191; season-only AP 0.177; Logistic Regression AP 0.270 |
| UNOSAT, counties | Nov 2023 ROC AUC 0.744; Apr 2024 0.430; both 0.616 |
| UNOSAT, sub-counties within county | 0.461 (no better than chance) |
| Tests | 183 passing, 8 October 2026 (382 seconds for the first 173 on an idle laptop; 1,238 seconds for 183 under load), plus 17 dashboard tests (`npm test`) |
| Seed variation (CV, pooled AP, seeds 0 to 4) | Random Forest 0.2516 (sd 0.0027), Gradient Boosting 0.2339 (sd 0.0013), Logistic Regression 0.2385 (deterministic) |
| Learning curve (CV, fold-mean AP) | 25% 0.217, 50% 0.255, 75% 0.309, 100% 0.297 |
| Computing budget | 1,236 seconds, 2 cores, Intel Xeon 2.20 GHz (free Colab) |
| Repository | 58 commits on `develop`, 22 PRs opened, 21 merged, tag `v0.2-sprint2` (7 October 2026) |
