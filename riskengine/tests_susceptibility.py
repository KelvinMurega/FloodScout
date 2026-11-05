"""The susceptibility layer per region: the sums, the build command and the endpoint."""

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

import joblib
import numpy as np
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from geodata.models import Region
from riskengine import susceptibility
from riskengine.ml.susceptibility import layer
from riskengine.ml.susceptibility.unosat_test import FEATURE_BANDS
from riskengine.tests_analytics import make_region


class HandModel:
    """Susceptibility = the pixel's `hand` band read as a probability."""

    def predict_proba(self, inputs):
        p = np.clip(inputs[:, FEATURE_BANDS.index("hand")], 0, 1)
        return np.column_stack([1 - p, p])


def pixels(sub_counties, hands, *, permanent=None, area=62_500.0):
    n = len(sub_counties)
    data = {name: np.ones(n, dtype=np.float32) for name in FEATURE_BANDS}
    data["hand"] = np.asarray(hands, dtype=np.float32)
    data["sub_county"] = np.asarray(sub_counties, dtype=np.float32)
    data["permanent"] = np.asarray(permanent or [0] * n, dtype=np.float32)
    data["near_lake"] = np.zeros(n, dtype=np.float32)
    data["area_m2"] = np.full(n, area, dtype=np.float32)
    data["col"] = np.arange(n, dtype=np.int32)
    data["row"] = np.zeros(n, dtype=np.int32)
    return data


class LayerTests(TestCase):
    def test_sums_per_sub_county_and_county_leave_out_unusable_pixels(self):
        raw = pixels([1, 1, 2, 2, 0], [0.2, 0.6, 0.9, 0.5, 1.0], permanent=[0, 0, 0, 1, 0])
        scored = layer.usable_scores(raw, HandModel())
        # Sub-county 0 is outside any sub-county; the permanent-water pixel goes too.
        self.assertEqual(scored["sub_county"].tolist(), [1, 1, 2])
        summary = layer.summarise(scored, line=0.6)
        self.assertEqual(summary[1]["mean"], 0.4)
        self.assertEqual(summary[1]["high_share"], 0.5)
        self.assertEqual(summary[2]["high_share"], 1.0)
        self.assertEqual(summary[0]["land_km2"], round(3 * 62_500 / 1e6, 2))
        self.assertAlmostEqual(summary[0]["expected_km2"], (0.2 + 0.6 + 0.9) * 0.0625, places=2)

    def test_the_high_line_is_kenyas_80th_percentile(self):
        line = layer.high_line([np.arange(0, 50) / 100, np.arange(50, 100) / 100])
        self.assertAlmostEqual(line, 0.792, places=3)

    def test_ranks_run_within_a_level_most_flood_prone_first(self):
        rows = [
            {"level": "county", "high_share": 0.1},
            {"level": "county", "high_share": 0.3},
            {"level": "sub_county", "high_share": 0.2},
            {"level": "sub_county", "high_share": None},
        ]
        layer.ranks(rows)
        self.assertEqual([r.get("rank_in_kenya") for r in rows], [2, 1, 1, None])
        self.assertEqual(rows[0]["of"], 2)


class BuildCommandTests(TestCase):
    def setUp(self):
        self.alpha = make_region("Alpha")
        self.beta = make_region("Beta", offset=2.0)
        for name, county, offset in (("Alpha North", "Alpha", 4.0), ("Beta West", "Beta", 6.0)):
            sub = make_region(name, level=Region.Level.SUB_COUNTY, offset=offset)
            sub.county_name = county
            sub.save()
        self.dir = Path(tempfile.mkdtemp())
        self.run = self.dir / "run"
        self.run.mkdir()
        self.px = self.dir / "pixels"
        self.px.mkdir()
        joblib.dump({"model": HandModel(), "ship": "model"}, self.run / "susceptibility_model.joblib")
        (self.px / "sub_counties.json").write_text(
            json.dumps({"1": {"name": "Alpha North", "county": "Alpha"}, "2": {"name": "Beta West", "county": "Beta"}})
        )
        np.savez(self.px / "Alpha.npz", **pixels([1, 1, 1, 1], [0.1, 0.2, 0.3, 0.9]))

    def build(self):
        call_command("build_susceptibility", "--run", str(self.run), "--pixels", str(self.px), stdout=StringIO())

    def test_every_county_must_have_its_pixels(self):
        with self.assertRaisesRegex(CommandError, "Beta"):
            self.build()

    def test_it_writes_one_row_per_region(self):
        np.savez(self.px / "Beta.npz", **pixels([2, 2, 2, 2], [0.8, 0.85, 0.9, 0.95]))
        self.build()
        data = json.loads((self.run / "regions.json").read_text())
        names = {(r["level"], r["name"]) for r in data["rows"]}
        self.assertEqual(
            names,
            {("county", "Alpha"), ("county", "Beta"), ("sub_county", "Alpha North"), ("sub_county", "Beta West")},
        )
        beta = next(r for r in data["rows"] if r["name"] == "Beta West")
        self.assertEqual(beta["rank_in_kenya"], 1)
        self.assertEqual(data["high_percentile"], 80)

    def test_a_run_that_shipped_a_baseline_is_refused(self):
        joblib.dump({"model": HandModel(), "ship": "hand"}, self.run / "susceptibility_model.joblib")
        np.savez(self.px / "Beta.npz", **pixels([2], [0.5]))
        with self.assertRaisesRegex(CommandError, "did not ship"):
            self.build()


class EndpointTests(TestCase):
    def setUp(self):
        susceptibility._load.cache_clear()
        self.addCleanup(susceptibility._load.cache_clear)
        self.county = make_region("Kisumu")
        self.sub = make_region("Nyando", level=Region.Level.SUB_COUNTY, offset=2.0)
        self.sub.county_name = "Kisumu"
        self.sub.save()
        self.run = Path(tempfile.mkdtemp())

    def get(self, region):
        with mock.patch("riskengine.susceptibility.SHIPPED_RUN", self.run):
            return self.client.get(reverse("region-susceptibility", args=[region.pk])).json()

    def test_nothing_is_said_before_the_layer_is_built(self):
        self.assertEqual(self.get(self.sub), {"available": False})

    def test_a_sub_county_gets_its_numbers_its_countys_and_the_test_result(self):
        row = {"mean": 0.2, "expected_km2": 41.0, "land_km2": 500.0, "rank_in_kenya": 12, "of": 290}
        (self.run / "regions.json").write_text(
            json.dumps(
                {
                    "run": "r1",
                    "high_percentile": 80,
                    "rows": [
                        {**row, "level": "sub_county", "county": "Kisumu", "name": "Nyando", "high_share": 0.34},
                        {**row, "level": "county", "county": "Kisumu", "name": "Kisumu", "high_share": 0.22},
                    ],
                }
            )
        )
        (self.run / "unosat_test.json").write_text(
            json.dumps(
                {
                    "date": "2026-11-05",
                    "engine_sub_county_roc_auc": 0.461,
                    "sub_county": {"model": {"within_county_roc_auc": 0.7}},
                }
            )
        )
        data = self.get(self.sub)
        self.assertTrue(data["available"])
        self.assertEqual((data["high_share"], data["county_high_share"]), (0.34, 0.22))
        self.assertEqual(data["checked"]["sub_county_roc_auc"], 0.7)
        self.assertIsNone(self.get(self.county)["county_high_share"])
