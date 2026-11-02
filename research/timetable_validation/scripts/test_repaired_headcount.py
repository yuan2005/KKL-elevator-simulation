import copy,unittest
from linked_demand import generate_linked
from run_headcount_sensitivity import with_missing_fraction
from run_repaired_headcount import verify_isolation


class RepairedHeadcountTests(unittest.TestCase):
    def test_known_attendance_and_independent_layers_stay_fixed(self):
        selected=[dict(session_id='known',start_min=60,end_min=110,floor='1',headcount=60,
            headcount_snapshot=60,capacity=100,headcount_basis='known'),
            dict(session_id='missing',start_min=120,end_min=170,floor='2',headcount=60,
            headcount_snapshot=None,capacity=100,headcount_basis='assumed_60pct_room_capacity',headcount_exceeds_capacity=False)]
        reference=generate_linked(selected,71,2700,11100)
        for fraction in [.4,.8]:
            adjusted=with_missing_fraction(selected,fraction)
            demand=generate_linked(adjusted,71,2700,11100)
            self.assertTrue(all(verify_isolation(selected,adjusted,reference,demand).values()))
            broken=copy.deepcopy(demand);broken['audit']['session_attendance']['known']+=1
            with self.assertRaises(AssertionError):verify_isolation(selected,adjusted,reference,broken)
        self.assertEqual(selected[1]['headcount'],60)


if __name__=='__main__':unittest.main()
