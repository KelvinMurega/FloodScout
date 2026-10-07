"""Pack the code and the training set for a Google Colab training run.

Writes data/processed/colab_bundle_<shortcommit>.tar.gz. Put it in Google
Drive under MyDrive/FloodScout/data/ and run
notebooks/train_flood_model.ipynb, which unpacks the newest bundle it finds
there.

The code in the bundle is read from the commit, not from the working files,
and the command refuses to run with uncommitted changes, so a run can be
traced to one commit. The bundle carries that commit in a COMMIT file, which
`train.git_commit()` reads on Colab, and the SHA-256 of the training set in
DATASET_SHA256, which the notebook checks after unpacking. The training set
itself is not in git, so it is read from data/processed/.

Run `build_training_set` first.
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from riskengine.ml import train

from .build_training_set import TRAINING_SET_PATH

# Code the notebook needs, as paths in the commit.
CODE_PATHS = ["riskengine", "geodata/__init__.py", "geodata/services", "requirements.txt"]
# The saved model is replaced by the run, so the old one is left out.
LEFT_OUT = "riskengine/ml/artifacts/"
DATASET_SHA_FILE = "DATASET_SHA256"


class BundleError(Exception):
    pass


def _git(root: Path, *args: str) -> bytes:
    try:
        done = subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise BundleError("git is not installed where this command is running.") from exc
    except subprocess.CalledProcessError as exc:
        raise BundleError(
            f"git {args[0]} failed: {exc.stderr.decode(errors='replace').strip()}"
        ) from exc
    return done.stdout


def _add_bytes(bundle: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = 0o644
    bundle.addfile(info, io.BytesIO(content))


def build_bundle(root: Path, training_set: Path, out_dir: Path) -> tuple[Path, str]:
    """Write the bundle and return (its path, the commit it was built from)."""
    if not training_set.is_file():
        raise BundleError(f"{training_set} not found. Run `manage.py build_training_set` first.")
    changes = _git(root, "status", "--porcelain").decode().strip()
    if changes:
        raise BundleError(
            "The working copy has uncommitted changes, so the bundle would not match a "
            f"commit. Commit or discard them first:\n{changes}"
        )
    commit = _git(root, "rev-parse", "HEAD").decode().strip()
    code = _git(root, "archive", "--format=tar", "HEAD", "--", *CODE_PATHS)
    dataset = training_set.read_bytes()
    meta = train.meta_path(training_set)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"colab_bundle_{commit[:7]}.tar.gz"
    with tarfile.open(path, "w:gz") as bundle:
        with tarfile.open(fileobj=io.BytesIO(code)) as archive:
            for member in archive:
                if member.name.startswith(LEFT_OUT) or not (member.isfile() or member.isdir()):
                    continue
                bundle.addfile(member, archive.extractfile(member) if member.isfile() else None)
        _add_bytes(bundle, "data/processed/training_set.csv", dataset)
        if meta.is_file():
            _add_bytes(bundle, "data/processed/training_set.meta.json", meta.read_bytes())
        _add_bytes(bundle, train.COMMIT_FILE, (commit + "\n").encode())
        _add_bytes(bundle, DATASET_SHA_FILE, (hashlib.sha256(dataset).hexdigest() + "\n").encode())
    return path, commit


class Command(BaseCommand):
    help = "Pack the committed code and the training set into a bundle for Colab."

    def handle(self, *args, **options):
        try:
            path, commit = build_bundle(
                train.REPO_ROOT, TRAINING_SET_PATH, TRAINING_SET_PATH.parent
            )
        except BundleError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Wrote {path.name} from commit {commit}"))
        self.stdout.write(f"Copy {path} to MyDrive/FloodScout/data/ in Google Drive.")
