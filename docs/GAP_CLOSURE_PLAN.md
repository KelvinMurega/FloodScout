# FloodScout — Gap Closure Plan

Written 5 October 2026. Closes the gaps found when the project was checked
against the lecturer's Sprint I and II brief and the Chapter 5 guideline.
Each phase is one or more branches off `develop`, merged by pull request.
Phases are in the order they should be done; later ones depend on earlier
ones only where stated.

This supersedes the "Immediate next actions" list in
`IMPLEMENTATION_PLAN.md`, which describes the repository as it was on
20 September.

## Summary

Status as of 8 October 2026. `docs/IMPLEMENTATION_GUIDE.md` is the working
plan from 7 October onwards and has the detailed progress; this table is
kept in step with it.

| Phase | Closes | Status | Still needs from Kelvin |
|---|---|---|---|
| 0. Repository clean-up | Stale PRs, `develop` behind `main`, uncommitted model, stale READMEs | Done | — |
| 1. Evaluation evidence in code | Per-fold and error figures have no committed source; outlook from old model | Done (#13) | — |
| 2. Independent test | No held-out test set | Done (#14, #15, #18) | — |
| 3. Chapter 3 promises | No hyperparameter search, no explainability, no error breakdown | Code done (#17); the report text is open | Supervisor's view on SHAP; the deviation text in Chapters 3 and 5 |
| 4. Access control | No login, no administrator function | Done (#22) | — |
| 5. CI and repository evidence | No CI, licence, tags or task record | Done, except supervisor access (issue #27) | Confirm the supervisor can open the repository |
| 6. Deployment and user testing | Local only; no performance, security or user evidence | Not started | Hosting choice, 3 to 5 testers |
| 7. Report | Chapter 4 diagrams, Chapter 5 placeholders, AI example | Open. Chapter 5 has the dashboard screenshots (#69, not yet merged) and the AI example; the diagrams, the remaining square-bracket items and `AI_USE_LOG.md` are open | Mostly Kelvin's own work |

Work added after this plan was written, tracked in the implementation guide:
the extra ML evidence (#63) and the five dashboard and administration
features from Chapter 4 (#64 to #68) are built and wait to be merged.

Phases 0 to 2 matter most for the Sprint II review. Phase 6 is Sprint III.

---

## Phase 0 — Repository clean-up

State on 5 October: `develop` is 10 commits behind `main` and has nothing
`main` lacks. The default branch on GitHub is `main`. Five pull requests into
`develop` are open and all mergeable.

1. Merge **#10**, **#6** and **#8** in that order. *Done 5 October.* #10 was
   cut on 26 September, so it did not carry the later work; #6 and #8 hold
   commits that reached `main` through #11 and merged cleanly.
2. Merge **#7** (configurable API port). *Done 5 October.* Port 8000 is taken
   on the development machine.
3. Decide **#9** (Earth Engine SRTM and CHIRPS client, not in `main`).
   *Closed without merging, 7 October.* The Sentinel-1 label work it was
   kept for is not planned: the flood archive supplies the labels, and ERA5
   and the Copernicus DEM replaced CHIRPS and SRTM. Nothing imports the
   module, and the rest of the pull request (the `earthengine-api`
   requirement, the credential volume, the four-tier README) reached
   `develop` by other pull requests. The branch is kept, so the client can
   be recovered.
4. Set `develop` as the default branch on GitHub so new PRs target it.
   *Done.*
5. On `feat/model-accuracy-improvements` (*done 5 October*, pull request
   #12):
   - commit the retrained `flood_model.joblib` and `metrics.json`;
   - review and commit, or discard, the three uncommitted frontend files;
   - update the results table in `riskengine/ml/README.md` and the
     "Where the numbers come from" section of `README.md`, both of which
     still describe the 1 October logistic regression;
   - open the PR into `develop`.
6. Delete `docs/REPO_STATE.md`, which describes 20 September, and point
   `IMPLEMENTATION_PLAN.md` at this file. *Done 5 October.*

**Done when:** no open PR is older than the work in progress, `develop`
contains everything in `main`, `git status` is clean, and both READMEs name
the Random Forest model.

## Phase 1 — Evaluation evidence in code

Branch: `feat/evaluation-evidence`

Tables 5.16 and 5.18 of the Chapter 5 draft came from a one-off script. The
guideline asks for variation across folds and error analysis, and the sprint
brief asks for each result to be traceable to its code and data version.

1. In `riskengine/ml/train.py`, add to `metrics.json`:
   - average precision and ROC AUC per fold, with the held-out years, record
     count and flood count of each fold, and the mean and standard deviation;
   - the confusion matrix, precision, recall and specificity at each band
     boundary (5%, 10%, 20%);
   - the Git commit hash, a SHA-256 of `training_set.csv`, the random seed
     and the scikit-learn version.
2. Add tests in `riskengine/tests_model.py` for the new keys.
3. Retrain with `train_model`. The scores must match the current
   `metrics.json`, because data and seed are unchanged. If they do not, stop
   and find out why.
4. Rerun `score_regions`, then `flood_outlook` for both levels, so the stored
   outlook comes from the same model as the scores.
5. Add the per-fold table to `notebooks/train_flood_model.ipynb`.

*Done 5 October* (pull request #13). The per-fold table reached the notebook
with the Colab run (#16).

**Done when:** every number in Chapter 5 Section 5.7 can be read from
`metrics.json`, and `RiskOutlook.model_version` equals
`RiskScore.model_version` for the latest rows.

## Phase 2 — Independent test

The same six folds currently select the model and report its score. Two
separate checks fix this. Do both; the first is fully under the project's
control and the second is the stronger evidence.

### 2a. Temporal hold-out

Branch: `feat/temporal-holdout`

1. Add a `--test-from YEAR` option to `train_model`. Years from that year on
   are removed before cross-validation and model selection.
2. Select the model and the calibration on the earlier years only, then score
   the held-out years once.
3. Record the result in `metrics.json` under `final_test`.
4. Use 2017 as the boundary: 1996 to 2016 for development, 2017 to 2021 for
   the final test (about 2,700 records). Fix this choice before looking at
   the result, and do not change it afterwards.
5. The deployed model is still fitted on all years. Say so in the README.

*Done 5 October.* Result: 1996 to 2016 selected Gradient Boosting, which
scored ROC AUC 0.767 and average precision 0.153 on 2017 to 2021, below the
season-only baseline's 0.177. The Logistic Regression, not selected, scored
0.275. Details are in `riskengine/ml/README.md`. This raises one decision for
the supervisor: whether to deploy the Logistic Regression as the simpler and
more stable model. Do not make that choice from the test score alone.

### 2b. Satellite validation

Branch: `feat/unosat-validation`

1. Kelvin downloads the UNOSAT flood extent products for Kenya's 2023 short
   rains and 2024 long rains from the Humanitarian Data Exchange into
   `data/external/unosat/`.
2. Write the manifest `data/external/unosat/events.json` in the format given
   in the `validate_unosat` docstring. List only the counties each product
   analysed under `covers`.
3. Run `validate_unosat`. It writes `data/processed/unosat_validation.csv`.
4. Record the product names, dates and download addresses in
   `docs/DATA_DICTIONARY.md`, and the scores in `riskengine/ml/README.md`.

*Done 5 October.* Result: ROC AUC 0.744 for November 2023 and 0.430 for
April 2024, 0.616 over both (94 county-events). The manifest is tracked at
`riskengine/ml/unosat_events.json`, not under `data/`, so the run can be
repeated. Follow-up worth doing in Phase 3: use the `CloudObstruction`
layers so that a county under cloud is left out instead of counted as dry.

**Done when:** Chapter 5 can report a score on years the model never saw,
and, if the UNOSAT products are usable, a score against labels that are not
circles. Report the result whatever it is.

## Phase 3 — Chapter 3 promises

Branch: `feat/model-tuning-and-explanation`. Depends on Phase 2a.

1. **Hyperparameter search.** Wrap each candidate in `GridSearchCV` with
   `GroupKFold` on year, run on the development years only. Keep the grid
   small (Random Forest: number of trees, minimum leaf size, maximum
   features; Gradient Boosting: depth, learning rate; Logistic Regression:
   `C`). Record the chosen settings in `metrics.json`. Report the tuned and
   untuned scores side by side. If tuning does not help, say so and keep the
   fixed settings.
2. **Explainability.** Start with permutation importance from scikit-learn,
   computed on held-out folds. It needs no new dependency. Add SHAP only if
   the supervisor requires it by name, since it adds a heavy package. Check
   that the direction of each input makes physical sense, as was done for the
   drainage inputs.
3. **Error breakdown.** Extend the Phase 1 output with false negatives and
   false positives by county and by calendar month.
4. **Deviation from the proposal.** No code. Chapters 3 and 5 must state that
   ERA5, Copernicus DEM and the flood archive replaced CHIRPS, SRTM and
   Sentinel-1 labels, and why.

*Items 1 to 3 done 6 October* (pull request #17). The search confirmed the
fixed settings for the Random Forest and the Logistic Regression and changed
Gradient Boosting's, which did not change the selected model. Permutation
importance was used and SHAP was not; whether that is accepted is still a
question for the supervisor. The cloud follow-up from Phase 2b was not done.
Item 4 is open and is Kelvin's own writing.

**Done when:** each method named in Chapter 3 Section 3.2 was either carried
out or has a stated, defensible reason for its replacement.

## Phase 4 — Access control

Branch: `feat/admin-access-control`

The Use Case Diagram gives the System Administrator tasks that the system
does not yet offer. One real administrator function with a tested permission
boundary is enough to demonstrate the design.

1. `POST /api/auth/token/` using Django REST Framework's token view.
2. An `IsAdministrator` permission class built on `User.is_administrator()`.
3. One administrator-only endpoint: `POST /api/risk-scores/rescore/`, which
   runs the scoring that `score_regions` runs. Refactor the command so both
   share one function.
4. A small login form on the dashboard and a "Rescore" button shown only to
   administrators. Keep the token in memory, not in local storage.
5. Tests in `accounts/tests.py` and `api/tests.py`, which are empty today:
   valid and invalid credentials, anonymous request refused (401), end user
   refused (403), administrator accepted, read endpoints still open.

*Done 6 October.* Also built: `POST /api/auth/logout/`, which deletes the
token, and a limit of ten sign-in attempts a minute. A Django superuser
counts as an administrator. Rescoring all 337 regions takes about a minute
in one request, which is longer than a production server's default request
timeout; settle that in Phase 6 (a longer timeout for that route, or scoring
in batches).

**Done when:** the five test cases pass and the Chapter 5 test table can
include an authentication row.

## Phase 5 — CI and repository evidence

Branch: `chore/ci-and-repo-evidence`

Items 1 and 2 *done 6 October* (pull request #21). Items 3 to 5 *done
7 October*. Item 6 is open (issue #27).

1. A GitHub Actions workflow that runs `python manage.py test` on every pull
   request into `develop` and `main`, with a `postgis/postgis:16-3.4` service
   container. Build the project's Dockerfile in the job so GDAL is present.
2. Add `ruff` as a lint step.
3. Add a `LICENSE` file, or a short access statement in the README if the
   repository stays private. *Done:* the repository stays private, so the
   README has an access statement with all rights reserved. An open-source
   licence can still be added later; the reverse is not possible.
4. Create GitHub issues for the remaining tasks in this plan and link each
   pull request to its issue. This is the traceable task record the guideline
   asks for. *Done:* issues #25 to #41.
5. After Phases 0 to 2 are merged, open `develop` into `main` and tag it
   `v0.2-sprint2`. *Done:* pull request #24; the tag is on `main`.
6. Confirm the supervisor has access to the private repository.

**Done when:** a pull request shows a passing check, and the Sprint II state
of the code can be checked out by tag.

## Phase 6 — Deployment and user testing (Sprint III)

Branch: `feat/production-deployment`. Depends on Phase 4.

1. **Choose the host with the supervisor.** The requirement that narrows the
   choice is a managed PostgreSQL with the PostGIS extension. Note the tier,
   region and cost in Chapter 5 Table 5.1.
2. **Production settings.** `DEBUG` off; the secret key read from the
   environment with no default; `ALLOWED_HOSTS` and CORS origins set to the
   real domains; `gunicorn` in place of the development server; HTTPS only.
3. **Dashboard.** `npm run build`, served as static files with
   `VITE_API_BASE_URL` pointing at the deployed API.
4. **Daily refresh.** A scheduled job running `seed_rainfall` then
   `score_regions`.
5. **Performance test.** Time `regions/` and `latest-by-region/` over
   repeated requests on the deployed system. Record the tool, the number of
   repetitions, the median and the slowest response. Agree the acceptance
   threshold from the Chapter 4 non-functional requirements first.
6. **Security checks.** Run `manage.py check --deploy`. Test the permission
   boundary from Phase 4 against the deployed system. Confirm no secret is in
   the repository history.
7. **User evaluation.** Three to five people complete set tasks: find the
   risk for a named county, open a focus region, read the outlook for next
   month. Record who took part, completion, time taken and comments. Agree
   the protocol with the supervisor, including any consent the university
   requires.

**Done when:** the system is reachable at a public address, and Chapter 5
has measured performance, a security record and user findings.

## Phase 7 — Report

No branch; this is Kelvin's own writing, done alongside the phases above.

1. **Chapter 4 diagrams.** Update the ERD and Class Diagram for `RiskOutlook`,
   `Region.level` and `Region.parent`, the two new `GeospatialFeature` fields
   and `RiskScore.model_version`. Justify each change in the text.
2. **Chapter 5 draft.** Fill every item in square brackets: objectives,
   requirement identifiers, screenshots (Figures 5.1 to 5.5), sample rows,
   browser version. Replace Section 5.7 figures after Phases 1 to 3.
3. **AI use.** `AI_USE_LOG.md` is kept with the proposal, not in this
   repository. Bring it up to date and choose one example of an
   AI suggestion that was checked, with what was accepted or rejected and
   why. The drainage inputs are a candidate.
4. **Sprint task list.** Keep the summary table at the top of this file
   current, one line per phase, and bring it to each review.

## Suggested order

| Week | Work |
|---|---|
| 1 (before the Sprint II review) | Phases 0 and 1, Phase 2a, start downloading for 2b, Chapter 5 screenshots |
| 2 | Phases 2b, 3 and 5; Chapter 4 diagrams |
| 3 (Sprint III) | Phases 4 and 6; user testing; final Chapter 5 figures |
