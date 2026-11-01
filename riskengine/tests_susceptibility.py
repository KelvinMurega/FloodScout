"""The susceptibility rules, on made-up pixels. No Earth Engine and no network."""

import numpy as np
from django.test import SimpleTestCase

from riskengine.ml.susceptibility import evaluate, sample, train


def pixels(rows=1200, seed=0):
    """Two events in two counties each, where low HAND floods more often."""
    generator = np.random.default_rng(seed)
    hand = generator.uniform(0, 30, rows)
    flooded = (generator.uniform(0, 1, rows) < np.exp(-hand / 8)).astype(float)
    return {
        "event": np.tile([1.0, 2.0], rows // 2),
        "county": np.repeat(["Busia", "Kisumu"], rows // 2),
        "flooded": flooded,
        "weight": np.where(flooded == 1, 1.0, 5.0),
        "lon": generator.uniform(34, 36, rows),
        "lat": generator.uniform(-1, 1, rows),
        "hand": hand,
        "twi": 20 - hand / 3 + generator.normal(0, 1, rows),
        "log_upa": generator.uniform(0, 3, rows),
        "dist_river": hand * 100 + generator.normal(0, 300, rows),
        "elev": generator.uniform(1000, 1500, rows),
        "slope": generator.uniform(0, 5, rows),
        "flood_zone_100y": (hand < 3).astype(float),
        "near_lake": (generator.uniform(0, 1, rows) < 0.2).astype(float),
    }


class ConfigTests(SimpleTestCase):
    def test_config_matches_the_protocol(self):
        config = sample.load_config()
        self.assertEqual(config["protocol_version"], 1)
        self.assertEqual(config["data"]["sampling_seed"], 20261029)
        self.assertEqual(config["data"]["min_clear_perc"], 0.5)
        self.assertEqual(config["promotion"]["margin"], 0.03)
        self.assertEqual(len(config["data"]["features"]), 7)

    def test_eight_candidates_in_a_fixed_order(self):
        found = train.candidates(sample.load_config()["selection"]["grids"])
        self.assertEqual(len(found), 8)
        self.assertEqual(found, train.candidates(sample.load_config()["selection"]["grids"]))


class SamplingRuleTests(SimpleTestCase):
    def test_an_event_needs_enough_flooded_pixels(self):
        self.assertTrue(sample.is_usable({1: 150, 2: 50}, 200))
        self.assertFalse(sample.is_usable({1: 199}, 200))

    def test_only_counties_with_a_flooded_pixel_are_used(self):
        self.assertEqual(sample.touched_counties({3: 1, 1: 40, 2: 0}), [1, 3])

    def test_a_weight_says_how_many_pixels_a_drawn_one_stands_for(self):
        self.assertEqual(sample.sampling_weight(912000, 3000), 304)

    def test_a_class_with_nothing_drawn_is_an_error(self):
        with self.assertRaises(ValueError):
            sample.sampling_weight(10, 0)


class MetricTests(SimpleTestCase):
    def test_a_perfect_score_gets_one_and_its_reverse_gets_zero(self):
        data = pixels()
        self.assertEqual(evaluate.summary(data, data["flooded"])["within_county_roc_auc"], 1.0)
        self.assertEqual(evaluate.summary(data, -data["flooded"])["within_county_roc_auc"], 0.0)

    def test_a_county_event_with_too_few_pixels_is_left_out(self):
        data = pixels(rows=400)
        labels = data["flooded"].copy()
        labels[data["county"] == "Busia"] = 0
        table = evaluate.by_county_event({**data, "flooded": labels}, data["hand"])
        self.assertNotIn("Busia", {row["county"] for row in table})

    def test_class_weights_change_precision_but_not_the_area_under_the_curve(self):
        data = pixels()
        noisy = -data["hand"] + np.random.default_rng(1).normal(0, 20, len(data["hand"]))
        even = evaluate.summary({**data, "weight": np.ones(len(data["hand"]))}, noisy)
        weighted = evaluate.summary(data, noisy)
        self.assertEqual(even["within_county_roc_auc"], weighted["within_county_roc_auc"])
        self.assertNotEqual(
            even["within_county_average_precision"], weighted["within_county_average_precision"]
        )

    def test_hand_is_the_best_baseline_when_low_ground_floods(self):
        baselines = evaluate.score_baselines(pixels())
        self.assertEqual(evaluate.best_baseline(baselines), "hand")
        self.assertGreater(baselines["hand"]["within_county_roc_auc"], 0.7)

    def test_the_sensitivity_score_drops_the_rows_near_a_lake(self):
        data = pixels()
        away, score = evaluate.without_lakes(data, data["hand"])
        self.assertEqual(len(away["flooded"]), int((data["near_lake"] == 0).sum()))
        self.assertEqual(len(score), len(away["flooded"]))
        self.assertTrue((away["near_lake"] == 0).all())

    def test_calibration_bins_hold_every_row(self):
        data = pixels()
        result = evaluate.calibration(data, np.linspace(0, 1, len(data["hand"])))
        self.assertEqual(sum(row["rows"] for row in result["reliability"]), len(data["hand"]))


class DecisionTests(SimpleTestCase):
    def test_the_model_ships_when_it_clears_the_margin_and_the_blocks(self):
        self.assertEqual(evaluate.decide(0.80, 0.75, 0.77, 0.75, 0.03)["ship"], "model")

    def test_just_under_the_margin_ships_the_baseline(self):
        self.assertEqual(evaluate.decide(0.79, 0.76, 0.77, 0.75, 0.03)["ship"], "best-baseline")

    def test_losing_on_blocks_ships_the_baseline(self):
        self.assertEqual(evaluate.decide(0.90, 0.74, 0.77, 0.75, 0.03)["ship"], "best-baseline")


class TrainingTests(SimpleTestCase):
    def setUp(self):
        self.settings = sample.load_config()
        self.features = self.settings["data"]["features"]
        self.options = train.candidates(self.settings["selection"]["grids"])

    def test_blocks_are_half_degree_squares(self):
        blocks = train.block_ids(
            {"lon": np.array([34.1, 34.4, 34.6]), "lat": np.array([0.1, 0.2, 0.1])}, 0.5
        )
        self.assertEqual(blocks[0], blocks[1])
        self.assertNotEqual(blocks[0], blocks[2])

    def test_out_of_fold_never_leaves_a_pixel_unscored(self):
        model = train.build(self.options[0], 0)
        predicted = train.block_cv(pixels(), self.features, model, 0.5, 3)
        self.assertFalse(np.isnan(predicted).any())

    def test_low_ground_passes_the_sign_check(self):
        data = pixels(rows=2000)
        inputs = train.matrix(data, self.features)
        model = train._fit(train.build(self.options[2], 0), inputs, data["flooded"], data["weight"])
        self.assertTrue(train.sign_check(model, data, self.features)["hand"]["as_expected"])

    def test_a_baseline_layer_keeps_the_order_of_its_score(self):
        layer = train.fit_baseline_layer(pixels(), "hand")
        low_ground, high_ground = layer.predict_proba([[-1.0], [-20.0]])[:, 1]
        self.assertGreater(low_ground, high_ground)

    def test_the_whole_protocol_runs_and_reaches_a_decision(self):
        settings = sample.load_config()
        settings["selection"]["seeds"] = [0]
        settings["selection"]["grids"]["hist-gradient-boosting"]["max_iter"] = [20]
        settings["selection"]["grids"]["hist-gradient-boosting"]["min_samples_leaf"] = [20]
        layer, metrics = train.run(pixels(), settings, log=lambda message: None)
        self.assertIn(metrics["decision"]["ship"], ("model", "best-baseline"))
        self.assertEqual(metrics["best_baseline"], "hand")
        self.assertEqual(len(metrics["candidates"]), 8)
        self.assertEqual(len(metrics["event_cv"]["per_event"]), 2)
        self.assertIn(
            "model, block CV", metrics["sensitivity_without_lakes"]["within_county_roc_auc"]
        )
        self.assertIsNotNone(layer)
