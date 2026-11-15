import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

import config as C
from complete_experiments import fixed, load
from controllers import FEATURES
from threshold_applicability import (
    ORIGINAL, PROFILES, SCENES, SEEDS, Study, digest, holm, input_records, make_case, run_case,
)


class ThresholdApplicabilityTests(unittest.TestCase):
    def test_design_has_288_cases_and_leaves_tree_unchanged(self):
        with TemporaryDirectory() as folder:
            study = Study(folder, workers=1)
            self.assertEqual(len(study.jobs()), 288)
            self.assertEqual(len({digest(job) for job in study.jobs()}), 288)
            source = load(ORIGINAL / "e1_tree.json")
            self.assertEqual(study.tree["nodes"], source["nodes"])
            self.assertEqual(study.tree["features"], source["features"])
            self.assertEqual(study.tree["nodes"][0]["thr"], 300.96665954589844)
            self.assertFalse(study.protocol["tuning"])

    def test_holm_is_monotone_in_order_and_caps_at_one(self):
        np.testing.assert_allclose(holm([.01, .04, .03]), [.03, .06, .06])
        np.testing.assert_allclose(holm([1., 0., .8]), [1., 0., 1.])
        with self.assertRaises(ValueError):
            holm([np.nan])
        with self.assertRaises(ValueError):
            holm([1.1])

    def test_piecewise_input_keeps_unique_people_and_stage_groups(self):
        spec = make_case("office_single", "low_high_low", SEEDS[0], fixed("C1"))
        records, duration, generation = input_records(json.dumps(spec["input"], sort_keys=True))
        self.assertEqual(duration, 1500)
        self.assertTrue(records)
        self.assertEqual([r[0] for r in records], list(range(len(records))))
        self.assertTrue(all(0 <= record[1] < duration for record in records))
        self.assertEqual([r[1] for r in records], sorted(r[1] for r in records))
        self.assertGreaterEqual(generation["outside_horizon"], 0)
        self.assertEqual({r[4]//1_000_000 for r in records}, {0, 1, 2})
        self.assertEqual(input_records(json.dumps(spec["input"], sort_keys=True))[0], records)

    def test_office_is_only_demand_change_not_model_change(self):
        self.assertEqual(SCENES["office_single"]["entrances"], {"G": 1.})
        self.assertEqual(SCENES["office_dual"]["entrances"], C.ENTRANCE_WEIGHTS)
        self.assertEqual(SCENES["office_single"]["weights"], SCENES["office_dual"]["weights"])
        self.assertFalse(SCENES["office_single"]["stairs"])
        self.assertEqual(SCENES["office_single"]["thru"], 0)
        self.assertEqual(PROFILES["low_high_low"], [(300, 100), (900, 450), (300, 100)])
        self.assertFalse(set(SEEDS) & set(range(936000, 941010)))

    def test_actual_small_transition_drains_with_static_and_map_controls(self):
        previous = C.DT, C.DRAIN_TIME
        try:
            with TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "cases").mkdir()
                (root / "passengers").mkdir()
                tree = dict(kind="tree", features=FEATURES, nodes=[
                    dict(f=0, thr=100., l=1, r=2, label="C1"),
                    dict(f=-1, label="C1"), dict(f=-1, label="C2")])
                specifications = [
                    make_case("office_dual", "low_high_low", SEEDS[0], controller)
                    for controller in (fixed("C4"), dict(kind="realtime", tree=tree))]
                for spec in specifications:
                    spec["input"]["schedule"] = [[30, 100], [60, 450], [30, 100]]
                rows = [run_case((spec, root, "unit-source")) for spec in specifications]
                self.assertEqual(rows[0]["input_hash"], rows[1]["input_hash"])
                self.assertTrue(all(row["incomplete"] == 0 for row in rows))
                self.assertEqual(rows[0], run_case((specifications[0], root, "unit-source")))
                with self.assertRaises(RuntimeError):
                    run_case((specifications[0], root, "other-source"))
                for row in rows:
                    with gzip.open(root / "passengers" / f"{row['case_id']}.json.gz", "rt", encoding="utf-8") as stream:
                        raw = json.load(stream)
                    self.assertTrue(all(p[1] <= p[5] < p[6] for p in raw["passengers"]))
                self.assertGreater(rows[1]["switches"], 0)
        finally:
            C.DT, C.DRAIN_TIME = previous


if __name__ == "__main__":
    unittest.main()
