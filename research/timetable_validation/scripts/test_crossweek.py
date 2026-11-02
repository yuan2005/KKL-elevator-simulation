import unittest
from report_crossweek import paired_index,contrast,check_demand_ledger


class CrossweekTests(unittest.TestCase):
    def setUp(self):
        self.p=dict(dates=['a','b'],variants=['mixed'],seeds=[1,2,3],steps=[.25,.125],controllers=['C1','D10'])
        self.rows=[dict(date=d,variant=v,seed=s,dt=dt,controller=c,input_sha256=f'{d}-{v}-{s}',
            n=10 if d=='a' else 1000,awt=10+(s+(0 if d=='a' else 2) if c=='D10' else 0))
            for d in self.p['dates'] for v in self.p['variants'] for s in self.p['seeds']
            for dt in self.p['steps'] for c in self.p['controllers']]

    def test_incomplete_duplicate_and_unpaired_cells_rejected(self):
        for rows in [self.rows[:-1],self.rows+[self.rows[0]],
                     [dict(self.rows[0],input_sha256='different')]+self.rows[1:]]:
            with self.subTest(size=len(rows)):
                with self.assertRaises(AssertionError):paired_index(rows,self.p)

    def test_interval_uses_seed_means_and_equal_dates_not_passenger_weights(self):
        index=paired_index(self.rows,self.p)
        r=contrast(index,self.p['dates'],self.p['variants'],self.p['seeds'],.25,'D10','C1','awt')
        self.assertEqual(r['seed_means'],[2,3,4])
        self.assertEqual(r['mean'],3)
        self.assertEqual(r['n_seeds'],3)
        self.assertAlmostEqual(r['low'],3-4.302652729911275/(3**.5),places=7)
        self.assertAlmostEqual(r['high'],3+4.302652729911275/(3**.5),places=7)

    def test_same_floor_movements_are_conserved_without_lift_requests(self):
        r=dict(n=6,attendance_slots=6,transfer_links=2,
            ledger=dict(class_intent=10,class_lift=6,class_stairs=3,class_same_floor=1))
        check_demand_ledger(r)
        with self.assertRaises(AssertionError):check_demand_ledger(dict(r,n=7))
        with self.assertRaises(AssertionError):
            check_demand_ledger(dict(r,ledger=dict(r['ledger'],class_same_floor=0)))


if __name__=='__main__':unittest.main()
