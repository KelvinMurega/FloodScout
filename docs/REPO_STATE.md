# FloodScout — Verified Repository State

Snapshot taken 2026-09-20. Everything below was read directly out of
`.git` — refs, reflogs, and decompressed commit/tree objects — rather
than inferred from files on disk. That distinction matters: an earlier
version of these notes claimed the Earth Engine work was merged into
`main`, which was wrong. It was inferred from seeing
`geodata/services/earth_engine.py` in the working tree, when the working
tree was simply checked out on `feat/earthengine-setup` at the time.

## Commit graph

```
9fb01ad  feat(setup): scaffold initial django project
   │     ← main, develop, feat/seed-region-boundaries
   │     ← origin/main, origin/develop
   │
   ├── 3d8ee09  feat(geodata): add Earth Engine client wrapper for CHIRPS/SRTM ingestion
   │      │     ← origin/feat/earthengine-setup
   │      └── 72a204c  chore(docker): persist earthengine credentials across rebuilds
   │                   ← feat/earthengine-setup (LOCAL ONLY — not pushed)
   │
   ├── a31c669  fix(settings): register rest_framework.authtoken app
   │            ← fix/authtoken-app  (in sync with origin)
   │
   ├── f1adfa4  feat(api): add read-only REST endpoints for regions, geodata, risk scores
   │            ← feat/api-serializers-viewsets  (in sync with origin)
   │
   └── 527cafe  docs: add sprint-by-sprint implementation plan
          └── 3f4bd49  chore(git): ignore local .worktrees/
                       ← docs/implementation-plan  (in sync with origin)
```

**Nothing is merged.** All four feature branches sit directly on
`9fb01ad`. `develop` was created from `main` and is identical to it —
zero commits of its own.

## Branch names (these are the real ones)

The directories under `.worktrees/` use hyphenated names that do **not**
match the branches they hold. Use the branch names, not the folder names:

| Actual branch | Worktree folder |
|---|---|
| `feat/earthengine-setup` | *(none — was the root checkout until 2026-09-20)* |
| `fix/authtoken-app` | `.worktrees/fix-authtoken-app` |
| `feat/api-serializers-viewsets` | `.worktrees/feat-api-serializers` |
| `docs/implementation-plan` | `.worktrees/docs-implementation-plan` |
| `feat/seed-region-boundaries` | *(root checkout, current HEAD)* |

## Current checkout

The root working tree is on **`feat/seed-region-boundaries`** (created
from `develop`, no commits yet, not pushed — it has no remote-tracking
entry in `.git/config`). This means `geodata/services/earth_engine.py` is
**not** present in the working tree right now; it lives only on
`feat/earthengine-setup`.

Work already started on this branch, uncommitted:
`geodata/management/__init__.py` and
`geodata/management/commands/__init__.py` exist, but `seed_regions.py`
does not yet.

## Things that exist only outside of git

**1. A stash holding a README change.**
`stash@{0}` = `419b9a3`, message *"On feat/earthengine-setup: WIP README
on feat/earthengine-setup (parked by seed_regions task)"*. Its `README.md`
blob (`3c738faf`) differs from both the base (`fba840a2`) and
`feat/earthengine-setup` (`b620322d`) — it is the three-tier → four-tier
architecture correction. **It exists nowhere else.** Dropping the stash
loses it.

**2. Three untracked documents in `docs/`.**
`DEV_AND_TRAINING_WORKFLOW.md`, `TECH_STACK_RATIONALE.md`, and
`TRAINING_DATA_RESEARCH.md` sit in the working tree but are on no branch —
the `docs/` tree exists only on `docs/implementation-plan`, and there it
contains just `IMPLEMENTATION_PLAN.md`. They survived the branch switch
only because checkout doesn't touch untracked files. They are not backed
up by git at all.

**3. An uncommitted rewrite of the implementation plan.**
`docs/IMPLEMENTATION_PLAN.md` is committed on `docs/implementation-plan`
as blob `2955f80e`. The copy in `.worktrees/docs-implementation-plan/`
has been rewritten since and is uncommitted.

## Merge conflicts to expect

Merging `feat/earthengine-setup` and `feat/api-serializers-viewsets` into
`develop` will conflict in **two** files, because both branches changed
them from the same base:

| File | base `9fb01ad` | `feat/earthengine-setup` | `feat/api-serializers-viewsets` |
|---|---|---|---|
| `requirements.txt` | `a6ecdbc3` | `38983e53` | `5c7d410a` |
| `geodata/tests.py` | `7ce503c2` | `a8269575` | `4bc74296` |

For `requirements.txt`, the correct resolution keeps **both** added
dependencies — `earthengine-api` *and* `djangorestframework-gis`. For
`geodata/tests.py`, both branches appended their own tests, so the
resolution keeps both sets.

Note: git will **raise** these conflicts rather than silently resolving
them. An earlier version of these notes implied `earthengine-api` could
disappear silently on merge — it can't; the risk is only a careless
"accept theirs" during conflict resolution.

## Leftover git debris

None of it is currently blocking the root repository (`.git/HEAD.lock` is
gone and branch operations are working), but it should be cleaned up:

- `.git/` root: `_s12.bak`, `_s15.bak`, `_s16.bak`, `_s17.bak`,
  `_s19.bak`, `_s20.bak`, `_s30.bak`, `_stale1.bak`, `_stale7.bak`,
  `_stale_head.lock.bak`, `_stale_head.lock.bak.2`,
  `_stale_index.lock.bak`, `_stale_index.lock.bak.2`, `tpdyjfc` — all
  zero bytes. Plus `_stale_fixauthtoken_ref.bak`, which holds the SHA
  `3d8ee09` (a stale ref backup, no longer needed).
- `.git/refs/heads/`: three zero-byte `*.lock.bak` files.
- **`.git/worktrees/*/`: live `HEAD.lock` files in all three worktrees,
  and an `index.lock` in `fix-authtoken-app`.** These are real lock files,
  not `.bak` copies — git will refuse operations inside those worktrees
  ("Unable to create '…/index.lock': File exists") until they're removed.
- Each worktree also has a `locked` file containing "initializing", which
  will block `git worktree prune`.

All of this is residue from an earlier session working around a sandbox
that couldn't delete files. Removing the `.bak`/`tpdyjfc`/lock files is
safe; run `git status` and `git fsck` afterwards to confirm.
