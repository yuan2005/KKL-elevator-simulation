import unittest

import numpy as np

from research.dual_bank.analysis import daily_statistics, holm, matrix, paired


class AnalysisTests(unittest.TestCase):
    def test_zero_and_constant_contrasts(self):
        self.assertEqual(paired([0.]*8, 3)["p_raw"], 1.)
        self.assertEqual(paired([0.]*8, 3)["simultaneous_ci"], [0., 0.])
        self.assertEqual(paired([-1.]*8, 3)["p_raw"], 0.)
        with self.assertRaises(ValueError):
            paired([float("nan"), 1.], 3)

    def test_holm_monotonic_and_bounded(self):
        rows = [dict(p_raw=.04), dict(p_raw=.001), dict(p_raw=.02)]
        holm(rows)
        np.testing.assert_allclose([r["p_holm"] for r in rows], [.04, .003, .04])

    def test_matrix_missing_and_duplicate_rejected(self):
        row = dict(phase="p", level="low", seed=1, regime=None, policy="a")
        self.assertEqual(len(matrix([row], "p", ["a"], ["low"], [1])), 1)
        with self.assertRaises(AssertionError):
            matrix([row, row], "p", ["a"], ["low"], [1])
        with self.assertRaises(AssertionError):
            matrix([row], "p", ["a", "b"], ["low"], [1])

    def test_daily_unit_is_eight_seeds_not_24_days(self):
        p = dict(levels=["low", "medium", "high"], evaluation_seeds=list(range(8)),
                 practical_seconds=.5, p90_ratio=1.05, lwt_increase_pp=1.)
        rows = []
        for phase in ("daily_eval", "daily_fine"):
            for level in p["levels"]:
                for seed in p["evaluation_seeds"]:
                    for policy in ("fixed_level", "fixed_global", "naive", "gated"):
                        rows.append(dict(phase=phase, level=level, seed=seed, regime=None,
                                         policy=policy, awt=10.-(policy == "gated"),
                                         p90=20., lwt=1.))
        result, differences = daily_statistics(rows, p)
        self.assertEqual(result["daily_eval"]["contrasts"][2]["n"], 8)
        self.assertTrue(result["daily_eval"]["contrasts"][2]["verified_improvement"])
        self.assertEqual(len(differences), 48)
        for row in rows:
            if row["phase"] == "daily_fine" and row["policy"] == "gated":
                row["awt"] = 10.
        result, _ = daily_statistics(rows, p)
        self.assertFalse(result["daily_eval"]["contrasts"][2]["verified_improvement"])


if __name__ == "__main__":
    unittest.main()
