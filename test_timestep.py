from copy import deepcopy
import unittest

from check_timestep import analyze, demand
import experiments as E


class TimestepScreenTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            dict(profile="S3", combo=combo, seed=seed, dt=dt, input_hash=str(seed),
                 status="completed", awt=20.+index, p90=40., lwt=0.,
                 incomplete=0, residual_calls=0)
            for index, combo in enumerate(E.MAP_COMBOS)
            for seed in (1, 2, 3) for dt in (.125, .0625)]

    def screen(self, rows):
        return analyze(rows, seeds=(1, 2, 3), steps=(.125, .0625), profiles=("S3",))

    def test_equal_trajectories_pass(self):
        self.assertTrue(self.screen(self.rows)["passed"])

    def test_missing_duplicate_or_mismatched_input_is_rejected(self):
        duplicate = self.rows+[self.rows[0]]
        different = deepcopy(self.rows)
        different[0]["input_hash"] = "different"
        for rows in (self.rows[:-1], duplicate, different):
            with self.assertRaises(ValueError):
                self.screen(rows)

    def test_awt_and_tail_instability_are_detected(self):
        for metric, delta in (("awt", 10.), ("p90", 20.)):
            rows = deepcopy(self.rows)
            for row in rows:
                if row["dt"] == .125 and row["combo"] == "C1":
                    row[metric] += delta
            self.assertFalse(self.screen(rows)["passed"])

    def test_undrained_case_blocks_progress(self):
        self.rows[0].update(status="invalid_or_undrained", awt=None, p90=None, lwt=None,
                            incomplete=1)
        report = self.screen(self.rows)
        self.assertFalse(report["passed"])
        self.assertEqual(len(report["failures"]), 1)

    def test_practical_winner_reversal_blocks_progress(self):
        for row in self.rows:
            row["awt"] = 20.
            if row["combo"] == "C1":
                row["awt"] = 18.9 if row["dt"] == .125 else 20.
            if row["combo"] == "C2":
                row["awt"] = 20. if row["dt"] == .125 else 18.9
        self.assertFalse(self.screen(self.rows)["passed"])

    def test_current_scenario_and_overlap_input_shapes(self):
        for profile in ("S3", "S4", "lunch_overlap"):
            records, end, evaluate_from = demand(profile, 914301)
            self.assertGreater(len(records), 0)
            self.assertEqual([r[0] for r in records], list(range(len(records))))
            self.assertEqual([r[1] for r in records], sorted(r[1] for r in records))
            self.assertTrue(all(0 <= r[1] < end+3 for r in records))
            self.assertGreater(sum(evaluate_from <= r[1] < end for r in records), 0)
        self.assertEqual(demand("lunch_overlap", 914301)[1:], (3600., 1800.))
        with self.assertRaises(ValueError):
            demand("unknown", 1)


if __name__ == "__main__":
    unittest.main()
