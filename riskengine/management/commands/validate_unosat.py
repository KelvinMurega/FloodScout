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
picks one if the file has several, by position or by name. A list of layers
is merged, for a product that maps one flood over several image dates.

UNOSAT's "satellite detected water" usually includes permanent water
(lakes, reservoirs) unless the product separates it. Use the flood-only
layer when there is one; otherwise --min-km2 keeps a lake shore from
counting as a flood.

Writes data/processed/unosat_validation.csv (one row per county and event)
and prints the scores.

With --level sub_county the same events are scored for sub-counties, into
unosat_validation_sub_county.csv. The model was trained on counties, so this
is the only check of its sub-county scores. Two questions are answered
separately: whether sub-counties are ranked well across the country, which
a model that only knew the county could also do, and whether they are ranked
well *within* their own county, which needs the sub-county's own rainfall
and terrain to carry information.
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
from riskengine.ml.infer import (
    classify,
    flood_probabilities,
    load_model,
    model_thresholds,
    region_row,
)

from .build_training_set import DATA_DIR, RAINFALL_DIR, county_rainfall

# Area-preserving CRS for measuring flooded km² (EPSG:6933, global equal area).
EQUAL_AREA_SRID = 6933
DEFAULT_MIN_KM2 = 5.0
OUTPUT_PATH = DATA_DIR / "processed" / "unosat_validation.csv"
SUB_COUNTY_OUTPUT_PATH = DATA_DIR / "processed" / "unosat_validation_sub_county.csv"
SUB_COUNTY_RAINFALL_DIR = RAINFALL_DIR / "sub_county"


def load_extent(path: Path, layer=0) -> GEOSGeometry:
    """Every polygon in the layer (or layers), merged, in lon/lat."""
    source = DataSource(str(path))
    layers = layer if isinstance(layer, list) else [layer]
    shapes = [feature.geom.geos for name in layers for feature in source[name]]
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


def within_county_auc(results) -> tuple[float, int] | None:
    """Mean ROC AUC of sub-counties ranked inside their own county and event.

    Only counties where some sub-counties flooded and some did not can be
    ranked. Returns (mean, how many county-events it is over), or None.
    """
    groups = {}
    for row in results:
        groups.setdefault((row["event"], row["county"]), []).append(row)
    values = []
    for rows in groups.values():
        flooded = [row["flooded"] for row in rows]
        if 0 < sum(flooded) < len(flooded):
            values.append(roc_auc_score(flooded, [row["probability"] for row in rows]))
    if not values:
        return None
    return round(float(np.mean(values)), 4), len(values)


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
            help=f"Flooded area a region needs to count as flooded (default {DEFAULT_MIN_KM2}).",
        )
        parser.add_argument(
            "--level",
            choices=[Region.Level.COUNTY, Region.Level.SUB_COUNTY],
            default=Region.Level.COUNTY,
            help="Score counties (default) or sub-counties.",
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

        sub_county = options["level"] == Region.Level.SUB_COUNTY
        counties = list(
            Region.objects.filter(level=options["level"])
            .select_related("parent")
            .prefetch_related("geospatial_features")
            .order_by("name")
        )
        if not counties:
            raise CommandError(f"No {options['level']} regions in the database — run `seed_regions` first.")
        # `covers` names counties at either level.
        by_name = {}
        for region in counties:
            by_name.setdefault(region.county_name.casefold(), []).append(region)

        try:
            model = {**model, "oni": fetch_oni(), "dmi": fetch_dmi()}
        except ClimateIndexError as exc:
            raise CommandError(str(exc)) from exc
        if model.get("rain_source", "").startswith("earth-engine") and sub_county:
            raise CommandError("Sub-county validation reads point rainfall; this model was trained on county means.")
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
                covered = [region for n in event["covers"] for region in by_name[n.casefold()]]

            extent = load_extent(Path(event["extent"]), event.get("layer", 0))
            start = date - dt.timedelta(days=HISTORY_DAYS)
            try:
                rainfall, _points = county_rainfall(
                    covered,
                    start,
                    date,
                    source,
                    log=self.stdout.write,
                    cache_dir=SUB_COUNTY_RAINFALL_DIR if sub_county else RAINFALL_DIR,
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
                        **({"sub_county": county.name} if sub_county else {}),
                        "county": county.county_name,
                        "flooded_km2": round(km2, 2),
                        "flooded": int(km2 >= options["min_km2"]),
                        "probability": round(probability, 4),
                        "risk_category": classify(probability, model_thresholds(model)),
                    }
                )

        output = SUB_COUNTY_OUTPUT_PATH if sub_county else OUTPUT_PATH
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(results[0]))
            writer.writeheader()
            writer.writerows(results)

        self._report(model, results, output, "sub-county" if sub_county else "county")

    def _report(self, model, results, output, unit):
        self.stdout.write("")
        self.stdout.write(f"Model {model['version']} against UNOSAT extents, by {unit}")
        for name in dict.fromkeys(r["event"] for r in results):
            subset = [r for r in results if r["event"] == name]
            self.stdout.write(f"  {name}: {self._line(subset)}")
        self.stdout.write(f"  All events: {self._line(results)}")
        if unit == "sub-county":
            within = within_county_auc(results)
            if within:
                self.stdout.write(
                    f"  Ranked within their own county: mean ROC AUC {within[0]} "
                    f"over {within[1]} county-events with both flooded and dry sub-counties"
                )
        self.stdout.write("")
        self.stdout.write("Flooded share by risk band:")
        for band in ("low", "moderate", "high", "very_high"):
            inside = [r for r in results if r["risk_category"] == band]
            share = f"{100 * sum(r['flooded'] for r in inside) / len(inside):.0f}%" if inside else "n/a"
            self.stdout.write(f"  {band:<10} {len(inside):>4} regions, flooded {share}")
        self.stdout.write("")
        if len(results) < 100:
            self.stdout.write(self.style.WARNING(
                f"Only {len(results)} {unit}-events: treat these scores as indicative, not conclusive."
            ))
        self.stdout.write(self.style.SUCCESS(f"Wrote {output}"))

    @staticmethod
    def _line(rows) -> str:
        s = scores([r["flooded"] for r in rows], [r["probability"] for r in rows])
        parts = [f"{s['flooded']}/{s['rows']} flooded", f"Brier {s['brier']}"]
        if "roc_auc" in s:
            parts += [f"ROC AUC {s['roc_auc']}", f"avg precision {s['average_precision']}"]
        return ", ".join(parts)
