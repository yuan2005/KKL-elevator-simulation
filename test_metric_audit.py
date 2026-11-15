from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

from experiments import SWITCHABLE, regret
from metrics import summarize
from traffic import Passenger


class MetricAuditTests(unittest.TestCase):
    def test_waiting_and_total_journey_have_distinct_definitions(self):
        passengers = [
            Passenger(0, 0, 1, 4, t_board=2., t_alight=10.),
            Passenger(1, 5, 1, 4, t_board=13., t_alight=25.)]
        sim = SimpleNamespace(pax=passengers, gen_end=30., t=40., cars=[])
        result = summarize(sim)
        self.assertEqual(result["n"], 2)
        self.assertEqual(result["awt"], 5.)
        self.assertEqual(result["att"], 15.)
        self.assertAlmostEqual(result["p90"], float(np.percentile([2, 8], 90)))
        self.assertEqual(result["unserved"], 0)

    def test_arrival_cohort_is_half_open_and_long_wait_is_strict(self):
        passengers = [
            Passenger(0, 0, 1, 4, t_board=60., t_alight=80.),
            Passenger(1, 10, 1, 4, t_board=71., t_alight=90.),
            Passenger(2, 20, 1, 4, t_board=100., t_alight=110.)]
        sim = SimpleNamespace(pax=passengers, gen_end=20., t=120., cars=[])
        result = summarize(sim)
        self.assertEqual(result["n"], 2)
        self.assertEqual(result["awt"], 60.5)
        self.assertEqual(result["lwt"], 50.)
        self.assertEqual(summarize(sim, t_from=10, t_to=20)["n"], 1)

    def points(self):
        values = {f"awt_{combo}": [10., 10.] for combo in SWITCHABLE}
        values["awt_C7"] = [8., 7.]
        return pd.DataFrame(dict(values, label=["C1", "C1"], awt_C0=[1., 1.]))

    def test_regret_uses_actual_candidate_minimum_not_screened_label(self):
        self.assertEqual(list(regret(self.points(), ["C1", "C7"])), [2., 0.])
        self.assertTrue((regret(self.points(), ["C7", "C7"]) >= 0).all())

    def test_regret_rejects_misaligned_or_ineligible_predictions(self):
        for predictions in (["C1"], ["C1", "unknown"], ["C0", "C1"]):
            with self.assertRaises(ValueError):
                regret(self.points(), predictions)


if __name__ == "__main__":
    unittest.main()
