from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config as C
from building import ENTRANCE_IDX, HEIGHT, IDX, flight_time
from research.dual_bank.control import FixedControl
from research.dual_bank.engine import DualBankSim
from traffic import Passenger
from research.dual_bank.physics import PhysicalCar, advance


class EngineTests(unittest.TestCase):
    def test_exact_rest_to_rest_duration_at_three_steps(self):
        for distance in (1., 3.6, 6.25, 27., 68.4):
            for step in (.25, .125, .0625):
                position, velocity, elapsed = 0., 0., 0.
                for _ in range(10000):
                    moved, velocity, arrived, used = advance(distance-position, velocity, step)
                    position += moved
                    elapsed += used
                    self.assertLessEqual(velocity, C.RATED_SPEED+1e-9)
                    if arrived:
                        break
                self.assertTrue(arrived)
                self.assertAlmostEqual(position, distance, places=7)
                self.assertAlmostEqual(elapsed, flight_time(distance), places=7)

    def test_moving_profiles_preserve_stopping_feasibility(self):
        for speed in (.4, 1.2, 2.4):
            for extra in (.001, .3, 10.):
                distance = speed*speed/(2*C.ACCEL)+extra
                moved, after, arrived, _ = advance(distance, speed, .25)
                self.assertGreaterEqual(distance-moved+1e-8, after*after/(2*C.ACCEL))
                if arrived:
                    self.assertEqual(after, 0.)

    def test_impossible_stop_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "stopping distance"):
            advance(.1, 2., .25)

    def test_bidirectional_stop_guard(self):
        for direction in (-1, 1):
            car = PhysicalCar(0, IDX["8"])
            car.state, car.dir, car.v = "moving", direction, 2.
            target = IDX["8"]+direction
            car.x = HEIGHT[target]-direction*1.
            self.assertFalse(car.can_stop_at(target))
            car.x = HEIGHT[target]-direction*3.
            self.assertTrue(car.can_stop_at(target))

    def test_complete_service_and_physical_conservation(self):
        for step in (.25, .125):
            with patch.object(C, "DT", step), patch.object(C, "DRAIN_TIME", 1800.):
                passengers = [
                    Passenger(j, float(j%5), ENTRANCE_IDX[j%2], IDX[str(3+j%10)])
                    for j in range(40)
                ]
                passengers.sort(key=lambda p: p.t)
                sim = DualBankSim(passengers, FixedControl("pickup_uniform_b0"), 30.).run()
                self.assertTrue(all(p.t_board is not None and p.t_alight is not None for p in sim.pax))
                self.assertGreaterEqual(sim.t, max(p.t_alight for p in sim.pax))
                self.assertEqual(len(sim.physical_board), len(passengers))
                self.assertEqual(len(sim.physical_alight), len(passengers))
                self.assertGreater(sim.conservation_checks, 0)
                for row in sim.service_records():
                    self.assertGreaterEqual(row["board"], row["arrival"])
                    self.assertGreaterEqual(row["alight"], row["board"])


if __name__ == "__main__":
    unittest.main()
