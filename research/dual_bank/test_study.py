import copy
from pathlib import Path
import tempfile
import unittest

import numpy as np

import config as C
from research.dual_bank.control import DayControl, FixedControl, rule_key, state_bin
from research.dual_bank.demand import REGIMES, measurement_seconds, sustained
from research.dual_bank.engine import DualBankSim
from research.dual_bank.experiment import (
    branch, cohort_metrics, digest, fit_rules, lock, locked, protocol,
)
from traffic import Passenger


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.settings = C.DT, C.DRAIN_TIME
        C.DT, C.DRAIN_TIME = .5, 3600.

    def tearDown(self):
        C.DT, C.DRAIN_TIME = self.settings

    def test_sustained_generation_reproducible_unique(self):
        for regime in REGIMES:
            a, end, _ = sustained("low", regime, 8531001)
            b, other_end, _ = sustained("low", regime, 8531001)
            self.assertEqual([(p.id, p.t, p.o, p.d, p.group) for p in a],
                             [(p.id, p.t, p.o, p.d, p.group) for p in b])
            self.assertEqual(end, other_end)
            self.assertEqual(len(a), len({p.id for p in a}))
            self.assertTrue(any(600 <= p.t < 600+measurement_seconds(regime) for p in a))

    def test_local_bins(self):
        self.assertEqual(state_bin(dict(latched=0, onboard=0)), "clear")
        self.assertEqual(state_bin(dict(latched=2, onboard=1)), "occupied")
        self.assertEqual(state_bin(dict(latched=3, onboard=0)), "busy")
        self.assertEqual(state_bin(dict(latched=0, onboard=16)), "busy")

    def test_naive_and_gated_keep_no_remote_access(self):
        mapping = {r: "marginal_uniform_b1" for r in REGIMES}
        class Local:
            def observable(self, bank):
                return dict(latched=bank, onboard=0, idle=3-bank)
        gated = DayControl("pickup_uniform_b0", mapping, "gated")
        self.assertEqual(gated.update(Local(), 0), {0: "pickup_uniform_b0", 1: "pickup_uniform_b0"})
        self.assertEqual(gated.update(Local(), 450*60),
                         {0: "pickup_uniform_b0", 1: "pickup_uniform_b0"})
        naive = DayControl("pickup_uniform_b0", mapping)
        self.assertEqual(naive.update(Local(), 0),
                         {0: "marginal_uniform_b1", 1: "marginal_uniform_b1"})

    def test_gated_uses_each_bank_only(self):
        class Local:
            def observable(self, bank):
                return dict(latched=bank*3, onboard=0, idle=3)
        key = rule_key("S6", "pickup_uniform_b0", Local().observable(0))
        control = DayControl("pickup_uniform_b0", dict.fromkeys(REGIMES, "pickup_uniform_b0"),
                             "gated", {key: "marginal_uniform_b0"})
        self.assertEqual(control.update(Local(), 0),
                         {0: "marginal_uniform_b0", 1: "pickup_uniform_b0"})

    def test_same_state_branch_does_not_change_parent(self):
        passengers = [Passenger(i, float(i*4), 3, 10) for i in range(10)]
        sim = DualBankSim(passengers, FixedControl("pickup_uniform_b0"), 50.)
        while sim.t < 10:
            sim.step()
        state = digest(sim.state_record())
        identifiers = {p.id for p in sim.pax if p.t_board is None}
        fork, metric, before = branch(
            sim, {0: "marginal_uniform_b1", 1: "pickup_uniform_b0"}, 60.,
            identifiers, state, {})
        self.assertEqual(before, state)
        self.assertEqual(digest(sim.state_record()), state)
        self.assertEqual(metric["incomplete"], 0)
        self.assertEqual(fork.policy_names[1], "pickup_uniform_b0")
        self.assertGreater(fork.t, sim.t)

    def test_remaining_wait_not_total_wait(self):
        rows = [dict(id=1, arrival=0., board=110., alight=130.),
                dict(id=2, arrival=105., board=115., alight=140.)]
        self.assertEqual(cohort_metrics(rows, {1, 2}, 100.)["awt"], 10.)
        with self.assertRaises(AssertionError):
            cohort_metrics(rows, {3})
        with self.assertRaises(AssertionError):
            cohort_metrics([], set())
        self.assertTrue(cohort_metrics([], set(), allow_empty=True)["empty_cohort"])

    def test_selection_lock_rejects_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock(root, "choice", {"a": 1})
            self.assertEqual(locked(root, "choice"), {"a": 1})
            (root / "choice.json").write_text('{"a":2}', encoding="utf-8")
            with self.assertRaises(AssertionError):
                locked(root, "choice")

    def test_rules_pair_by_seed_not_bank(self):
        records = []
        for seed in range(4):
            keep = dict(level="low", seed=seed, boundary=450, policy="keep",
                        awt=10., p90=20., lwt=0.)
            records.append(dict(metrics=keep))
            for bank in (0, 1):
                metric = dict(keep, policy=f"bank{bank}", bank=bank,
                              regime="S5", candidate="marginal_uniform_b0", awt=8., p90=18.)
                extra = dict(observations={str(bank): dict(latched=1, onboard=0)},
                             snapshot=dict(policy_names={str(bank): "pickup_uniform_b0"}))
                records.append(dict(metrics=metric, extra=extra))
        choice = fit_rules(records)
        self.assertEqual(choice["details"][0]["seeds"], 4)
        self.assertEqual(len(choice["rules"]["low"]), 1)
        for raw in records:
            if raw["metrics"]["policy"] != "keep":
                raw["metrics"]["awt"] = 9.9
        self.assertFalse(fit_rules(records)["rules"]["low"])

    def test_protocol_budget_and_disjoint_seeds(self):
        p = protocol()
        self.assertEqual(sum(v for k, v in p["expected"].items() if k != "total"),
                         p["expected"]["total"])
        self.assertFalse(set(p["development_seeds"]) & set(p["evaluation_seeds"]))


if __name__ == "__main__":
    unittest.main()
