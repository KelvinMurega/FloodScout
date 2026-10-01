"""Flood outlook for the months ahead, from a seasonal rainfall forecast.

`score_regions` scores a date from rainfall that has already fallen. This
asks the same model about months that have not happened yet, by feeding it
the ECMWF seasonal forecast instead: 51 equally likely versions of the
coming months' rainfall. Each one is scored separately, so the result is a
spread, not a single number.

The results go to RiskOutlook, not RiskScore: a forecast-driven score must
not sit next to observed ones and become the dashboard's "latest". Each run
replaces the outlook for the regions and months it covers. A copy is written
to data/processed/flood_outlook_<level>_<today>.csv.
"""

from __future__ import annotations

import csv
import datetime as dt
import statistics

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from geodata.management.commands.seed_terrain import representative_point
from geodata.models import Region
from geodata.services.climate_indices import ClimateIndexError, fetch_oni, oni_for
from geodata.services.open_meteo import (
    OpenMeteoError,
    fetch_daily_rainfall,
    fetch_seasonal_rainfall,
)
from riskengine.ml.features import FEATURE_NAMES, HISTORY_DAYS, feature_row
from riskengine.ml.infer import classify, flood_probabilities, load_model, model_thresholds
from riskengine.models import RiskOutlook, RiskScore

from .build_training_set import DATA_DIR, month_ends

DEFAULT_MONTHS = 3
RATIO_INDEX = FEATURE_NAMES.index("rain_30d_ratio")


class Command(BaseCommand):
    help = "Predict flood risk per region for the coming months from the seasonal forecast."

    def add_arguments(self, parser):
        parser.add_argument(
            "--months",
            type=int,
            default=DEFAULT_MONTHS,
            help=f"How many months ahead, counting this one (default {DEFAULT_MONTHS}).",
        )
        parser.add_argument(
            "--level",
            choices=[level.value for level in Region.Level],
            default=Region.Level.COUNTY.value,
            help="Counties (default) or sub-counties.",
        )

    def handle(self, *args, **options):
        model = load_model()
        if model is None:
            raise CommandError("No trained model — run `manage.py train_model` first.")
        if not 1 <= options["months"] <= 6:
            raise CommandError("--months must be between 1 and 6.")

        regions = list(
            Region.objects.filter(level=options["level"])
            .prefetch_related("geospatial_features")
            .order_by("county_name", "name")
        )
        if not regions:
            raise CommandError("No regions in the database — run `seed_regions` first.")

        today = timezone.localdate()
        last_month = dt.date(
            today.year + (today.month + options["months"] - 2) // 12,
            (today.month + options["months"] - 2) % 12 + 1,
            1,
        )
        ends = month_ends(today.replace(day=1), last_month)
        points = [representative_point(region) for region in regions]

        self.stdout.write(
            f"Outlook for {len(regions)} regions, {ends[0]:%B %Y} to {ends[-1]:%B %Y} "
            f"({model['version']})"
        )
        try:
            observed = fetch_daily_rainfall(points, past_days=HISTORY_DAYS + 1)
            forecast = fetch_seasonal_rainfall(points, (ends[-1] - today).days + 1)
            oni = fetch_oni()
        except (OpenMeteoError, ClimateIndexError) as exc:
            raise CommandError(str(exc)) from exc

        # One row per region, month and ensemble member, scored in one call.
        keys = []
        rows = []
        for region, past, members in zip(regions, observed, forecast):
            terrain = next(iter(region.geospatial_features.all()), None)
            for end in ends:
                for member in members:
                    keys.append((region, end))
                    rows.append(
                        feature_row(
                            # Forecast days win where the two overlap (today).
                            {**past, **member},
                            end,
                            normal_30d=model["normals"].get(region.county_name, {}).get(end.month),
                            oni=oni_for(end, oni),
                            terrain=terrain,
                        )
                    )

        by_key: dict = {}
        for key, row, probability in zip(keys, rows, flood_probabilities(model, rows)):
            by_key.setdefault(key, []).append((probability, row[RATIO_INDEX]))

        thresholds = model_thresholds(model)
        results = []
        for (region, end), scored in by_key.items():
            probabilities = sorted(probability for probability, _ratio in scored)
            deciles = statistics.quantiles(probabilities, n=10)
            mean = statistics.fmean(probabilities)
            results.append(
                {
                    "region": region.name,
                    "county": region.county_name,
                    "month": f"{end:%Y-%m}",
                    "flood_probability": round(mean, 4),
                    "probability_p10": round(deciles[0], 4),
                    "probability_p90": round(deciles[-1], 4),
                    "risk_category": classify(mean, thresholds),
                    "rain_vs_normal": round(statistics.median(ratio for _p, ratio in scored), 2),
                    "members": len(probabilities),
                }
            )

        with transaction.atomic():
            RiskOutlook.objects.filter(
                region__in=regions, month__in=[end.replace(day=1) for end in ends]
            ).delete()
            RiskOutlook.objects.bulk_create(
                RiskOutlook(
                    region=region,
                    month=end.replace(day=1),
                    flood_probability=result["flood_probability"],
                    probability_low=result["probability_p10"],
                    probability_high=result["probability_p90"],
                    # NaN (no normal on file for the county) is stored as null.
                    rain_vs_normal=(
                        result["rain_vs_normal"]
                        if result["rain_vs_normal"] == result["rain_vs_normal"]
                        else None
                    ),
                    risk_category=result["risk_category"],
                    model_version=model["version"],
                    issued_on=today,
                )
                for (region, end), result in zip(by_key, results)
            )

        path = DATA_DIR / "processed" / f"flood_outlook_{options['level']}_{today.isoformat()}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(results[0]))
            writer.writeheader()
            writer.writerows(results)

        self._report(results, ends, oni_for(ends[0], oni), path)

    def _report(self, results, ends, oni, path):
        labels = dict(RiskScore.RiskCategory.choices)
        self.stdout.write(f"El Niño index (ONI) carried into the outlook: {oni:+.2f}")
        for end in ends:
            month = [r for r in results if r["month"] == f"{end:%Y-%m}"]
            month.sort(key=lambda r: r["flood_probability"], reverse=True)
            self.stdout.write("")
            self.stdout.write(f"{end:%B %Y}")
            for category in reversed(RiskScore.RiskCategory.values):
                count = sum(r["risk_category"] == category for r in month)
                self.stdout.write(f"  {labels[category]:<10} {count}")
            self.stdout.write("  Highest:")
            for r in month[:10]:
                self.stdout.write(
                    f"    {r['region']:<22} {100 * r['flood_probability']:5.1f}%  "
                    f"(members {100 * r['probability_p10']:.0f}-{100 * r['probability_p90']:.0f}%)  "
                    f"rain {r['rain_vs_normal']:.1f}x normal  {labels[r['risk_category']]}"
                )
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(results)} rows to {path}"))
