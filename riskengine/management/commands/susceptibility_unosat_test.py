"""The one UNOSAT look at a susceptibility run: SUSCEPTIBILITY_PROTOCOL.md, section 8.

Two steps, on purpose kept apart:

    # Every 250 m pixel of the test counties, from Earth Engine. Reads no label.
    docker compose run --rm web python manage.py susceptibility_unosat_test prepare

    # The look. Once per shipped version; refused if experiments.md has it.
    docker compose run --rm web python manage.py susceptibility_unosat_test look \
        riskengine/ml/artifacts/susceptibility/<run>/susceptibility_model.joblib \
        --use-the-one-look

`prepare` keeps one file per county in data/processed/susceptibility_unosat_test/
and carries on where it stopped. The test counties are those with mapped water
in either event in riskengine/ml/artifacts/unosat_validation.csv, which the
engine's own UNOSAT check wrote.

`look` burns each extent onto a grid five times finer than the pixels, so
"at least half covered" can be read off, scores the run and the baselines,
writes unosat_test.json next to the model and adds the look to experiments.md.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import subprocess
import tempfile
from pathlib import Path

import joblib
import numpy as np
from django.conf import settings
from django.contrib.gis.gdal import GDALRaster
from django.core.management.base import BaseCommand, CommandError

from geodata.models import Region
from geodata.services.earth_engine_rain import SIMPLIFY_DEG, EarthEngineError, _ee
from riskengine.ml.susceptibility import evaluate, layers, sample, train
from riskengine.ml.susceptibility import unosat_test as test

BASE = Path(settings.BASE_DIR)
WORK_DIR = BASE / "data" / "processed" / "susceptibility_unosat_test"
NUMBERS_PATH = WORK_DIR / "sub_counties.json"
GRID_PATH = WORK_DIR / "grid.json"
MANIFEST = BASE / "riskengine" / "ml" / "unosat_events.json"
COUNTY_LABELS = BASE / "riskengine" / "ml" / "artifacts" / "unosat_validation.csv"
SUB_COUNTY_LABELS = BASE / "riskengine" / "ml" / "artifacts" / "unosat_validation_sub_county.csv"
LEDGER = BASE / "riskengine" / "ml" / "experiments.md"
ENGINE_SUB_COUNTY_AUC = 0.461
# 256 x 256 pixels of 11 float bands is under 3 MB, well inside one request.
TILE = 256


def event_number(name: str) -> int:
    """20231124 from "... (UNOSAT FL20231124KEN, VIIRS)"."""
    start = name.index("FL") + 2
    return int(name[start : start + 8])


def tiles(box, transform):
    """(column, row, width, height) tiles covering `box` on the grid of `transform`."""
    scale_x, _, origin_x, _, scale_y, origin_y = transform
    first_col = math.floor((box[0] - origin_x) / scale_x)
    last_col = math.ceil((box[2] - origin_x) / scale_x)
    first_row = math.floor((box[3] - origin_y) / scale_y)
    last_row = math.ceil((box[1] - origin_y) / scale_y)
    for row in range(first_row, last_row, TILE):
        for col in range(first_col, last_col, TILE):
            yield col, row, min(TILE, last_col - col), min(TILE, last_row - row)


def tile_box(col, row, width, height, transform):
    scale_x, _, origin_x, _, scale_y, origin_y = transform
    west, north = origin_x + col * scale_x, origin_y + row * scale_y
    return west, north + height * scale_y, west + width * scale_x, north


class Command(BaseCommand):
    help = "The one UNOSAT look at a susceptibility run (SUSCEPTIBILITY_PROTOCOL.md, section 8)."

    def add_arguments(self, parser):
        steps = parser.add_subparsers(dest="step", required=True)
        prepare = steps.add_parser("prepare", help="Fetch the test pixels. Reads no label.")
        prepare.add_argument("--county", help="Only this county, for a trial.")
        look = steps.add_parser("look", help="Score the run once.")
        look.add_argument("model", type=Path, help="The run's susceptibility_model.joblib.")
        look.add_argument("--use-the-one-look", action="store_true")

    def handle(self, *args, **options):
        if options["step"] == "prepare":
            self.prepare(options["county"])
        else:
            self.look(options["model"], options["use_the_one_look"])

    # Step 1 -------------------------------------------------------------

    def test_counties(self):
        with open(COUNTY_LABELS, newline="") as handle:
            return sorted(
                {row["county"] for row in csv.DictReader(handle) if float(row["flooded_km2"]) > 0}
            )

    def prepare(self, only):
        config = sample.load_config()
        try:
            ee = _ee()
        except EarthEngineError as exc:
            raise CommandError(str(exc)) from exc
        WORK_DIR.mkdir(parents=True, exist_ok=True)

        subs = list(
            Region.objects.filter(level=Region.Level.SUB_COUNTY).order_by("county_name", "name")
        )
        numbers = {
            number: {"name": region.name, "county": region.county_name}
            for number, region in enumerate(subs, start=1)
        }
        NUMBERS_PATH.write_text(json.dumps(numbers, indent=1) + "\n")
        shapes = [
            (number, json.loads(region.geom.simplify(SIMPLIFY_DEG, True).geojson))
            for number, region in enumerate(subs, start=1)
        ]
        sub_image = sample.county_image(ee, shapes).rename("sub_county")

        grid_info = (
            ee.Image(ee.ImageCollection(layers.GFD).first())
            .select("flooded")
            .projection()
            .getInfo()
        )
        transform = grid_info["transform"]
        if transform[1] or transform[3] or transform[4] >= 0:
            raise CommandError(f"Expected a north-up grid with no shear, got {transform}.")
        GRID_PATH.write_text(json.dumps(grid_info) + "\n")
        grid = ee.Projection(grid_info["crs"], transform)
        image = layers.test_image(
            ee,
            grid,
            sub_image,
            config["derived"],
            config["data"]["permanent_water_occurrence"],
            config["sensitivity"]["lakes"],
        )

        counties = [only] if only else self.test_counties()
        in_county = {}
        for number, sub in numbers.items():
            in_county.setdefault(sub["county"], []).append(number)
        for county in counties:
            path = WORK_DIR / f"{county}.npz"
            if path.exists():
                continue
            region = Region.objects.get(level=Region.Level.COUNTY, name=county)
            parts = [
                self.fetch(ee, image, grid_info["crs"], transform, *tile)
                for tile in tiles(region.geom.extent, transform)
            ]
            pixels = {
                name: np.concatenate([part[name] for part in parts])
                for name in [*test.BANDS, "col", "row"]
            }
            keep = np.isin(pixels["sub_county"], in_county[county])
            np.savez_compressed(path, **{name: values[keep] for name, values in pixels.items()})
            self.stdout.write(f"{county}: {int(keep.sum())} pixels")

    def fetch(self, ee, image, crs, transform, col, row, width, height):
        scale_x, shear_x, origin_x, shear_y, scale_y, origin_y = transform
        request = {
            "expression": image,
            "fileFormat": "NUMPY_NDARRAY",
            "grid": {
                "dimensions": {"width": width, "height": height},
                "affineTransform": {
                    "scaleX": scale_x,
                    "shearX": shear_x,
                    "translateX": origin_x + col * scale_x,
                    "shearY": shear_y,
                    "scaleY": scale_y,
                    "translateY": origin_y + row * scale_y,
                },
                "crsCode": crs,
            },
        }
        array = ee.data.computePixels(request)
        rows, cols = np.mgrid[row : row + height, col : col + width]
        part = {name: np.asarray(array[name], dtype=np.float32).ravel() for name in test.BANDS}
        for name in test.FEATURE_BANDS:
            part[name][part[name] == layers.MISSING] = np.nan
        part["col"], part["row"] = cols.ravel().astype(np.int32), rows.ravel().astype(np.int32)
        return part

    # Step 2 -------------------------------------------------------------

    def look(self, model_path, confirmed):
        run_id = model_path.parent.name
        ledger = LEDGER.read_text(encoding="utf-8")
        if test.already_looked(ledger, run_id):
            raise CommandError(f"experiments.md already records the UNOSAT look for {run_id}.")
        if not confirmed:
            raise CommandError("This is the one look the protocol allows. Add --use-the-one-look.")
        if not GRID_PATH.exists():
            raise CommandError("Run `prepare` first.")

        grid_info = json.loads(GRID_PATH.read_text())
        numbers = {int(number): sub for number, sub in json.loads(NUMBERS_PATH.read_text()).items()}
        county_of = {number: sub["county"] for number, sub in numbers.items()}
        counties = self.test_counties()
        missing = [county for county in counties if not (WORK_DIR / f"{county}.npz").exists()]
        if missing:
            raise CommandError(f"Not prepared: {', '.join(missing)}. Run `prepare` again.")
        pixels = {county: dict(np.load(WORK_DIR / f"{county}.npz")) for county in counties}
        everything = {
            name: np.concatenate([p[name] for p in pixels.values()]) for name in test.BANDS
        }

        layer = joblib.load(model_path)["model"]
        config = sample.load_config()
        development = evaluate.load_sample(BASE / config["data"]["training_set"])
        fitted = {name: train.fit_baseline_layer(development, name) for name in evaluate.BASELINES}

        events = json.loads(MANIFEST.read_text())
        labelled = []
        for event in events:
            labels = np.concatenate(
                [self.burn(event, pixels[county], grid_info["transform"]) for county in counties]
            )
            labelled.append((event_number(event["name"]), everything, labels))
            self.stdout.write(f"{event['name']}: {int(labels.sum())} flooded pixels")

        # Pixel level
        data = test.pixel_table(labelled, county_of)
        inputs = train.matrix(data, test.FEATURE_BANDS)
        scores = {"model": layer.predict_proba(inputs)[:, 1]}
        scores |= {name: score(data) for name, score in evaluate.BASELINES.items()}
        pixel = {name: evaluate.summary(data, score) for name, score in scores.items()}
        away, _ = evaluate.without_lakes(data, scores["model"])
        lakes = {
            name: evaluate.summary(away, evaluate.without_lakes(data, score)[1])[
                "within_county_roc_auc"
            ]
            for name, score in scores.items()
        }

        # Sub-county level: expected flooded area, same labels as the engine's 0.461
        probability = {
            "model": layer.predict_proba(train.matrix(everything, test.FEATURE_BANDS))[:, 1]
        }
        for name, score in evaluate.BASELINES.items():
            column = np.nan_to_num(np.asarray(score(everything), dtype=float)).reshape(-1, 1)
            probability[name] = fitted[name].predict_proba(column)[:, 1]
        areas = {name: test.expected_area(everything, p) for name, p in probability.items()}
        areas["sub-county area"] = test.land_area(everything)
        number_of = {(sub["county"], sub["name"]): number for number, sub in numbers.items()}
        rows = test.sub_county_labels(
            SUB_COUNTY_LABELS, {e["name"]: event_number(e["name"]) for e in events}
        )
        unknown = sorted(
            {
                row["sub_county"]
                for row in rows
                if (row["county"], row["sub_county"]) not in number_of
            }
        )
        if unknown:
            raise CommandError(f"Sub-counties in the labels but not in the database: {unknown}")
        for row in rows:
            number = number_of[(row["county"], row["sub_county"])]
            for name, area in areas.items():
                # A sub-county of a county with no mapped water was not prepared.
                row[name] = area.get(number, 0.0)
        sub_county = {name: test.within_county_auc(rows, name) for name in areas}

        result = {
            "run": run_id,
            "date": dt.date.today().isoformat(),
            "commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=BASE
            ).stdout.strip(),
            "pixel": pixel,
            "pixel_away_from_lakes": lakes,
            "sub_county": sub_county,
            "engine_sub_county_roc_auc": ENGINE_SUB_COUNTY_AUC,
            "note": "UNOSAT dry pixels are not cloud-checked under protocol v1, so dry is weaker evidence than in development.",
        }
        (model_path.parent / "unosat_test.json").write_text(json.dumps(result, indent=2) + "\n")
        summary = (
            f"pixels: model {pixel['model']['within_county_roc_auc']}, hand {pixel['hand']['within_county_roc_auc']}; "
            f"sub-counties: model {sub_county['model']['within_county_roc_auc']}, "
            f"area {sub_county['sub-county area']['within_county_roc_auc']}, engine {ENGINE_SUB_COUNTY_AUC}"
        )
        LEDGER.write_text(
            test.record_look(ledger, result["date"], run_id, result["commit"][:7], summary),
            encoding="utf-8",
        )
        self.report(result)

    def burn(self, event, county_pixels, transform):
        """0 or 1 for each of the county's pixels, from the extent burned five times finer."""
        if not len(county_pixels["col"]):
            return np.zeros(0, dtype=int)
        cols, rows = county_pixels["col"].astype(int), county_pixels["row"].astype(int)
        first_col, first_row = cols.min(), rows.min()
        width, height = cols.max() - first_col + 1, rows.max() - first_row + 1
        west, south, east, north = tile_box(first_col, first_row, width, height, transform)
        layers_wanted = event["layer"] if isinstance(event["layer"], list) else [event["layer"]]
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "burned.tif"
            command = ["gdal_rasterize", "-q", "-burn", "1", "-init", "0", "-ot", "Byte"]
            for name in layers_wanted:
                command += ["-l", name]
            command += [
                "-te",
                str(west),
                str(south),
                str(east),
                str(north),
                "-ts",
                str(width * test.SUPERSAMPLE),
                str(height * test.SUPERSAMPLE),
                str(BASE / event["extent"]),
                str(out),
            ]
            subprocess.run(command, check=True)
            fine = np.asarray(GDALRaster(str(out)).bands[0].data())
        wet = test.flooded(fine.reshape(height * test.SUPERSAMPLE, width * test.SUPERSAMPLE))
        return wet[rows - first_row, cols - first_col]

    def report(self, result):
        self.stdout.write("\nPixels, within-county ROC AUC (unweighted):")
        for name, scores in result["pixel"].items():
            away = result["pixel_away_from_lakes"][name]
            self.stdout.write(
                f"  {name:>18} {scores['within_county_roc_auc']}  away from lakes {away}  "
                f"({scores['county_events']} county-events)"
            )
        self.stdout.write("\nSub-counties by expected flooded area, within-county ROC AUC:")
        for name, scores in result["sub_county"].items():
            self.stdout.write(
                f"  {name:>18} {scores['within_county_roc_auc']}  ({scores['county_events']} county-events)"
            )
        self.stdout.write(
            f"  {'current engine':>18} {result['engine_sub_county_roc_auc']}  (43 county-events)"
        )
