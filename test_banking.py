import unittest
from copy import deepcopy
from dataclasses import replace

import config as C
from banking import DualBankSim
from building import HEIGHT, IDX
from controllers import Static
from sim import Sim
from strategies import make_combos
from traffic import Passenger, SCENARIOS, generate_scenario
from compare_banks import analyze


class DualBankTests(unittest.TestCase):
    def make_sim(self, passengers=(), combo="C1", broken=()):
        return DualBankSim(list(passengers), Static(make_combos()[combo]), 15,
                           broken_cars=broken)

    def test_arrival_has_one_physical_queue_and_two_local_calls(self):
        p = Passenger(0, 0, IDX["G"], IDX["10"])
        sim = self.make_sim([p])
        sim.set_combo(sim.controller.combo)
        sim._register(p)
        self.assertEqual(sum(map(len, sim.waiting.values())), 1)
        self.assertEqual(len(sim.calls), 2)
        for key, call in sim.calls.items():
            self.assertEqual(sim.bank_of[call.car], key[2][0])
            self.assertEqual(call.queue, [])

    def test_pickup_does_not_cancel_other_bank_and_empty_stop_costs_time(self):
        p = Passenger(0, 0, IDX["G"], IDX["10"])
        sim = self.make_sim([p], combo="C0")
        sim.set_combo(sim.controller.combo)
        sim._register(p)
        first = sim.cars[1]
        first.floor, first.x, first.dir = p.o, HEIGHT[p.o], 1
        sim.stop_here(first, forced=True)
        self.assertEqual(len(first.passengers), 1)
        self.assertEqual(len(sim.calls), 1)
        self.assertEqual(next(iter(sim.calls))[2][0], 1)
        other = sim.cars[4]
        other.floor, other.x, other.dir = p.o, HEIGHT[p.o], 1
        sim.stop_here(other, forced=True)
        self.assertEqual(other.load, 0)
        self.assertFalse(sim.calls)
        self.assertEqual(other.state, "doors")
        self.assertEqual(other.dwell_left, C.T_DOOR_OPEN+C.T_DOOR_CLOSE)

    def test_dispatch_and_parking_receive_only_local_cars(self):
        sim = self.make_sim()
        sim.set_combo(sim.controller.combo)
        for bank in (0, 1):
            view = sim.local_view(bank)
            self.assertEqual(len(view.cars), 3)
            self.assertTrue(all(sim.bank_of[c.id] == bank for c in view.cars))
            self.assertIsNot(sim.bank_combos[bank], sim.combo)
        p = Passenger(0, 0, IDX["5"], IDX["10"])
        sim._register(p)
        for bank in (0, 1):
            before = {k: c.car for k, c in sim.calls.items() if k[2][0] == bank}
            for car in sim.cars:
                if sim.bank_of[car.id] != bank:
                    car.x = HEIGHT[p.o]
            for key, call in sim.calls.items():
                if key[2][0] == bank:
                    sim.assign(call)
            self.assertEqual(before, {k: c.car for k, c in sim.calls.items() if k[2][0] == bank})

    def test_each_bank_has_its_own_sectors(self):
        sim = self.make_sim(combo="C3")
        sim.set_combo(sim.controller.combo)
        for bank in (0, 1):
            sectors = [frozenset(c.down_sector) for c in sim.local_view(bank).cars]
            self.assertEqual(len(set(sectors)), 3)
        sim.set_combo(make_combos()["C2"])
        for bank in (0, 1):
            self.assertEqual({c.up_sector for c in sim.local_view(bank).cars}, {0, 1})

    def test_remote_parking_changes_do_not_change_local_target(self):
        sim = self.make_sim()
        sim.set_combo(sim.controller.combo)
        car = sim.cars[0]
        target = sim.parking_target(car)
        for remote in sim.local_view(1).cars:
            remote.parking = target
            remote.floor = target
        self.assertEqual(sim.parking_target(car), target)

    def test_both_banks_at_same_floor_cannot_board_same_person(self):
        ps = [Passenger(i, 0, IDX["G"], IDX["10"]) for i in range(3)]
        sim = self.make_sim(ps, combo="C0")
        sim.set_combo(sim.controller.combo)
        for p in ps:
            sim._register(p)
        for car in (sim.cars[1], sim.cars[4]):
            car.floor, car.x, car.dir = ps[0].o, HEIGHT[ps[0].o], 1
            sim.stop_here(car, forced=True)
        onboard = [p for c in sim.cars for p in c.passengers]
        self.assertEqual(len(onboard), 3)
        self.assertEqual(len({p.id for p in onboard}), 3)
        self.assertFalse(sim.waiting)

    def test_sector_boarding_only_takes_eligible_passengers(self):
        sim = self.make_sim(combo="C2")
        sim.set_combo(sim.controller.combo)
        combo = sim.bank_combos[0]
        destinations = {}
        for floor, tag in combo._up_map.items():
            if floor > IDX["G"]:
                destinations.setdefault(tag, floor)
        ps = [Passenger(i, 0, IDX["G"], destination)
              for i, destination in enumerate(destinations.values())]
        for p in ps:
            sim._register(p)
        car = sim.cars[1]
        car.floor, car.x, car.dir = IDX["G"], HEIGHT[IDX["G"]], 1
        sim.stop_here(car, forced=True)
        self.assertTrue(car.passengers)
        self.assertTrue(all(combo.tag(sim.local_view(0), p) == car.up_sector
                            for p in car.passengers))
        self.assertEqual(len(car.passengers)+sum(map(len, sim.waiting.values())), len(ps))

    def test_switch_preserves_remote_ghost_call(self):
        p = Passenger(0, 0, IDX["G"], IDX["10"])
        sim = self.make_sim([p], combo="C0")
        sim.set_combo(sim.controller.combo)
        sim._register(p)
        car = sim.cars[1]
        car.floor, car.x, car.dir = p.o, HEIGHT[p.o], 1
        sim.stop_here(car, forced=True)
        before = set(sim.calls)
        sim.set_combo(make_combos()["C2"])
        self.assertEqual(set(sim.calls), before)
        self.assertEqual(car.load, 1)
        self.assertFalse(sim.waiting)

    def test_full_bank_represses_without_duplicate_passengers(self):
        ps = [Passenger(i, 0, IDX["G"], IDX["10"]) for i in range(C.CAPACITY+2)]
        sim = self.make_sim(ps, combo="C0")
        sim.set_combo(sim.controller.combo)
        for p in ps:
            sim._register(p)
        car = sim.cars[1]
        car.floor, car.x, car.dir = ps[0].o, HEIGHT[ps[0].o], 1
        sim.stop_here(car, forced=True)
        self.assertEqual(car.load, C.CAPACITY)
        self.assertEqual(sum(map(len, sim.waiting.values())), 2)
        self.assertEqual({key[2][0] for key in sim.calls}, {0, 1})
        self.assertEqual(len({p.id for p in car.passengers}), car.load)

    def test_all_combos_finish_without_cross_bank_assignments(self):
        scenario = replace(SCENARIOS["S3"], duration=90, lam5=130)
        original, _ = generate_scenario(scenario, 91401)
        for combo in make_combos(forecast=[]):
            with self.subTest(combo=combo):
                ps = deepcopy(original)
                sim = DualBankSim(ps, Static(make_combos(forecast=[])[combo]), 90)
                while sim.t < 90+C.DRAIN_TIME:
                    sim.step()
                    for key, call in sim.calls.items():
                        if call.car is not None:
                            self.assertEqual(sim.bank_of[call.car], key[2][0])
                    if sim.t > 90 and all(p.t_alight is not None and p.t_alight <= sim.t for p in ps) and not sim.calls:
                        break
                self.assertTrue(all(p.t_alight is not None for p in ps))
                self.assertEqual(len({p.id for c in sim.cars for p in c.passengers}), 0)
                self.assertFalse(sim.waiting)
                self.assertFalse(sim.calls)
                self.assertTrue(all(p.t <= p.t_board < p.t_alight for p in ps))

    def test_outage_and_dynamic_switching(self):
        combos = make_combos()

        class Switch:
            interval = 10

            def update(self, sim, t):
                return combos[("C2", "C7", "C3", "C4")[int(t//10) % 4]]

        ps, _ = generate_scenario(replace(SCENARIOS["S3"], duration=80), 91402)
        sim = DualBankSim(ps, Switch(), 80, broken_cars=(0,)).run()
        self.assertTrue(all(p.t_alight is not None for p in ps))
        self.assertFalse(sim.cars[0].active)
        self.assertTrue(all(p.car != 0 for p in ps))
        self.assertFalse(sim.waiting)
        self.assertFalse(sim.calls)

    def test_legacy_simulator_is_still_available(self):
        p = Passenger(0, 0, IDX["G"], IDX["10"])
        sim = Sim([p], Static(make_combos()["C1"]), 10).run()
        self.assertIsNotNone(p.t_alight)
        self.assertTrue(all(len(key) == 3 for key in sim.calls))

    def test_run_finishes_scheduled_alighting_and_empty_stop_sessions(self):
        ps, _ = generate_scenario(replace(SCENARIOS["S1"], duration=600), 914101)
        sim = DualBankSim(ps, Static(make_combos()["C0"]), 600).run()
        self.assertTrue(all(p.t_alight is not None and p.t_alight <= sim.t for p in ps))
        self.assertFalse(sim._sessions)
        self.assertGreater(sim.completed_stops, 0)
        self.assertGreater(sim.empty_stops, 0)

    def test_reject_unsupported_car_count(self):
        with self.assertRaises(ValueError):
            DualBankSim([], Static(make_combos()["C1"]), 0, n_cars=5)

    def test_pilot_rejects_mismatched_or_missing_paired_inputs(self):
        base = dict(scenario="S3", scale=1, combo="C1", dt=.25, seed=1,
                    model="coordinated", awt=10, input_hash="same", empty_stops=None)
        other = dict(base, model="independent", awt=16, empty_stops=5)
        summary = analyze([base, other])
        self.assertEqual(summary[0]["delta_awt"], 6)
        self.assertEqual(summary[0]["n_seeds"], 1)
        with self.assertRaisesRegex(ValueError, "inputs differ"):
            analyze([base, dict(other, input_hash="different")])
        with self.assertRaisesRegex(ValueError, "Missing paired model"):
            analyze([base])


if __name__ == "__main__":
    unittest.main()
