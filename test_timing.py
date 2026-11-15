import unittest

import config as C
from banking import DualBankSim
from building import HEIGHT, IDX, advance_motion, flight_time
from controllers import Static
from strategies import make_combos
from traffic import Passenger


class TimingTests(unittest.TestCase):
    def test_tiny_positive_braking_distance_has_nonzero_duration(self):
        distance = 3e-11
        speed = (2*C.ACCEL*distance)**.5
        moved, velocity, arrived, elapsed = advance_motion(distance, speed, .001)
        self.assertTrue(arrived)
        self.assertEqual(moved, distance)
        self.assertEqual(velocity, 0)
        self.assertAlmostEqual(elapsed, speed/C.ACCEL, places=14)

    def test_motion_respects_limits_and_conserves_distance(self):
        for dt in (.25, .125, .0625):
            for speed in (0., .3, C.RATED_SPEED):
                for extra in (.01, .5, 5., 30.):
                    with self.subTest(dt=dt, speed=speed, extra=extra):
                        remaining = speed*speed/(2*C.ACCEL)+extra
                        distance, velocity, total = remaining, speed, 0.
                        for _ in range(10000):
                            moved, new_velocity, arrived, elapsed = advance_motion(remaining, velocity, dt)
                            self.assertLessEqual(abs(new_velocity-velocity), C.ACCEL*elapsed+1e-8)
                            self.assertLessEqual(new_velocity, C.RATED_SPEED+1e-8)
                            self.assertGreaterEqual(moved, 0)
                            self.assertLessEqual(moved, remaining+1e-8)
                            total += moved
                            remaining -= moved
                            velocity = new_velocity
                            if arrived:
                                break
                        else:
                            self.fail("Motion did not reach target")
                        self.assertAlmostEqual(total, distance, places=8)
                        self.assertEqual(velocity, 0)

    def test_motion_rejects_impossible_stop_and_invalid_inputs(self):
        for distance, speed, dt in ((.1, 2.5, .25), (-1, 0, .25),
                                    (1, 0, 0), (1, 0, float("nan")),
                                    (1, C.RATED_SPEED+1, .25)):
            with self.subTest(distance=distance, speed=speed, dt=dt):
                with self.assertRaises(ValueError):
                    advance_motion(distance, speed, dt)

    def test_arrival_timestamp_matches_rest_to_rest_motion(self):
        class Probe(DualBankSim):
            def arrive(self, car):
                self.arrival = self.t
                car.state = "idle"

        old_dt = C.DT
        self.addCleanup(setattr, C, "DT", old_dt)
        for dt in (.25, .125, .0625):
            C.DT = dt
            for floors in (1, 2, 5, 10, 16):
                with self.subTest(dt=dt, floors=floors):
                    sim = Probe([], Static(make_combos()["C0"]), 0)
                    car = sim.cars[0]
                    car.x, car.floor, car.dir, car.target = 0, 0, 1, floors
                    car.state = "moving"
                    while car.state == "moving":
                        sim.move(car)
                        sim.t += dt
                    self.assertAlmostEqual(sim.arrival, flight_time(floors*C.FLOOR_HEIGHT), places=8)

    def test_last_boarding_is_followed_by_full_closing_time(self):
        ps = [Passenger(0, 0, IDX["G"], IDX["10"]),
              Passenger(1, 6, IDX["G"], IDX["10"])]
        sim = DualBankSim(ps, Static(make_combos()["C0"]), 10)
        sim.set_combo(sim.controller.combo)
        sim._register(ps[0])
        car = sim.cars[1]
        car.floor, car.x, car.dir = IDX["G"], HEIGHT[IDX["G"]], 1
        sim.stop_here(car, forced=True)
        sim.t = C.DT
        registered = False
        while car.state == "doors":
            if not registered and sim.t >= ps[1].t:
                sim._register(ps[1])
                registered = True
            sim.update_car(car)
            self.assertLess(sim.t, 30)
            if car.state != "doors":
                break
            sim.t += C.DT
        last_boarding = max(p.t_board for p in ps if p.t_board is not None)
        self.assertGreaterEqual(sim.t-last_boarding, C.T_DOOR_CLOSE-1e-8)
        self.assertAlmostEqual(ps[1].t_board, ps[1].t+C.T_DOOR_OPEN+C.T_PASSENGER)
        self.assertEqual(sim.reopenings, 1)

    def test_new_passenger_during_open_service_does_not_wait_until_closing_ends(self):
        ps = [Passenger(0, 0, IDX["G"], IDX["10"]),
              Passenger(1, 2.5, IDX["G"], IDX["10"])]
        sim = DualBankSim(ps, Static(make_combos()["C0"]), 10)
        sim.set_combo(sim.controller.combo)
        sim._register(ps[0])
        car = sim.cars[1]
        car.floor, car.x, car.dir = IDX["G"], HEIGHT[IDX["G"]], 1
        sim.stop_here(car, forced=True)
        sim.t = C.DT
        while car.state == "doors":
            if sim.t == 2.5:
                sim._register(ps[1])
            sim.update_car(car)
            sim.t += C.DT
        self.assertIsNotNone(ps[1].t_board)
        self.assertLessEqual(ps[1].t_board, ps[0].t_board+C.T_PASSENGER+C.DT+1e-8)

    def test_stopping_feasibility_has_no_one_step_braking_slack(self):
        sim = DualBankSim([], Static(make_combos()["C0"]), 0)
        car = sim.cars[0]
        car.state, car.dir, car.v = "moving", 1, C.RATED_SPEED
        car.x = HEIGHT[IDX["G"]]-C.RATED_SPEED**2/(2*C.ACCEL)+.1
        self.assertFalse(car.can_stop_at(IDX["G"]))


if __name__ == "__main__":
    unittest.main()
