"""Draw the susceptibility sample and write it with a manifest.

Writes data/processed/susceptibility_sample_v1.csv, which goes to Colab in the
bundle, and riskengine/ml/snapshots/susceptibility_sample_v1.json, which is
committed. A large event runs past the time limit of a direct request, so each
event is a batch export to a temporary Earth Engine asset, deleted once read.
Finished and running events are kept in the work folder, so a run that stops
carries on where it was.

    docker compose run --rm web python manage.py sample_susceptibility
    docker compose run --rm web python manage.py sample_susceptibility --event 4137
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from geodata.models import Region
from geodata.services.earth_engine_rain import SIMPLIFY_DEG, EarthEngineError, _ee
from riskengine.ml.susceptibility import layers, sample

DATA_DIR = Path(settings.BASE_DIR) / "data" / "processed"
OUT_PATH = DATA_DIR / "susceptibility_sample_v1.csv"
WORK_DIR = DATA_DIR / "susceptibility_work"
MANIFEST_PATH = (
    Path(settings.BASE_DIR) / "riskengine" / "ml" / "snapshots" / "susceptibility_sample_v1.json"
)
ASSET_PREFIX = "susceptibility_v1_event_"
POLL_SECONDS = 30


def done_path(event_id):
    return WORK_DIR / f"event_{event_id}.json"


def pending_path(event_id):
    return WORK_DIR / f"pending_{event_id}.json"


def extent(regions):
    boxes = [region.geom.extent for region in regions]
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


class Command(BaseCommand):
    help = "Sample flooded and dry pixels from the Global Flood Database."

    def add_arguments(self, parser):
        parser.add_argument("--event", type=int, help="Only this GFD event, for a trial run.")

    def handle(self, *args, **options):
        config = sample.load_config()
        data, derived = config["data"], config["derived"]
        try:
            ee = _ee()
        except EarthEngineError as exc:
            raise CommandError(str(exc)) from exc

        counties = list(Region.objects.filter(level=Region.Level.COUNTY).order_by("name"))
        names = dict(enumerate((region.name for region in counties), start=1))
        shapes = [
            (number, json.loads(region.geom.simplify(SIMPLIFY_DEG, True).geojson))
            for number, region in enumerate(counties, start=1)
        ]
        county = sample.county_image(ee, shapes)
        kenya = ee.Geometry.Rectangle(list(extent(counties)))

        collection = ee.ImageCollection(layers.GFD).filterBounds(kenya)
        event_ids = sorted(collection.aggregate_array("id").getInfo())
        if options["event"]:
            event_ids = [options["event"]]

        WORK_DIR.mkdir(parents=True, exist_ok=True)
        for event_id in event_ids:
            self.submit(ee, collection, event_id, county, kenya, names, data, derived)
        self.collect(ee, data)

        results = [json.loads(done_path(event_id).read_text()) for event_id in event_ids]
        if not options["event"]:
            self.write(ee, [result for result in results if "rows" in result], config)

    def submit(self, ee, collection, event_id, county, kenya, names, data, derived):
        """Start the event's export, unless it has been decided or is already running."""
        if done_path(event_id).exists() or pending_path(event_id).exists():
            return
        image = ee.Image(collection.filter(ee.Filter.eq("id", event_id)).first())
        flooded = sample.flooded_by_county(
            ee, image, county, kenya, data["permanent_water_occurrence"]
        )
        pixels = sum(flooded.values())
        if not sample.is_usable(flooded, data["min_flooded_pixels_per_event"]):
            done_path(event_id).write_text(json.dumps({"event": event_id, "flooded": pixels}))
            self.stdout.write(f"{event_id}: {pixels} flooded pixels, not used")
            return

        touched = sample.touched_counties(flooded)
        table, totals = sample.draw(ee, image, county, touched, data, derived)
        asset = f"projects/{os.environ['EE_PROJECT_ID']}/assets/{ASSET_PREFIX}{event_id}"
        task = ee.batch.Export.table.toAsset(
            collection=table,
            description=f"{ASSET_PREFIX}{event_id}",
            assetId=asset,
            overwrite=True,
        )
        task.start()
        pending = {
            "event": event_id,
            "task": task.id,
            "asset": asset,
            "counties": [names[number] for number in touched],
            "names": {str(number): names[number] for number in touched},
            "pixels_in_area": totals,
        }
        pending_path(event_id).write_text(json.dumps(pending))
        self.stdout.write(f"{event_id}: export started, {totals} in {len(touched)} counties")

    def collect(self, ee, data):
        """Wait for every running export, and save each one as it finishes."""
        while pending := sorted(WORK_DIR.glob("pending_*.json")):
            for path in pending:
                job = json.loads(path.read_text())
                status = ee.data.getTaskStatus(job["task"])[0]
                if status["state"] == "COMPLETED":
                    self.finish(ee, job, data)
                    path.unlink()
                elif status["state"] in ("FAILED", "CANCELLED"):
                    path.unlink()
                    raise CommandError(
                        f"Event {job['event']}: {status['state']}: "
                        f"{status.get('error_message')}. Run again to retry it."
                    )
            if list(WORK_DIR.glob("pending_*.json")):
                time.sleep(POLL_SECONDS)

    def finish(self, ee, job, data):
        event_id = job["event"]
        rows = sample.read_rows(ee, job["asset"])
        totals = {int(value): pixels for value, pixels in job["pixels_in_area"].items()}
        drawn = {value: sum(row["label"] == value for row in rows) for value in totals}
        weights = {value: sample.sampling_weight(totals[value], drawn[value]) for value in totals}
        result = {
            "event": event_id,
            "counties": job["counties"],
            "pixels_in_area": totals,
            "pixels_drawn": drawn,
            "rows": [
                {
                    "event": event_id,
                    "county": job["names"][str(row["county"])],
                    "flooded": row["label"],
                    "weight": round(weights[row["label"]], 6),
                    "lon": round(row["longitude"], 6),
                    "lat": round(row["latitude"], 6),
                    **{name: row[name] for name in data["features"]},
                }
                for row in rows
            ],
        }
        done_path(event_id).write_text(json.dumps(result))
        ee.data.deleteAsset(job["asset"])
        self.stdout.write(f"{event_id}: drew {drawn} of {totals}")

    def write(self, ee, events, config):
        columns = [
            "event",
            "county",
            "flooded",
            "weight",
            "lon",
            "lat",
            "near_lake",
            *config["data"]["features"],
        ]
        rows = [row for event in events for row in event["rows"]]
        lakes = config["sensitivity"]["lakes"]
        flags = sample.near_lake_flags(ee, rows, lakes)
        with open(OUT_PATH, "w", newline="") as handle:
            writer = csv.DictWriter(handle, columns)
            writer.writeheader()
            writer.writerows(
                {**row, "near_lake": flag} for row, flag in zip(rows, flags, strict=True)
            )
        digest = hashlib.sha256(OUT_PATH.read_bytes()).hexdigest()
        summary = [
            {key: value for key, value in event.items() if key != "rows"} for event in events
        ]
        MANIFEST_PATH.parent.mkdir(exist_ok=True)
        manifest = {
            "file": OUT_PATH.name,
            "sha256": digest,
            "protocol_version": config["protocol_version"],
            "rows": len(rows),
            "near_lake_rows": sum(flags),
            "events": summary,
        }
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
        self.stdout.write(f"{len(events)} events written to {OUT_PATH.name}, SHA-256 {digest}")
