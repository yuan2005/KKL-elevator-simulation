from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from controllers import FEATURES
from revalidate_switching import FrozenPolicy, Study, canonical, digest, fixed, job, run_job, with_error
from traffic import SCENARIOS


class ResearchRevalidationTests(unittest.TestCase):
    def tree(self):
        return dict(kind="tree", features=FEATURES, nodes=[
            dict(f=0, thr=100., l=1, r=2, label="C1"),
            dict(f=-1, label="C1"), dict(f=-1, label="C7")])

    def test_feature_errors_preserve_valid_composition(self):
        feat = dict(lam5=100., s_in=.2, s_out=.3, s_thru=.3,
                    s_from_ent=.5, s_to_ent=.6, s_high=.4)
        original = dict(feat)
        for error in ("rate_low", "rate_high", "entrance_low", "entrance_high", "none"):
            result = with_error(feat, error)
            self.assertAlmostEqual(result["s_from_ent"], result["s_in"]+result["s_thru"])
            self.assertAlmostEqual(result["s_to_ent"], result["s_out"]+result["s_thru"])
            self.assertLessEqual(result["s_in"]+result["s_out"]+result["s_thru"], 1.)
            self.assertGreaterEqual(min(result.values()), 0)
        self.assertEqual(feat, original)
        self.assertEqual(with_error(feat, "rate_low")["lam5"], 80.)
        with self.assertRaises(ValueError):
            with_error(feat, "unknown")

    def test_frozen_policy_has_exact_threshold_and_error_behavior(self):
        policy = FrozenPolicy(self.tree())
        self.assertEqual(policy.predict(dict(lam5=100)), "C1")
        self.assertEqual(policy.predict(dict(lam5=101)), "C7")
        high = FrozenPolicy(dict(self.tree(), error="rate_high"))
        self.assertEqual(high.predict(dict(lam5=90)), "C7")

    def test_constant_trees_and_errors_canonicalize_to_fixed(self):
        tree = dict(kind="tree", features=FEATURES, nodes=[dict(f=-1, label="C1")],
                    error="rate_high", ablation="parking_off")
        self.assertEqual(canonical(tree), dict(kind="fixed", combo="C1", ablation="parking_off"))
        self.assertEqual(canonical(dict(fixed("C1"), error="rate_high")), fixed("C1"))

    def test_jobs_survive_json_roundtrip_without_cache_identity_changes(self):
        sc = replace(SCENARIOS["S3"], duration=30)
        case = job(dict(kind="scenario", scenario=asdict(sc), seed=1), self.tree())
        self.assertEqual(json.loads(json.dumps(case)), case)
        self.assertEqual(digest(json.loads(json.dumps(case))), digest(case))

    def test_real_small_case_drains_and_valid_cache_resumes(self):
        with TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "cases").mkdir()
            (output / "passengers").mkdir()
            sc = replace(SCENARIOS["S3"], duration=60)
            case = job(dict(kind="scenario", scenario=asdict(sc), seed=925291), fixed("C1"))
            result = run_job((case, output, "test-source"))
            resumed = run_job((case, output, "test-source"))
            self.assertEqual(result, resumed)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["incomplete"], 0)
            with self.assertRaises(RuntimeError):
                run_job((case, output, "changed-source"))

    def test_pairing_rejects_different_demand(self):
        a = dict(input_hash="a", input_n=10)
        Study.check_pair(a, deepcopy(a))
        with self.assertRaises(ValueError):
            Study.check_pair(a, dict(input_hash="b", input_n=10))

    def test_development_confirmation_robust_seeds_are_disjoint(self):
        from revalidate_switching import DEV_SEEDS, BOUNDARY_SEEDS, CONFIRM_SEEDS, ROBUST_SEEDS
        groups = [set(x) for x in (DEV_SEEDS, BOUNDARY_SEEDS, CONFIRM_SEEDS, ROBUST_SEEDS)]
        for index, group in enumerate(groups):
            for other in groups[index+1:]:
                self.assertFalse(group & other)


if __name__ == "__main__":
    unittest.main()
