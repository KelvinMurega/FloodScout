"""The UNOSAT look, on made-up pixels and a made-up extent. No Earth Engine, no real labels."""

import io
import json
import tempfile
from pathlib import Path
from unittest import mock

import joblib
import numpy as np
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from sklearn.ensemble import HistGradientBoostingClassifier

from riskengine.management.commands import susceptibility_unosat_test as command
from riskengine.ml.susceptibility import unosat_test as test
from riskengine.tests_susceptibility import pixels as development_pixels

LEDGER = """## Looks at held-out test sets

| Date | Test set | Run or model | Commit | Result |
|---|---|---|---|---|

## Runs
"""


def pixels(sub_counties, flooded_share=0.0, permanent=None, seed=0):
    """One pixel per entry of `sub_counties`, with low HAND where it is wet."""
    generator = np.random.default_rng(seed)
    size = len(sub_counties)
    result = {name: generator.uniform(0, 10, size) for name in test.FEATURE_BANDS}
    result["sub_county"] = np.asarray(sub_counties, dtype=float)
    result["permanent"] = (
        np.zeros(size) if permanent is None else np.asarray(permanent, dtype=float)
    )
    result["near_lake"] = np.zeros(size)
    result["area_m2"] = np.full(size, 62_500.0)
    return result


class CoverageTests(SimpleTestCase):
    def test_half_a_pixel_is_flooded_and_just_under_half_is_dry(self):
        left, right = np.zeros((5, 5), dtype=np.uint8), np.zeros((5, 5), dtype=np.uint8)
        left.flat[:13] = 1  # 13 of 25 cells: 52%
        right.flat[:12] = 1  # 12 of 25: 48%
        self.assertEqual(test.flooded(np.hstack([left, right])).tolist(), [[1, 0]])

    def test_a_raster_that_does_not_divide_is_an_error(self):
        with self.assertRaises(ValueError):
            test.coverage(np.zeros((6, 10)))


class PixelTableTests(SimpleTestCase):
    county_of = {1: "Busia", 2: "Busia", 3: "Kisumu"}

    def test_permanent_water_missing_features_and_outside_kenya_are_dropped(self):
        data = pixels([1, 1, 2, 0], permanent=[0, 1, 0, 0])
        data["hand"][2] = np.nan
        self.assertEqual(test.usable(data).tolist(), [True, False, False, False])

    def test_a_county_with_no_mapped_water_is_left_out(self):
        data = pixels([1, 2, 3, 3])
        table = test.pixel_table([(20231124, data, np.array([1, 0, 0, 0]))], self.county_of)
        self.assertEqual(table["county"].tolist(), ["Busia", "Busia"])
        self.assertEqual(table["flooded"].tolist(), [1.0, 0.0])
        self.assertEqual(table["weight"].tolist(), [1.0, 1.0])

    def test_each_event_keeps_its_own_counties(self):
        data = pixels([1, 3])
        table = test.pixel_table(
            [(1, data, np.array([1, 0])), (2, data, np.array([0, 1]))], self.county_of
        )
        self.assertEqual(table["event"].tolist(), [1.0, 2.0])
        self.assertEqual(table["county"].tolist(), ["Busia", "Kisumu"])


class ExpectedAreaTests(SimpleTestCase):
    def test_expected_area_sums_probability_times_area_without_permanent_water(self):
        data = pixels([1, 1, 2], permanent=[0, 1, 0])
        area = test.expected_area(data, np.array([0.5, 1.0, 0.2]))
        self.assertAlmostEqual(area[1], 0.5 * 0.0625)
        self.assertAlmostEqual(area[2], 0.2 * 0.0625)

    def test_the_area_baseline_is_the_usable_land(self):
        self.assertAlmostEqual(test.land_area(pixels([4, 4, 4]))[4], 3 * 0.0625)

    def test_sub_counties_are_ranked_only_inside_a_county_with_both_classes(self):
        rows = [
            {"event": 1, "county": "Busia", "flooded": 1, "score": 3.0},
            {"event": 1, "county": "Busia", "flooded": 0, "score": 1.0},
            {"event": 1, "county": "Kisumu", "flooded": 1, "score": 0.0},
            {"event": 1, "county": "Kisumu", "flooded": 1, "score": 9.0},
        ]
        self.assertEqual(
            test.within_county_auc(rows, "score"),
            {"within_county_roc_auc": 1.0, "county_events": 1},
        )


