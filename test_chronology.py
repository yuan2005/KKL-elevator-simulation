from dataclasses import replace
import unittest

import config as C
from banking import DualBankSim
from building import HEIGHT, IDX
from controllers import Static
from strategies import make_combos
from traffic import Passenger, generate_scenario
from experiments import SCENARIOS


class ChronologyTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, C, "DT", C.DT)

    def approaching_cars(self, arrival=0.):
        passenger = Passenger(0, arrival, IDX["G"], IDX["10"])
        sim = DualBankSim([passenger], Static(make_combos()["C0"]), 1,
                          broken_cars=(1, 2, 4, 5))
        sim.set_combo(sim.controller.combo)
        for index, distance in ((0, .01), (3, .0025)):
            car = sim.cars[index]
            car.floor, car.x = IDX["LG1"], HEIGHT[IDX["G"]]-distance
            car.v, car.dir, car.target, car.state = 0., 1, IDX["G"], "moving"
        return sim, passenger

    def test_earlier_arrival_wins_regardless_of_car_index(self):
        for dt in (.25, .125, .0625):
            with self.subTest(dt=dt):
                C.DT = dt
                sim, passenger = self.approaching_cars()
                while passenger.t_board is None:
                    sim.step()
                    self.assertLess(sim.t, 1)
                self.assertEqual(passenger.car, 3)
                self.assertAlmostEqual(passenger.t_board, .1+C.T_DOOR_OPEN+C.T_PASSENGER)

    def test_passenger_arrival_inside_a_step_precedes_later_car_arrival(self):
        C.DT = .25
        sim, passenger = self.approaching_cars(.05)
        while passenger.t_board is None:
            sim.step()
            self.assertLess(sim.t, 1)
        self.assertEqual(passenger.car, 3)
        self.assertAlmostEqual(passenger.t_board, 3.3)

    def test_zero_duration_arrival_is_settled_before_advancing_time(self):
        sim, passenger = self.approaching_cars()
        sim.cars[3].x = HEIGHT[IDX["G"]]
        for _ in range(3):
            sim.step()
            if passenger.t_board is not None:
                break
        self.assertEqual(passenger.car, 3)
        self.assertAlmostEqual(passenger.t_board, C.T_DOOR_OPEN+C.T_PASSENGER)

    def test_door_deadline_is_not_rounded_to_the_grid(self):
        C.DT = .25
        passenger = Passenger(0, 0., IDX["G"], IDX["10"])
        sim = DualBankSim([passenger], Static(make_combos()["C0"]), 10)
        while passenger.t_board is None:
            sim.step()
        car = sim.cars[passenger.car]
        while car.state == "doors":
            before = sim.t
            sim.step()
            if car.state == "moving":
                self.assertAlmostEqual(before, 6.2)
                break
        self.assertEqual(passenger.t_board, 3.2)

    def test_exact_input_trajectory_is_independent_of_integration_cap(self):
        scenario = replace(SCENARIOS["S3"], duration=100)
        reference = None
        for dt in (.25, .125, .0625):
            C.DT = dt
            passengers, _ = generate_scenario(scenario, 914321)
            sim = DualBankSim(passengers, Static(make_combos()["C1"]), scenario.duration).run()
            self.assertFalse(sim.calls)
            records = [(p.car, p.t_board, p.t_alight) for p in passengers]
            self.assertTrue(all(board is not None and alight is not None for _, board, alight in records))
            if reference is not None:
                for actual, expected in zip(records, reference):
                    self.assertEqual(actual[0], expected[0])
                    self.assertAlmostEqual(actual[1], expected[1], places=8)
                    self.assertAlmostEqual(actual[2], expected[2], places=8)
            reference = records


if __name__ == "__main__":
    unittest.main()
