"""Focused isolation, physical-service and branch/replay tests."""
from copy import deepcopy
import json
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config as C
from building import HEIGHT, IDX, N_FLOORS
from research.dual_bank.engine import BANKS, DualBankSim, configurations
from research.dual_bank.physics import PhysicalSim
from research.dual_bank.policies import DemandParking
from traffic import Passenger


class Constant:
    interval = 30.

    def __init__(self, choice="pickup_uniform_b0"):
        self.choice = choice
        self.seen = []

    def update(self, sim, t):
        self.seen.append((t, sim.observable(0), sim.observable(1)))
        return self.choice


def sample(count=36):
    rng = random.Random(7381)
    result = []
    for identifier in range(count):
        origin = rng.randrange(N_FLOORS)
        destination = rng.choice([f for f in range(N_FLOORS) if f != origin])
        result.append(Passenger(identifier, float(identifier % 9), origin, destination))
    return sorted(result, key=lambda p: (p.t, p.id))


class DualBankTests(unittest.TestCase):
    def release(self, sim):
        while sim.i_next < len(sim.pax) and sim.pax[sim.i_next].t <= sim.t:
            sim._register(sim.pax[sim.i_next])
            sim.i_next += 1

    def stationary(self, car, floor, direction):
        car.floor, car.x, car.dir = floor, HEIGHT[floor], direction
        car.state, car.v, car.target = "idle", 0., None

    def test_exact_eight_candidates_and_motion_reuse(self):
        names = configurations()
        self.assertEqual(len(names), 8)
        self.assertEqual(names["marginal_demand_b1"],
                         dict(dispatch="marginal", parking="demand", bypass=True))
        names.clear()
        self.assertEqual(len(configurations()), 8)
        self.assertIs(DualBankSim.move, PhysicalSim.move)

    def test_arrival_two_latches_one_unique_queue_and_no_input_mutation(self):
        original = [Passenger(0, 0., 4, 10), Passenger(1, 0., 4, 12)]
        sim = DualBankSim(original, Constant(), 0.)
        self.release(sim)
        self.assertEqual(len(sim.waiting[(4, 1)]), 2)
        self.assertEqual(set(sim.calls), {(4, 1, 0), (4, 1, 1)})
        for call in sim.calls.values():
            self.assertEqual(len(call.queue), 1)
            self.assertNotIn(call.queue[0], sim.pax)
            self.assertFalse(hasattr(call.queue[0], "id"))
        sim.run()
        self.assertTrue(all(p.t_board is None for p in original))

    def test_ghost_is_not_cancelled_and_forced_empty_open_has_door_time(self):
        sim = DualBankSim([Passenger(0, 0., 5, 12)], Constant(), 0.)
        self.release(sim)
        self.stationary(sim.cars[0], 5, 1)
        self.stationary(sim.cars[3], 5, 1)
        sim.stop_here(sim.cars[0], forced=True)
        self.assertNotIn((5, 1, 0), sim.calls)
        self.assertIn((5, 1, 1), sim.calls)
        self.assertEqual(len(sim.physical_board), 1)
        sim.stop_here(sim.cars[3], forced=True)
        self.assertNotIn((5, 1, 1), sim.calls)
        self.assertEqual(sim.cars[3].state, "doors")
        self.assertEqual(sim.cars[3].dwell_left, C.T_DOOR_OPEN+C.T_DOOR_CLOSE)
        self.assertTrue(sim.stop_log[-1]["empty"])
        self.assertEqual(sim.stop_log[-1]["bank"], 1)
        sim.check_conservation()

    def test_remote_boarding_leaves_local_observation_assignment_identical(self):
        sim = DualBankSim([Passenger(0, 0., 7, 18)], Constant(), 0.)
        self.release(sim)
        before = sim.observable(1)
        control = deepcopy(sim)
        self.stationary(sim.cars[0], 7, 1)
        sim.stop_here(sim.cars[0], forced=True)
        self.assertEqual(before, sim.observable(1))
        for branch in (sim, control):
            branch.assign(branch.calls[(7, 1, 1)])
        self.assertEqual(sim.decision_log[-1], control.decision_log[-1])
        self.assertEqual(sim.calls[(7, 1, 1)].queue[0].d,
                         control.calls[(7, 1, 1)].queue[0].d)

    def test_synthetic_od_and_singleton_invariant_to_true_od_and_count(self):
        left = DualBankSim([Passenger(0, 0., 6, 8)], Constant(), 0.)
        right = DualBankSim([Passenger(i, 0., 6, 19) for i in range(20)], Constant(), 0.)
        self.release(left)
        self.release(right)
        for bank in BANKS:
            a, b = left.observer(bank), right.observer(bank)
            self.assertEqual(vars(a.calls[(6, 1, bank)].queue[0]),
                             vars(b.calls[(6, 1, bank)].queue[0]))
            self.assertEqual(a.calls[(6, 1, bank)].car, b.calls[(6, 1, bank)].car)
            self.assertEqual([c.id for c in a.cars], list(BANKS[bank]))
            self.assertFalse(hasattr(a, "pax"))
            self.assertFalse(hasattr(a, "waiting"))

    def test_capacity_clears_and_represses_only_own_button(self):
        with patch.object(C, "CAPACITY", 2):
            sim = DualBankSim([Passenger(i, 0., 5, 12) for i in range(5)], Constant(), 0.)
            self.release(sim)
            other_before = deepcopy(sim.button_history[1])
            self.stationary(sim.cars[0], 5, 1)
            sim.stop_here(sim.cars[0], forced=True)
            self.assertEqual(sim.cars[0].load, 2)
            self.assertEqual(len(sim.waiting[(5, 1)]), 3)
            self.assertIn((5, 1, 0), sim.calls)
            self.assertIn((5, 1, 1), sim.calls)
            self.assertEqual(sim.button_history[1], other_before)
            self.assertEqual(sim.button_history[0][-1]["source"], "repress")
            self.assertEqual([r["action"] for r in sim.latch_log if r["bank"] == 0],
                             ["set", "clear", "set"])
            sim.check_conservation()
            sim.run()
            self.assertEqual(sim.metrics()["incomplete"], 0)

    def test_full_car_still_represses_and_opposite_latch_is_not_cleared(self):
        with patch.object(C, "CAPACITY", 1):
            sim = DualBankSim([Passenger(0, 0., 5, 12), Passenger(1, 0., 5, 2),
                               Passenger(2, 0., 5, 13)], Constant(), 0.)
            self.release(sim)
            self.stationary(sim.cars[0], 5, 1)
            sim.board(sim.cars[0], 5, extra=False)
            old_history = len(sim.button_history[0])
            sim.stop_here(sim.cars[0], forced=True)
            self.assertIn((5, 1, 0), sim.calls)
            self.assertIn((5, -1, 0), sim.calls)
            self.assertGreater(len(sim.button_history[0]), old_history)
            self.assertEqual(len(sim.physical_board), 1)
            sim.check_conservation()

    def test_simultaneous_two_cars_board_physical_passenger_once(self):
        sim = DualBankSim([Passenger(0, 0., 5, 15)], Constant(), 0.)
        self.release(sim)
        identifiers = [sim.calls[(5, 1, bank)].car for bank in BANKS]
        for identifier in identifiers:
            self.stationary(sim.cars[identifier], 5, 1)
        for identifier in identifiers:
            sim.update_car(sim.cars[identifier])
        self.assertEqual(sum(c.load for c in sim.cars), 1)
        self.assertEqual(len(sim.physical_board), 1)
        self.assertEqual(sum(row["empty"] for row in sim.stop_log), 1)
        sim.check_conservation()

    def test_local_assignments_all_eight_controls(self):
        for policy in configurations():
            sim = DualBankSim(sample(12), Constant(policy), 10.)
            sim.run()
            self.assertTrue(sim.decision_log)
            self.assertTrue(all(row["car"] in BANKS[row["bank"]]
                                for row in sim.decision_log if row["car"] is not None))
            self.assertEqual(sim.metrics()["incomplete"], 0, policy)
            self.assertFalse(sim.calls, policy)
            self.assertTrue(all(c.state != "doors" for c in sim.cars))

    def test_policy_switch_preserves_physical_and_latched_state(self):
        sim = DualBankSim(sample(), Constant(), 15.)
        for _ in range(25):
            sim.step()
        before = sim.state_record()
        sim.set_policy("marginal_demand_b1", bank=0)
        after = sim.state_record()
        for key in ("t", "i_next", "cars", "waiting", "latches", "button_history",
                    "physical_board", "physical_alight", "registration"):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(sim.policy_names[1], "pickup_uniform_b0")
        self.assertEqual(sim.policy_names[0], "marginal_demand_b1")
        sim.check_conservation()

    def test_controller_partial_mapping_and_restricted_view(self):
        class Controller:
            interval = 30.

            def update(self, view, t):
                for forbidden in ("pax", "waiting", "calls", "cars", "service_records"):
                    if hasattr(view, forbidden):
                        raise AssertionError(forbidden)
                own = view.observable(0)
                for name in ("latched", "onboard", "idle"):
                    if name not in own:
                        raise AssertionError(name)
                own["cars"].clear()
                return {0: "marginal_uniform_b1"}
        sim = DualBankSim(sample(2), Controller(), 1.)
        sim.step()
        self.assertEqual(sim.policy_names, {0: "marginal_uniform_b1", 1: "pickup_uniform_b0"})
        self.assertEqual(len(sim.cars), 6)

    def test_controller_initial_choice_is_applied_at_zero(self):
        sim = DualBankSim([], Constant("marginal_demand_b1"), 1.)
        sim.step()
        self.assertEqual(set(sim.policy_names.values()), {"marginal_demand_b1"})
        self.assertEqual(sim.controller.seen[0][0], 0.)

    def test_demand_parking_uses_local_presses_including_repress(self):
        with patch.object(C, "CAPACITY", 1):
            sim = DualBankSim([Passenger(i, 0., 5, 12) for i in range(3)], Constant(), 0.)
            self.release(sim)
            self.stationary(sim.cars[0], 5, 1)
            sim.stop_here(sim.cars[0], forced=True)
            sim.set_policy("pickup_demand_b0")
            seen = []

            def capture(parking, view, weights):
                seen.append(([c.id for c in view.cars], list(weights)))
            with patch.object(DemandParking, "update", capture):
                sim._update_parking()
            self.assertEqual(seen[0][0], [0, 1, 2])
            self.assertEqual(seen[1][0], [3, 4, 5])
            self.assertEqual(seen[0][1][5], 4)
            self.assertEqual(seen[1][1][5], 3)

    def test_demand_parking_invariant_when_remote_bank_took_people(self):
        sim = DualBankSim([Passenger(0, 0., 7, 18)], Constant(), 0.)
        self.release(sim)
        twin = deepcopy(sim)
        self.stationary(sim.cars[0], 7, 1)
        sim.stop_here(sim.cars[0], forced=True)
        for branch in (sim, twin):
            branch.set_policy("pickup_demand_b0", bank=1)
            branch._update_parking()
        self.assertEqual(sim._parkings[1].targets, twin._parkings[1].targets)

    def test_one_bank_switch_does_not_reschedule_other_bank_parking(self):
        sim = DualBankSim(sample(8), Constant("pickup_demand_b0"), 5.)
        while sim.t < 30.:
            sim.step()
        other_targets = deepcopy(sim._parkings[1].targets)
        deadline = sim.next_parking
        sim.set_policy("marginal_demand_b1", bank=0)
        sim.controller = Constant({0: "marginal_demand_b1"})
        seen = []
        original = DemandParking.update

        def capture(parking, view, weights):
            seen.append([car.id for car in view.cars])
            return original(parking, view, weights)
        with patch.object(DemandParking, "update", capture):
            sim.step()
        self.assertEqual(seen, [[0, 1, 2]])
        self.assertEqual(sim.next_parking, deadline)
        self.assertEqual(sim._parkings[1].targets, other_targets)

    def test_deepcopy_replay_keep_switch_and_absolute_clock(self):
        sim = DualBankSim(sample(), Constant(), 15.)
        while sim.t < 30.:
            sim.step()
        clone = deepcopy(sim)
        switched = deepcopy(sim)
        clone.controller = Constant()
        switched.controller = Constant({0: "marginal_demand_b1"})
        self.assertEqual(sim.state_record(), clone.state_record())
        self.assertEqual(sim.state_record(), switched.state_record())
        sim.run()
        clone.run()
        switched.run()
        self.assertEqual(sim.service_records(), clone.service_records())
        self.assertEqual(sim.state_record(), clone.state_record())
        self.assertEqual(sim.decision_log, clone.decision_log)
        self.assertEqual(switched.controller.seen[0][0], 30.)
        self.assertTrue(all(t >= 30. for t, *_ in switched.controller.seen))
        self.assertEqual(switched.policy_names[1], sim.policy_names[1])
        self.assertEqual(switched.metrics()["incomplete"], 0)
        switched.check_conservation()

    def test_ghost_drain_and_door_completion_at_both_timesteps(self):
        for dt in (.5, .25):
            with patch.object(C, "DT", dt):
                sim = DualBankSim([Passenger(0, 0., IDX["G"], 19)], Constant(), 0.).run()
                self.assertEqual(sim.metrics()["incomplete"], 0)
                self.assertFalse(sim.calls)
                self.assertFalse(sim.waiting)
                self.assertTrue(all(c.state != "doors" for c in sim.cars))
                self.assertGreaterEqual(sim.t, sim.pax[0].t_alight)
                self.assertGreaterEqual(sim.metrics()["empty_stops"], 1)
                for stop in sim.stop_log:
                    clears = [event for event in sim.latch_log
                              if event["action"] == "clear" and event["car"] == stop["car"]
                              and event["t"] == stop["t"]]
                    self.assertTrue(all(event["bank"] == stop["bank"] for event in clears))

    def test_conservation_and_service_record_metrics_at_both_timesteps(self):
        for dt in (.5, .25):
            with patch.object(C, "DT", dt):
                sim = DualBankSim(sample(60), Constant("marginal_demand_b1"), 12.).run()
                rows = sim.service_records()
                metrics = sim.metrics()
                self.assertEqual(len(sim.physical_board), 60)
                self.assertEqual(len(sim.physical_alight), 60)
                self.assertEqual(metrics["incomplete"], 0)
                self.assertEqual(metrics["unserved"], 0)
                self.assertAlmostEqual(metrics["awt"],
                                       sum(r["board"]-r["arrival"] for r in rows)/60)
                self.assertAlmostEqual(metrics["att"],
                                       sum(r["alight"]-r["board"] for r in rows)/60)
                for row in rows:
                    self.assertGreaterEqual(row["board"], row["arrival"])
                    self.assertGreaterEqual(row["alight"], row["board"])
                    self.assertIn(row["car"], range(6))
                sim.check_conservation()

    def test_state_record_json_and_all_public_snapshots_detached(self):
        sim = DualBankSim(sample(6), Constant(), 1.)
        sim.step()
        record = sim.state_record()
        encoded = json.dumps(record, sort_keys=True, allow_nan=False)
        self.assertEqual(encoded, json.dumps(sim.state_record(), sort_keys=True, allow_nan=False))
        record["cars"].clear()
        record["button_history"][0].clear()
        record["policy_names"].clear()
        obs = sim.observable(0)
        obs["cars"].clear()
        view = sim.observer(0)
        view.cars.clear()
        snap = sim.snapshot()
        snap["cars"].clear()
        self.assertEqual(len(sim.cars), 6)
        self.assertEqual(len(sim.policy_names), 2)
        self.assertTrue(sim.button_history[0])

    def test_existing_open_doors_additional_boarding_is_auditable(self):
        for dt in (.5, .25):
            with patch.object(C, "DT", dt):
                sim = DualBankSim(
                    [Passenger(0, 0., 3, 10), Passenger(1, 25., 10, 3)],
                    Constant(), 30.).run()
                event = next(row for row in sim.board_log if 1 in row["passenger_ids"])
                self.assertTrue(event["extra"])
                self.assertEqual(event["t"], sim.physical_board[1])
                self.assertEqual(event["cleared"], [(10, -1, event["bank"])])
                stop = sim.stop_log[event["stop"]]
                self.assertLess(stop["t"], event["t"])
                self.assertEqual(stop["floor"], 10)
                self.assertEqual(stop["car"], event["car"])
                self.assertEqual(stop["initial_boarded"], 0)
                self.assertEqual(stop["boarded"], 1)
                self.assertIsNotNone(stop["closed"])
                self.assertFalse(any(row["t"] == event["t"] and row["car"] == event["car"]
                                     for row in sim.stop_log))
                self.assertTrue(any(row["action"] == "clear" and row["t"] == event["t"]
                                    and row["key"] in event["cleared"]
                                    for row in sim.latch_log))

    def test_initially_empty_ghost_session_later_boarding_is_not_empty_stop(self):
        sim = DualBankSim(
            [Passenger(0, 0., 5, 15), Passenger(1, 1., 5, 15)], Constant(), 2.)
        self.release(sim)
        for identifier in (0, 3):
            self.stationary(sim.cars[identifier], 5, 1)
        sim.stop_here(sim.cars[0], forced=True)
        sim.stop_here(sim.cars[3], forced=True)
        session = sim._open_stops[3]
        self.assertTrue(sim.stop_log[session]["initial_empty"])
        self.assertTrue(sim.stop_log[session]["empty"])
        self.assertEqual(sim.metrics()["empty_stops"], 0)
        sim.t = 1.
        self.release(sim)
        sim.cars[3].dwell_left = 0.
        sim.update_car(sim.cars[3])
        self.assertEqual(sim._open_stops[3], session)
        self.assertEqual(len(sim.stop_log), 2)
        self.assertEqual(sim.stop_log[session]["boarded"], 1)
        self.assertFalse(sim.stop_log[session]["empty"])
        self.assertIsNone(sim.stop_log[session]["closed"])
        sim.t = 3.
        sim.cars[3].dwell_left = 0.
        sim.update_car(sim.cars[3])
        self.assertNotIn(3, sim._open_stops)
        self.assertEqual(sim.stop_log[session]["closed"], 3.)
        self.assertFalse(sim.stop_log[session]["empty"])
        self.assertEqual(sim.metrics()["empty_stops"], 0)
        sim.check_conservation()

    def test_board_log_reconstructs_all_service_and_closed_session_metrics(self):
        sim = DualBankSim(sample(60), Constant("marginal_demand_b1"), 12.).run()
        boardings = [
            (identifier, row["t"], row["car"])
            for row in sim.board_log for identifier in row["passenger_ids"]]
        self.assertEqual(len(boardings), 60)
        self.assertEqual({identifier for identifier, *_ in boardings}, set(sim.physical_board))
        for identifier, t, car in boardings:
            self.assertEqual(sim.physical_board[identifier], t)
            self.assertEqual(next(p.car for p in sim.pax if p.id == identifier), car)
        for clear in (row for row in sim.latch_log if row["action"] == "clear"):
            matches = [row for row in sim.board_log
                       if row["t"] == clear["t"] and row["car"] == clear["car"]
                       and clear["key"] in row["cleared"]]
            self.assertEqual(len(matches), 1)
        for index, stop in enumerate(sim.stop_log):
            attempts = [row for row in sim.board_log if row["stop"] == index]
            self.assertEqual(sum(row["boarded"] for row in attempts), stop["boarded"])
            self.assertEqual([key for row in attempts for key in row["cleared"]], stop["calls"])
            self.assertEqual(stop["empty"], not stop["boarded"] and not stop["alighted"])
            self.assertIsNotNone(stop["closed"])
        metrics = sim.metrics()
        self.assertEqual(metrics["stops"], len(sim.stop_log))
        self.assertEqual(metrics["completed_stops"], len(sim.stop_log))
        self.assertEqual(metrics["open_stops"], 0)
        self.assertEqual(metrics["empty_stops"], sum(row["empty"] for row in sim.stop_log))
        self.assertEqual(sim.board_log, deepcopy(sim).board_log)

    def test_capacity_burst_full_cars_depart_all_eight_policies_both_timesteps(self):
        for dt in (.5, .25):
            for policy in configurations():
                with self.subTest(dt=dt, policy=policy), patch.object(C, "DT", dt), \
                        patch.object(C, "DRAIN_TIME", 180.), \
                        patch.object(C, "REASSIGN_INTERVAL", 10.):
                    count = 6*C.CAPACITY+1
                    sim = DualBankSim(
                        [Passenger(i, 0., 3, 1) for i in range(count)],
                        Constant(policy), 0.)
                    for car in sim.cars:
                        self.stationary(car, 3, 0)
                    sim.run()
                    self.assertEqual(sim.metrics()["incomplete"], 0)
                    self.assertEqual(sim.metrics()["unserved"], 0)
                    self.assertEqual(len(sim.physical_board), count)
                    self.assertEqual(len(sim.physical_alight), count)
                    self.assertGreater(sum(car.distance for car in sim.cars), 0.)
                    self.assertFalse(sim.calls)
                    self.assertFalse(sim.waiting)
                    self.assertFalse(sim._open_stops)
                    sim.check_conservation()

    def test_full_closing_car_releases_assignment_not_latch_or_queue(self):
        for dt in (.5, .25):
            with self.subTest(dt=dt), patch.object(C, "DT", dt), \
                    patch.object(C, "REASSIGN_INTERVAL", 10.):
                count = 6*C.CAPACITY+1
                sim = DualBankSim(
                    [Passenger(i, 0., 3, 1) for i in range(count)],
                    Constant("pickup_demand_b0"), 0.)
                for car in sim.cars:
                    self.stationary(car, 3, 0)
                sim.step()
                car = sim.cars[0]
                queue_before = [p.id for p in sim.waiting[(3, -1)]]
                remote = sim.observable(1)
                self.assertEqual(car.load, C.CAPACITY)
                car.dwell_left = 0.
                sim.update_car(car)
                self.assertEqual(car.state, "moving")
                self.assertEqual(car.target, 1)
                self.assertIn((3, -1, 0), sim.calls)
                self.assertEqual([p.id for p in sim.waiting[(3, -1)]], queue_before)
                self.assertEqual(sim.observable(1), remote)
                self.assertFalse(any(key[0] == 3 for key in car.assigned))
                self.assertEqual(sim.board_log[-1]["boarded"], 0)
                self.assertTrue(sim.board_log[-1]["extra"])
                self.assertEqual(sim.latch_log[-1]["action"], "set")
                sim.check_conservation()

    def test_previously_stalled_high_lunch_seeds_drain_both_timesteps(self):
        from research.dual_bank.control import FixedControl
        from research.dual_bank.demand import sustained

        for dt in (.5, .25):
            for seed in (8541001, 8541004):
                with self.subTest(dt=dt, seed=seed), patch.object(C, "DT", dt), \
                        patch.object(C, "DRAIN_TIME", 3600.), \
                        patch.object(C, "REASSIGN_INTERVAL", 10.):
                    passengers, end, _ = sustained("high", "lunch_S3", seed)
                    sim = DualBankSim(
                        passengers, FixedControl("pickup_demand_b0"), end).run()
                    self.assertEqual(sim.metrics()["incomplete"], 0)
                    self.assertEqual(sim.metrics()["unserved"], 0)
                    self.assertEqual(len(sim.physical_alight), len(passengers))
                    self.assertFalse(sim.waiting)
                    self.assertFalse(sim.calls)
                    self.assertFalse(sim._open_stops)
                    self.assertTrue(all(c.state != "doors" for c in sim.cars))
                    self.assertTrue(all(p.t_alight <= sim.t for p in sim.pax))
                    sim.check_conservation()

    def test_bypass_closing_threshold_preserves_opposite_latches_both_banks_loaded(self):
        for dt in (.5, .25):
            for load in (13, C.CAPACITY):
                with self.subTest(dt=dt, load=load), patch.object(C, "DT", dt):
                    count = 6*load
                    passengers = [Passenger(i, 0., 3, 1) for i in range(count)]
                    passengers.append(Passenger(count, 0., 3, 10))
                    sim = DualBankSim(passengers, Constant("pickup_demand_b1"), 0.)
                    sim.set_policy("pickup_demand_b1")
                    self.release(sim)
                    # Populate valid own-car riders at the two relevant loads.
                    with patch.object(C, "CAPACITY", load):
                        for car in sim.cars:
                            self.stationary(car, 3, -1)
                            sim.board(car, 3, extra=False)
                    self.assertTrue(all(c.load == load for c in sim.cars))
                    car = sim.cars[0]
                    self.assertTrue(sim.bypass_full(car))
                    sim.stop_here(car, forced=True)
                    self.assertEqual(car.state, "doors")
                    opposite = (3, 1, 0)
                    sim.unassign(opposite)
                    sim.calls[opposite].car = car.id
                    car.assigned.add(opposite)
                    remote = sim.observable(1)
                    latch = sim.calls[opposite]
                    waiting = [p.id for p in sim.waiting[(3, 1)]]
                    before_clears = len(sim.latch_log)
                    car.dwell_left = 0.
                    sim.update_car(car)
                    self.assertEqual(car.state, "moving")
                    self.assertEqual(car.target, 1)
                    self.assertIs(sim.calls[opposite], latch)
                    self.assertIn((3, 1, 1), sim.calls)
                    self.assertEqual([p.id for p in sim.waiting[(3, 1)]], waiting)
                    self.assertEqual(sim.observable(1), remote)
                    self.assertEqual(len(sim.latch_log), before_clears)
                    self.assertFalse(any(k[0] == 3 for k in car.assigned))
                    sim.check_conservation()
                    sim.run()
                    self.assertEqual(sim.metrics()["incomplete"], 0)
                    self.assertEqual(sim.metrics()["unserved"], 0)
                    sim.check_conservation()

    def test_input_and_configuration_validation(self):
        bad_inputs = [
            [Passenger(0, 0., 3, 3)],
            [Passenger(0, -1., 3, 5)],
            [Passenger(0, 0., 3, 25)],
            [Passenger(0, 0., 3, 5), Passenger(0, 1., 4, 6)],
        ]
        for passengers in bad_inputs:
            with self.assertRaises(ValueError):
                DualBankSim(passengers, Constant(), 1.)
        sim = DualBankSim([], Constant(), 0.)
        with self.assertRaises(ValueError):
            sim.set_policy("soft_zoning")
        with self.assertRaises(ValueError):
            sim.set_policy("pickup_uniform_b0", bank=2)
        self.assertIsNone(sim.metrics()["awt"])
        self.assertEqual(sim.run().metrics()["incomplete"], 0)


if __name__ == "__main__":
    unittest.main()
