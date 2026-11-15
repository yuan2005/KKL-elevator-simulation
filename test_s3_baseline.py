from dataclasses import asdict, replace
import unittest

from experiments import SCENARIOS, e1_points
from traffic import SCENARIOS as LEGACY_SCENARIOS, generate_scenario


class S3BaselineTests(unittest.TestCase):
    def test_only_s3_and_s4_intensity_changes(self):
        self.assertEqual(SCENARIOS["S3"].lam5, 225)
        self.assertEqual(LEGACY_SCENARIOS["S3"].lam5, 90)
        for key in SCENARIOS:
            expected = (replace(LEGACY_SCENARIOS[key], lam5=LEGACY_SCENARIOS[key].lam5*2.5)
                        if key in ("S3", "S4") else LEGACY_SCENARIOS[key])
            self.assertEqual(asdict(SCENARIOS[key]), asdict(expected))

    def test_new_baseline_exactly_matches_previous_2_5_inputs(self):
        for seed, count in ((914201, 564), (914202, 570), (914203, 533)):
            with self.subTest(seed=seed):
                current, stairs = generate_scenario(SCENARIOS["S3"], seed)
                previous, previous_stairs = generate_scenario(
                    replace(LEGACY_SCENARIOS["S3"], lam5=225), seed)
                self.assertEqual([asdict(p) for p in current], [asdict(p) for p in previous])
                self.assertEqual(stairs, previous_stairs)
                self.assertEqual(len(current), count)

    def test_e1_representative_point_uses_new_baseline(self):
        points = e1_points(1)
        point = next(row["sc"] for row in points if row["point"] == "S3")
        self.assertEqual(point.lam5, 225)


if __name__ == "__main__":
    unittest.main()
