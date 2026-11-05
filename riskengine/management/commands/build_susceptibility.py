"""Score every 250 m pixel of Kenya with the shipped susceptibility layer, and
write what it says about each county and sub-county.

    # The pixels, from Earth Engine (reads no flood label), all 47 counties:
    docker compose run --rm web python manage.py susceptibility_unosat_test prepare
    docker compose run --rm web python manage.py susceptibility_unosat_test prepare --county Bomet
    ...
    docker compose run --rm web python manage.py build_susceptibility

The pixels are the ones `susceptibility_unosat_test prepare` fetches, one file
per county. That command fetches the 38 counties of the UNOSAT test; the
other nine are fetched with `--county`. This one refuses unless all 47 are
there, so no region is left out silently.

It writes `regions.json` beside the run's model, one row per region with the
summaries in riskengine/ml/susceptibility/layer.py. That file is committed:
the server reads it (riskengine/susceptibility.py) and needs neither the
pixels nor Earth Engine.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
from django.core.management.base import BaseCommand, CommandError

from geodata.models import Region
from riskengine.ml.susceptibility import layer
from riskengine.susceptibility import SHIPPED_RUN

from .susceptibility_unosat_test import NUMBERS_PATH, WORK_DIR


class Command(BaseCommand):
    help = "Sum the shipped susceptibility layer up per county and sub-county."

    def add_arguments(self, parser):
        parser.add_argument(
            "--run",
            type=Path,
            default=SHIPPED_RUN,
            help="The run folder holding susceptibility_model.joblib (default: the shipped one).",
        )
        parser.add_argument("--pixels", type=Path, default=WORK_DIR)

    def handle(self, *args, **options):
        run, folder = options["run"], options["pixels"]
        model_path = run / "susceptibility_model.joblib"
        if not model_path.exists():
            raise CommandError(f"No model at {model_path}.")
        numbers_path = folder / NUMBERS_PATH.name
        if not numbers_path.exists():
            raise CommandError(f"No {numbers_path}: run `susceptibility_unosat_test prepare`.")

        counties = sorted(
            Region.objects.filter(level=Region.Level.COUNTY).values_list("name", flat=True)
        )
        missing = [c for c in counties if not (folder / f"{c}.npz").exists()]
        if missing:
            raise CommandError(
                "Pixels missing for: "
                + ", ".join(missing)
                + ". Run `susceptibility_unosat_test prepare --county <name>` for each."
            )

        saved = joblib.load(model_path)
        if saved.get("ship") != "model":
            raise CommandError(f"{run.name} did not ship its model ({saved.get('ship')}).")
        model = saved["model"]
        numbers = {int(n): sub for n, sub in json.loads(numbers_path.read_text()).items()}

        scored = {}
        for county in counties:
            scored[county] = layer.usable_scores(dict(np.load(folder / f"{county}.npz")), model)
            self.stdout.write(f"{county}: {len(scored[county]['probability'])} pixels scored")

        line = layer.high_line([scored[c]["probability"] for c in counties])
        rows = []
        for county in counties:
            summary = layer.summarise(scored[county], line)
            rows.append({"level": "county", "county": county, "name": county, **summary[0]})
            for number, values in summary.items():
                if number:
                    sub = numbers[number]
                    rows.append(
                        {"level": "sub_county", "county": sub["county"], "name": sub["name"], **values}
                    )
        layer.ranks(rows)

        out = run / "regions.json"
        body = {
            "run": run.name,
            "built": dt.date.today().isoformat(),
            "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "high_line": round(line, 4),
            "high_percentile": layer.HIGH_PERCENTILE,
            "median_mean": {
                "county": layer.median_mean(rows, "county"),
                "sub_county": layer.median_mean(rows, "sub_county"),
            },
            "rows": rows,
        }
        out.write_text(json.dumps(body, indent=1) + "\n")
        subs = sum(row["level"] == "sub_county" for row in rows)
        self.stdout.write(
            f"Wrote {out}: {len(counties)} counties, {subs} sub-counties; "
            f"the middle sub-county's share expected under water is {layer.median_mean(rows):.2%}"
        )
