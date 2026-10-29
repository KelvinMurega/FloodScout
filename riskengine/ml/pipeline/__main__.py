"""python -m riskengine.ml.pipeline train --model hazard --config <file.toml> [--check]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as run_config
from . import hazard, runs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m riskengine.ml.pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    train_parser = commands.add_parser("train", help="Train a model as its config says.")
    train_parser.add_argument("--model", required=True, choices=run_config.MODELS)
    train_parser.add_argument("--config", required=True, type=Path)
    train_parser.add_argument(
        "--check",
        action="store_true",
        help="Check the config against the code and the training set, and stop.",
    )
    train_parser.add_argument("--runs-dir", type=Path, default=runs.RUNS_DIR)
    args = parser.parse_args(argv)

    try:
        config = run_config.load(args.config)
        if config.model != args.model:
            raise run_config.ConfigError(
                f"--model {args.model}, but {args.config.name} is a {config.model} config."
            )
        if args.check:
            path = hazard.check(config)
            print(f"{config.path.name} matches train.py and {path.name}.")
            return 0
        from geodata.services.climate_indices import fetch_dmi, fetch_oni

        result = hazard.run(config, fetch_oni(), fetch_dmi())
    except run_config.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    run = runs.run_id()
    folder = runs.write(run, config, result.artifact, result.metrics, args.runs_dir)
    print(runs.record(run, config, result.metrics))
    print(f"Wrote {folder}")
    print((folder / "summary.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
