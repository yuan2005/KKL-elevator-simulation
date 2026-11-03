import unittest
from report_schedule_revision import assemble,change


class ScheduleRevisionTests(unittest.TestCase):
    def row(self,date,value):
        return dict(date=date,variant='low',seed=1,dt=.25,controller='C1',awt=value)

    def test_affected_replacement_keeps_unaffected_rows_and_rejects_missing_cells(self):
        old=[self.row('day1',10),self.row('day2',20)]
        revised=assemble(old,[self.row('day1',12)],['day1'])
        self.assertEqual([r['awt'] for r in revised],[12,20])
        self.assertEqual(old[0]['awt'],10)
        with self.assertRaises(AssertionError):assemble(old,[],['day1'])
        with self.assertRaises(AssertionError):assemble(old,[self.row('day1',12)]*2,['day1'])

    def test_change_uses_paired_seed_differences_not_difference_of_intervals(self):
        r=change([-10,0,10],[-9,1,11])
        self.assertEqual(r['seed_means'],[1,1,1])
        self.assertEqual((r['mean'],r['low'],r['high']),(1,1,1))


if __name__=='__main__':unittest.main()
