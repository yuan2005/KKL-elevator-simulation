from dataclasses import replace
import unittest
from unittest.mock import patch

import experiments as E
from traffic import SCENARIOS, background_rate, generate_day, generate_scenario, load_timetable


class LunchDemandTests(unittest.TestCase):
    def test_s4_is_exactly_historical_2_5(self):
        self.assertEqual(E.SCENARIOS["S4"].lam5, 137.5)
        current, stairs = generate_scenario(E.SCENARIOS["S4"], 914201)
        expected, expected_stairs = generate_scenario(replace(SCENARIOS["S4"], lam5=137.5), 914201)
        self.assertEqual(current, expected)
        self.assertEqual(stairs, expected_stairs)

    def test_background_multiplier_only_applies_in_lunch_window(self):
        for seconds, expected in ((4*3600-1, 12), (4*3600, 112.5),
                                  (6*3600-1, 112.5), (6*3600, 12)):
            self.assertEqual(background_rate(seconds, lunch_multiplier=2.5)[0], expected)
        self.assertEqual(background_rate(4*3600)[0], 45)

    def test_formal_day_uses_both_multipliers_once(self):
        tt = load_timetable(E.TIMETABLE)
        actual = E.generate_day(tt, 914201, attendance=.8)
        expected = generate_day(tt, 914201, attendance=.8, class_multiplier=2.5, lunch_multiplier=2.5)
        self.assertEqual(actual, expected)
        self.assertEqual([p.id for p in actual], list(range(len(actual))))
        self.assertEqual([p.t for p in actual], sorted(p.t for p in actual))

    def test_class_multiplier_matches_scaled_attendance(self):
        tt = load_timetable(E.TIMETABLE)
        self.assertEqual(
            generate_day(tt, 914201, attendance=.8, class_multiplier=2.5),
            generate_day(tt, 914201, attendance=2.0))

    def test_classes_and_lunch_background_are_generated_together(self):
        tt = [dict(size=100, floor=8, start=4.5*3600, end=5.5*3600)]
        with patch("traffic.background_rate", return_value=(0, (1, 0, 0))):
            classes = generate_day(tt, 914201, thru_share=0, class_multiplier=2.5)
        background = generate_day([], 914201, thru_share=0, lunch_multiplier=2.5)
        combined = E.generate_day(tt, 914201, thru_share=0)
        self.assertGreater(sum(4*3600 <= p.t < 6*3600 for p in classes), 0)
        self.assertGreater(sum(4*3600 <= p.t < 6*3600 for p in background), 0)
        self.assertGreater(sum(4*3600 <= p.t < 6*3600 for p in combined), len(classes))

    def test_forecast_uses_same_new_daily_generator(self):
        with patch.object(E, "TreePolicy"), patch.object(E, "generate_day", return_value=[]) as generate:
            E.build_controller("课表预测+地图", {}, [])
        self.assertEqual(generate.call_count, E.FORECAST_SAMPLES)

    def test_legacy_defaults_and_invalid_multiplier(self):
        tt = load_timetable(E.TIMETABLE)
        self.assertEqual(generate_day(tt, 123), generate_day(
            tt, 123, class_multiplier=1, lunch_multiplier=1))
        for value in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                generate_day([], 123, class_multiplier=value)
            with self.assertRaises(ValueError):
                generate_day([], 123, lunch_multiplier=value)


if __name__ == "__main__":
    unittest.main()
