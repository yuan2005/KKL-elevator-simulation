from dataclasses import asdict, replace
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import config as C
import experiments as E
from complete_experiments import (
    CompleteStudy, DelayedMap, SEED_STARTS, TracedPolicy, build_controller,
    case, day, demand, fixed, intervals, paired_summary, run_case, switch_cohort,
)
from controllers import FEATURES, ForecastMapSwitch, MapSwitch
from strategies import NoParking, make_combos
from traffic import Passenger


class FullCoverageTests(unittest.TestCase):
    def tree(self):
        return dict(kind="tree", features=FEATURES, nodes=[
            dict(f=0, thr=100., l=1, r=2, label="C1"),
            dict(f=-1, label="C1"), dict(f=-1, label="C7")])

    def test_error_changes_actual_policy_and_keeps_raw_features(self):
        policy = TracedPolicy(self.tree(), "rate_high")
        feature = dict(lam5=90., s_in=.2, s_out=.3, s_thru=.3,
                       s_from_ent=.5, s_to_ent=.6, s_high=.4)
        original = dict(feature)
        policy.time = 60.
        self.assertEqual(policy.predict(feature), "C7")
        time, clean, perturbed, chosen, ideal = policy.trace[0]
        self.assertEqual(time, 60.)
        self.assertEqual(clean, original)
        self.assertEqual(feature, original)
        self.assertEqual(perturbed["lam5"], 108.)
        self.assertEqual((chosen, ideal), ("C7", "C1"))

    def test_delayed_observation_excludes_recent_and_future_passengers(self):
        passengers = [Passenger(i, t, 3, 10, i) for i, t in enumerate((5., 35., 70., 95., 120.))]
        sim = SimpleNamespace(pax=passengers, i_next=4)
        combos = make_combos()
        policy = TracedPolicy(self.tree(), "none")
        controller = DelayedMap(combos, policy, 60)
        controller.update(sim, 100.)
        self.assertEqual(policy.trace[-1][1]["lam5"], 2*300/40)
        controller.update(sim, 30.)
        self.assertEqual(policy.trace[-1][1]["lam5"], 0.)

    def test_zero_delay_preserves_existing_realtime_features(self):
        passengers = [Passenger(i, t, 3, 10, i) for i, t in enumerate((5., 35., 70., 95.))]
        sim = SimpleNamespace(pax=passengers, i_next=4)
        a, b = TracedPolicy(self.tree(), "none"), TracedPolicy(self.tree(), "none")
        combos = make_combos()
        MapSwitch(combos, a).update(sim, 100.)
        DelayedMap(combos, b, 0).update(sim, 100.)
        self.assertEqual(a.trace, b.trace)

    def test_forecast_uses_separate_nominal_stream_not_actual_future(self):
        spec = dict(kind="forecast", tree=self.tree(), error="rate_low", ablation="parking_off")
        combos = make_combos()
        controller, policy = build_controller(spec, combos, [], 120, (941000, 941001))
        self.assertIsInstance(controller.controller, ForecastMapSwitch)
        self.assertTrue(all(isinstance(combo.parking, NoParking) for combo in combos.values()))
        controller.update(SimpleNamespace(pax=[], i_next=0), 30.)
        self.assertEqual(policy.trace[-1][0], 30.)
        self.assertGreater(policy.trace[-1][1]["lam5"], 0)
        self.assertEqual(controller.controller.k, 2)

    def test_formal_day_matches_previous_formal_generator_exactly(self):
        stream, duration = demand(json.dumps(day(937001), sort_keys=True))
        expected = E.generate_day(E.load_timetable(E.TIMETABLE), 937001)
        self.assertEqual(stream, tuple((p.id, p.t, p.o, p.d, p.group) for p in expected))
        self.assertEqual(duration, E.DAY_LEN)

    def test_outage_and_real_cache_with_no_censoring(self):
        previous = C.DT, C.DRAIN_TIME
        try:
            with TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "cases").mkdir()
                (root / "passengers").mkdir()
                scenario = replace(E.SCENARIOS["S3"], duration=90., lam5=80.)
                item = case(dict(kind="scenario", scenario=asdict(scenario), seed=936001), fixed("C1"), (0,))
                settings = dict(dt=.25, drain_seconds=21600, forecast_seeds=[941000], training_rate_max=800)
                row = run_case((item, root, "test-source", settings))
                self.assertEqual(row, run_case((item, root, "test-source", settings)))
                self.assertEqual(row["incomplete"], 0)
                with gzip.open(root / "passengers" / f"{row['case_id']}.json.gz", "rt", encoding="utf-8") as stream:
                    passengers = json.load(stream)["passengers"]
                self.assertTrue(passengers)
                self.assertTrue(all(p[7] != 0 and p[1] <= p[5] < p[6] for p in passengers))
                self.assertEqual(switch_cohort(row, row, root), [])
                with self.assertRaises(RuntimeError):
                    run_case((item, root, "other-source", settings))
        finally:
            C.DT, C.DRAIN_TIME = previous

    def test_intervals_clamp_drain_and_use_half_open_boundaries(self):
        log = [(0., "C1 one"), (30., "C7 two"), (90., "C1 one"), (130., "C7 two")]
        self.assertEqual(intervals(log, 120), [(0., 30., "C1"), (30., 90., "C7"), (90., 120, "C1")])

    def test_high_demand_with_one_car_out_has_identical_passengers_across_caps(self):
        previous = C.DT, C.DRAIN_TIME
        try:
            with TemporaryDirectory() as folder:
                root = Path(folder)
                for combo, split in (("C2", (.95, 0., .05)), ("C3", (0., .95, .05))):
                    trajectories = []
                    for index, dt in enumerate((.25, .125, .0625)):
                        output = root / f"{combo}_{index}"
                        (output / "cases").mkdir(parents=True)
                        (output / "passengers").mkdir()
                        sc = replace(E.SCENARIOS["S3"], duration=180., lam5=800.,
                                     split=split, profile="uniform", weights=E.mix_weights(1.))
                        specification = case(dict(kind="scenario", scenario=asdict(sc), seed=936999),
                                             fixed(combo), (0,))
                        settings = dict(dt=dt, drain_seconds=21600, forecast_seeds=[941000], training_rate_max=800)
                        row = run_case((specification, output, f"cap-{dt}", settings))
                        with gzip.open(output / "passengers" / f"{row['case_id']}.json.gz",
                                       "rt", encoding="utf-8") as stream:
                            trajectories.append(json.load(stream)["passengers"])
                    self.assertTrue(trajectories[0])
                    self.assertEqual(trajectories[0], trajectories[1])
                    self.assertEqual(trajectories[1], trajectories[2])
        finally:
            C.DT, C.DRAIN_TIME = previous

    def test_paired_statistics_and_alignment_guards(self):
        result = paired_summary([2., 4.], [1., 3.])
        self.assertEqual(result["delta"], 1.)
        self.assertEqual(result["ci_low"], 1.)
        with self.assertRaises(ValueError):
            paired_summary([1.], [1.])
        with self.assertRaises(ValueError):
            paired_summary([1., 2.], [1.])

    def test_seed_groups_do_not_reuse_prior_confirmation(self):
        groups = [set(range(start, start+10)) for start in SEED_STARTS.values()]
        for index, group in enumerate(groups):
            self.assertFalse(group & set(range(925101, 925407)))
            for other in groups[index+1:]:
                self.assertFalse(group & other)

    def test_smoke_protocol_resume_rejects_source_or_setting_changes(self):
        with TemporaryDirectory() as folder:
            a = CompleteStudy(folder, workers=1, smoke=True)
            b = CompleteStudy(folder, workers=2, smoke=True)
            self.assertEqual(a.fingerprint, b.fingerprint)
            with self.assertRaises(RuntimeError):
                CompleteStudy(folder, smoke=False)


if __name__ == "__main__":
    unittest.main()
