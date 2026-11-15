import unittest

import numpy as np
from sklearn.model_selection import KFold

from controllers import FEATURES
from research_map_methods import (
    CANDIDATES, FAMILIES, fit_model, loss, policy_spec, predict, select_config,
)
from revalidate_switching import FrozenPolicy


class MapMethodTests(unittest.TestCase):
    def data(self):
        x = np.zeros((64, len(FEATURES)))
        x[:, 0] = np.arange(64)
        costs = np.full((64, len(CANDIDATES)), 30.)
        costs[:32, 0] = 5.
        costs[32:, 3] = 4.
        labels = np.full(64, "C1")
        return x, costs, labels

    def test_true_loss_not_conservative_label(self):
        costs = np.array([[10., 20., 30., 2., 15.]])
        self.assertEqual(loss(costs, ["C1"])[0], 8.)
        self.assertEqual(loss(costs, ["C4"])[0], 0.)
        with self.assertRaises(ValueError):
            loss(costs, ["C0"])
        with self.assertRaises(ValueError):
            loss(costs, [])

    def test_all_cost_and_winner_models_can_recover_hidden_c4_region(self):
        x, costs, labels = self.data()
        for family in FAMILIES:
            model = fit_model(family, (1, 8), x, costs, labels)
            predicted = predict(model, family, x)
            if family == "legacy_label":
                self.assertTrue(np.all(predicted == "C1"))
            else:
                self.assertTrue(np.all(predicted[:32] == "C1"))
                self.assertTrue(np.all(predicted[32:] == "C4"))

    def test_serialized_cost_trees_match_runtime_predictions(self):
        x, costs, labels = self.data()
        for family in FAMILIES:
            model = fit_model(family, (2, 8), x, costs, labels)
            spec = policy_spec(model, family)
            actual = [FrozenPolicy(spec).predict(dict(zip(FEATURES, row))) for row in x]
            self.assertEqual(actual, list(predict(model, family, x)))

    def test_inner_selector_uses_only_given_training_data(self):
        x, costs, labels = self.data()
        splits = list(KFold(4, shuffle=True, random_state=1).split(x))
        config, scores = select_config("mean_winner", x, costs, labels, splits)
        self.assertEqual(config[0], 1)
        self.assertEqual(len(scores), 12)
        self.assertGreaterEqual(min(row["regret_s"] for row in scores), 0)

    def test_relative_cost_fit_ignores_common_point_level_wait(self):
        x, costs, labels = self.data()
        shifted = costs+np.arange(64)[:, None]*10.
        a = fit_model("relative_cost_tree", (2, 8), x, costs, labels)
        b = fit_model("relative_cost_tree", (2, 8), x, shifted, labels)
        np.testing.assert_array_equal(predict(a, "relative_cost_tree", x),
                                      predict(b, "relative_cost_tree", x))


if __name__ == "__main__":
    unittest.main()
