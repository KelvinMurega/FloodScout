"""Check the trained model against satellite-mapped floods it never saw.

The model is trained and cross-validated on Dartmouth Flood Observatory
labels, which place each flood as a circle and end in 2021. UNOSAT maps
the water actually seen from satellite, for recent events (the 2023 short
rains and the 2024 long rains). Scoring those months and comparing with
where UNOSAT saw water is a test on later years and on independent,
better-located labels.

Input is a JSON manifest, one entry per UNOSAT product:

    [
      {
        "name": "Long rains 2024, national",
        "date": "2024-05-05",
        "extent": "data/external/unosat/kenya_2024_05.shp",
        "covers": "all"
      },
      {
        "name": "Garissa, April 2024",
        "date": "2024-04-29",
        "extent": "data/external/unosat/garissa_2024_04/water.shp",
        "layer": 0,
        "covers": ["Garissa"]
      }
    ]

`date` is the end of the 30-day window scored; use the image date of the
product. `covers` lists the counties the product analysed, or "all". A
county outside it is left out rather than counted as dry, because UNOSAT
not mapping a county does not mean it did not flood. `extent` is anything
GDAL reads (shapefile, GeoPackage, GeoJSON, KML, a .gdb folder); `layer`
picks one if the file has several.

UNOSAT's "satellite detected water" usually includes permanent water
(lakes, reservoirs) unless the product separates it. Use the flood-only
layer when there is one; otherwise --min-km2 keeps a lake shore from
counting as a flood.

Writes data/processed/unosat_validation.csv (one row per county and event)
and prints the scores.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

import numpy as np
from django.contrib.gis.gdal import DataSource
from django.contrib.gis.geos import GEOSGeometry
from django.core.management.base import BaseCommand, CommandError
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from geodata.models import Region
from geodata.services import earth_engine_rain
from geodata.services.climate_indices import ClimateIndexError, fetch_dmi, fetch_oni
from geodata.services.open_meteo import OpenMeteoError
from riskengine.ml.features import HISTORY_DAYS
from riskengine.ml.infer import classify, flood_probabilities, load_model, model_thresholds, region_row

from .build_training_set import DATA_DIR, county_rainfall

# Area-preserving CRS for measuring flooded km² (EPSG:6933, global equal area).
EQUAL_AREA_SRID = 6933
DEFAULT_MIN_KM2 = 5.0
OUTPUT_PATH = DATA_DIR / "processed" / "unosat_validation.csv"


def load_extent(path: Path, layer=0) -> GEOSGeometry:
    """Every polygon in the layer, merged, in lon/lat."""
    source = DataSource(str(path))
    shapes = [feature.geom.geos for feature in source[layer]]
    if not shapes:
        raise CommandError(f"No features in {path} (layer {layer}).")
    for shape in shapes:
        if not shape.srid:
            shape.srid = 4326
        if shape.srid != 4326:
            shape.transform(4326)
    merged = shapes[0]
    for shape in shapes[1:]:
        merged = merged.union(shape)
    return merged.buffer(0)  # repairs self-intersections common in vectorised rasters


def flooded_km2(county_geom, extent) -> float:
    """km² of `extent` inside the county."""
    if not county_geom.intersects(extent):
        return 0.0
    overlap = county_geom.intersection(extent)
    overlap.transform(EQUAL_AREA_SRID)
    return overlap.area / 1_000_000


def scores(y, p) -> dict:
    y, p = np.asarray(y), np.asarray(p)
    result = {
        "rows": int(len(y)),
        "flooded": int(y.sum()),
        "base_rate": round(float(y.mean()), 4) if len(y) else None,
        "brier": round(float(brier_score_loss(y, p)), 4) if len(y) else None,
    }
    if 0 < y.sum() < len(y):
        result["roc_auc"] = round(float(roc_auc_score(y, p)), 4)
        result["average_precision"] = round(float(average_precision_score(y, p)), 4)
    return result


class Command(BaseCommand):
    help = "Score the trained model against UNOSAT satellite flood extents."

    def add_arguments(self, parser):
        parser.add_argument("manifest", type=Path, help="JSON list of UNOSAT products (see module docstring).")
        parser.add_argument(
            "--min-km2",
            type=float,
            default=DEFAULT_MIN_KM2,
            help=f"Flooded area a county needs to count as flooded (default {DEFAULT_MIN_KM2}).",
        )

    def handle(self, *args, **options):
        model = load_model()
        if model is None:
            raise CommandError("No trained model — run `manage.py train_model` first.")
        try:
            events = json.loads(options["manifest"].read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Could not read the manifest: {exc}") from exc
        if not isinstance(events, list) or not events:
            raise CommandError("The manifest must be a non-empty JSON list.")

        counties = list(
            Region.objects.filter(level=Region.Level.COUNTY)
            .prefetch_related("geospatial_features")
            .order_by("name")
        )
        if not counties:
            raise CommandError("No counties in the database — run `seed_regions` first.")
        by_name = {county.name.casefold(): county for county in counties}

        try:
            model = {**model, "oni": fetch_oni(), "dmi": fetch_dmi()}
        except ClimateIndexError as exc:
            raise CommandError(str(exc)) from exc
        source = "earth-engine" if model.get("rain_source", "").startswith("earth-engine") else "open-meteo"

        results = []
        for event in events:
            date = dt.date.fromisoformat(event["date"])
            if event.get("covers", "all") == "all":
                covered = counties
            else:
                missing = [n for n in event["covers"] if n.casefold() not in by_name]
                if missing:
                    raise CommandError(f"{event['name']}: unknown counties {missing}")
                covered = [by_name[n.casefold()] for n in event["covers"]]

            extent = load_extent(Path(event["extent"]), event.get("layer", 0))
            try:
                rainfall, _points = county_rainfall(
                    covered, date - dt.timedelta(days=HISTORY_DAYS), date, source, log=self.stdout.write
                )
            except (OpenMeteoError, earth_engine_rain.EarthEngineError) as exc:
                raise CommandError(str(exc)) from exc

            rows = [
                region_row(model, county, rainfall.get(county.name, {}), date,
                           next(iter(county.geospatial_features.all()), None))
                for county in covered
            ]
            for county, probability in zip(covered, flood_probabilities(model, rows)):
                km2 = flooded_km2(county.geom, extent)
                results.append(
                    {
                        "event": event["name"],
                        "date": date.isoformat(),
                        "county": county.name,
                        "flooded_km2": round(km2, 2),
                        "flooded": int(km2 >= options["min_km2"]),
                        "probability": round(probability, 4),
                        "risk_category": classify(probability, model_thresholds(model)),
                    }
                )

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with OUTPUT_PATH.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(results[0]))
            writer.writeheader()
            writer.writerows(results)

        self._report(model, results)

    def _report(self, model, results):
        self.stdout.write("")
        self.stdout.write(f"Model {model['version']} against UNOSAT extents")
        for name in dict.fromkeys(r["event"] for r in results):
            subset = [r for r in results if r["event"] == name]
            self.stdout.write(f"  {name}: {self._line(subset)}")
        self.stdout.write(f"  All events: {self._line(results)}")
        self.stdout.write("")
        self.stdout.write("Flooded share by risk band:")
        for band in ("low", "moderate", "high", "very_high"):
            inside = [r for r in results if r["risk_category"] == band]
            share = f"{100 * sum(r['flooded'] for r in inside) / len(inside):.0f}%" if inside else "n/a"
            self.stdout.write(f"  {band:<10} {len(inside):>4} counties, flooded {share}")
        self.stdout.write("")
        if len(results) < 100:
            self.stdout.write(self.style.WARNING(
                f"Only {len(results)} county-events: treat these scores as indicative, not conclusive."
            ))
        self.stdout.write(self.style.SUCCESS(f"Wrote {OUTPUT_PATH}"))

    @staticmethod
    def _line(rows) -> str:
        s = scores([r["flooded"] for r in rows], [r["probability"] for r in rows])
        parts = [f"{s['flooded']}/{s['rows']} flooded", f"Brier {s['brier']}"]
        if "roc_auc" in s:
            parts += [f"ROC AUC {s['roc_auc']}", f"avg precision {s['average_precision']}"]
        return ", ".join(parts)
