import unittest
from run_headcount_sensitivity import with_missing_fraction
from timetable_demand import sessions_for_day, select_sessions
from test_timetable_demand import event


class HeadcountSensitivityTests(unittest.TestCase):
    def test_only_missing_count_changes_and_reference_is_identical(self):
        known=event(1,'09:00','10:50')
        missing=event(2,'11:00','12:50',number=2)
        missing['business_headcount_snapshot']=None
        sessions=sessions_for_day([known,missing],'2026-10-26')
        selected,_=select_sessions(sessions,{'KK101':dict(floor='1',capacity=100)},'capacity_assumption')
        self.assertEqual(selected,with_missing_fraction(selected,.6))
        for fraction,n in [(.4,40),(.8,80)]:
            updated=with_missing_fraction(selected,fraction)
            self.assertEqual(updated[0],selected[0])
            self.assertEqual(updated[1]['headcount'],n)
            self.assertEqual(updated[1]['headcount_basis'],f'assumed_{n}pct_room_capacity')
        self.assertEqual(selected[1]['headcount'],60)

    def test_excluded_sessions_do_not_reappear(self):
        a=event(1,'09:00','10:50'); b=event(2,'09:00','10:50',number=2)
        sessions=sessions_for_day([a,b],'2026-10-26')
        selected,excluded=select_sessions(sessions,{'KK101':dict(floor='1',capacity=100)},'capacity_assumption')
        self.assertEqual(len(excluded),2)
        self.assertEqual(with_missing_fraction(selected,.8),[])
        with self.assertRaises(ValueError):
            with_missing_fraction([],1.1)


if __name__ == '__main__':
    unittest.main()
