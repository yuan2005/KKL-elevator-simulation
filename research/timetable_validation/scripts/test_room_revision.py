import unittest
from linked_demand import generate_linked
from run_room_revision import compare_demands


class RoomRevisionTests(unittest.TestCase):
    def test_new_room_preserves_background_and_existing_attendance(self):
        old=[dict(session_id='A',start_min=60,end_min=110,floor='1',headcount=60)]
        new=old+[dict(session_id='B',start_min=120,end_min=180,floor='LG1',headcount=45)]
        a=generate_linked(old,71,2700,11400);b=generate_linked(new,71,2700,11400)
        self.assertTrue(all(compare_demands(a,b).values()))
        self.assertGreater(b['audit']['attendance_slots'],a['audit']['attendance_slots'])
        self.assertGreater(b['audit']['transfer_links'],a['audit']['transfer_links'])
        self.assertNotEqual(a['trips'],b['trips'])


if __name__=='__main__':unittest.main()