class LookCounterTests(SimpleTestCase):
    def test_a_look_is_recorded_once_and_then_refused(self):
        self.assertFalse(test.already_looked(LEDGER, "20261102T1246Z_77a8329"))
        after = test.record_look(
            LEDGER, "2026-11-03", "20261102T1246Z_77a8329", "abc1234", "model 0.7"
        )
        self.assertTrue(test.already_looked(after, "20261102T1246Z_77a8329"))
        self.assertFalse(test.already_looked(after, "20261201T0000Z_1234567"))
        self.assertLess(after.index("abc1234"), after.index("## Runs"))

    def test_a_ledger_without_the_looks_table_is_an_error(self):
        with self.assertRaises(ValueError):
            test.record_look("# nothing\n", "2026-11-03", "run", "abc1234", "x")


class BurnTests(SimpleTestCase):
    """The extent lands on the right pixels, north up, through gdal_rasterize."""

    # Quarter-degree pixels, so every edge is exact in floating point.
    transform = [0.25, 0, 34.0, 0, -0.25, 1.0]

    def test_a_square_in_the_north_west_floods_only_that_corner(self):
        # A 2 x 2 pixel grid from (34.0, 1.0) to (34.5, 0.5); the square is its north-west pixel.
        square = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[34.0, 1.0], [34.25, 1.0], [34.25, 0.75], [34.0, 0.75], [34.0, 1.0]]
                        ],
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "extent.geojson"
            path.write_text(json.dumps(square))
            # An absolute extent path is used as it is.
            event = {"extent": str(path), "layer": "extent"}
            county = {"col": np.array([0, 1, 0, 1]), "row": np.array([0, 0, 1, 1])}
            labels = command.Command().burn(event, county, self.transform)
        self.assertEqual(labels.tolist(), [1, 0, 0, 0])

    def test_tiles_cover_a_box_on_the_grid(self):
        found = list(command.tiles((34.0, 0.5, 34.5, 1.0), self.transform))
        self.assertEqual(found, [(0, 0, 2, 2)])
        self.assertEqual(command.tile_box(0, 0, 2, 2, self.transform), (34.0, 0.5, 34.5, 1.0))

    def test_the_event_number_comes_from_the_activation(self):
        self.assertEqual(
            command.event_number("Short rains (UNOSAT FL20231124KEN, VIIRS)"), 20231124
        )


def polygon(west, north, east, south):
    ring = [[west, north], [east, north], [east, south], [west, south], [west, north]]
    return {
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


class LookTests(SimpleTestCase):
    """The whole look on a made-up Kenya: two counties of 10 x 10 quarter-degree pixels.

    Busia is columns 0-9 with sub-counties 1 (west half) and 2; Kisumu is columns
    10-19 with 3 (west half) and 4. Both floods cover the west halves, where HAND is low.
    """

    transform = [0.25, 0, 34.0, 0, -0.25, 1.0]
    run_id = "20261102T1246Z_77a8329"

    def county(self, first_col, west_sub, east_sub, generator):
        rows, cols = np.mgrid[0:10, first_col : first_col + 10]
        west = (cols - first_col < 5).ravel()
        size = west.size
        result = {
            "hand": np.where(west, generator.uniform(0, 3, size), generator.uniform(10, 40, size)),
            "twi": generator.uniform(12, 20, size),
            "log_upa": generator.uniform(0, 3, size),
            "dist_river": generator.uniform(0, 5000, size),
            "elev": generator.uniform(1000, 1500, size),
            "slope": generator.uniform(0, 5, size),
            "flood_zone_100y": west.astype(float),
            "sub_county": np.where(west, west_sub, east_sub).astype(float),
            "permanent": np.zeros(size),
            "near_lake": np.zeros(size),
            "area_m2": np.full(size, 62_500.0),
            "col": cols.ravel(),
            "row": rows.ravel(),
        }
        return {
            name: np.asarray(values, dtype=np.float32 if name not in ("col", "row") else np.int32)
            for name, values in result.items()
        }

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        work = root / "work"
        work.mkdir()
        generator = np.random.default_rng(0)
        np.savez(work / "Busia.npz", **self.county(0, 1, 2, generator))
        np.savez(work / "Kisumu.npz", **self.county(10, 3, 4, generator))
        (work / "grid.json").write_text(
            json.dumps({"crs": "EPSG:4326", "transform": self.transform})
        )
        numbers = {
            1: ("Busia West", "Busia"),
            2: ("Busia East", "Busia"),
            3: ("Kisumu West", "Kisumu"),
            4: ("Kisumu East", "Kisumu"),
        }
        (work / "sub_counties.json").write_text(
            json.dumps(
                {
                    number: {"name": name, "county": county}
                    for number, (name, county) in numbers.items()
                }
            )
        )

        extent = root / "extent.geojson"
        west_halves = [polygon(34.0, 1.0, 35.25, -1.5), polygon(36.5, 1.0, 37.75, -1.5)]
        extent.write_text(json.dumps({"type": "FeatureCollection", "features": west_halves}))
        names = [
            "Short rains (UNOSAT FL20231124KEN, VIIRS)",
            "Long rains (UNOSAT FL20240426KEN, VIIRS)",
        ]
        manifest = root / "events.json"
        manifest.write_text(
            json.dumps([{"name": name, "extent": str(extent), "layer": "extent"} for name in names])
        )

        county_labels = root / "county.csv"
        county_labels.write_text(
            "event,county,flooded_km2\nx,Busia,90.0\nx,Kisumu,90.0\nx,Siaya,0.0\n"
        )
        sub_labels = root / "sub_county.csv"
        lines = ["event,sub_county,county,flooded"]
        for name in names:
            for number, (sub, county) in numbers.items():
                lines.append(f'"{name}",{sub},{county},{int(number in (1, 3))}')
        sub_labels.write_text("\n".join(lines) + "\n")

        ledger = root / "experiments.md"
        ledger.write_text(LEDGER)

        development = development_pixels()
        sample_path = root / "sample.csv"
        columns = list(development)
        with open(sample_path, "w") as handle:
            handle.write(",".join(columns) + "\n")
            for row in zip(*(development[name] for name in columns), strict=True):
                handle.write(",".join(str(value) for value in row) + "\n")
        model = HistGradientBoostingClassifier(max_iter=20).fit(
            np.column_stack([development[name] for name in test.FEATURE_BANDS]),
            development["flooded"],
        )
        self.model_path = root / self.run_id / "susceptibility_model.joblib"
        self.model_path.parent.mkdir()
        joblib.dump({"model": model}, self.model_path)

        patches = {
            "WORK_DIR": work,
            "NUMBERS_PATH": work / "sub_counties.json",
            "GRID_PATH": work / "grid.json",
            "MANIFEST": manifest,
            "COUNTY_LABELS": county_labels,
            "SUB_COUNTY_LABELS": sub_labels,
            "LEDGER": ledger,
        }
        self.patchers = [mock.patch.object(command, name, value) for name, value in patches.items()]
        self.patchers.append(
            mock.patch.object(
                command.sample,
                "load_config",
                return_value={"data": {"training_set": str(sample_path)}},
            )
        )
        for patcher in self.patchers:
            patcher.start()
        self.ledger = ledger

    def tearDown(self):
        for patcher in self.patchers:
            patcher.stop()
        self.folder.cleanup()

    def run_look(self):
        out = io.StringIO()
        runner = command.Command(stdout=out)
        runner.look(self.model_path, True)
        return out.getvalue()

    def test_the_look_scores_both_levels_and_is_then_refused(self):
        printed = self.run_look()
        result = json.loads((self.model_path.parent / "unosat_test.json").read_text())

        self.assertEqual(result["pixel"]["model"]["county_events"], 4)
        self.assertEqual(result["pixel"]["hand"]["within_county_roc_auc"], 1.0)
        self.assertGreater(result["pixel"]["model"]["within_county_roc_auc"], 0.8)
        self.assertEqual(
            result["sub_county"]["hand"], {"within_county_roc_auc": 1.0, "county_events": 4}
        )
        # Every sub-county has the same land, so area alone cannot rank them.
        self.assertEqual(result["sub_county"]["sub-county area"]["within_county_roc_auc"], 0.5)
        self.assertIn("current engine", printed)
        self.assertTrue(test.already_looked(self.ledger.read_text(), self.run_id))

        with self.assertRaises(CommandError):
            self.run_look()

    def test_the_look_needs_the_flag(self):
        with self.assertRaises(CommandError):
            command.Command().look(self.model_path, False)
        self.assertFalse(test.already_looked(self.ledger.read_text(), self.run_id))
