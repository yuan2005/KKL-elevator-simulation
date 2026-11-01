import unittest
from linked_demand import generate_linked

S=[dict(session_id='a',start_min=540,end_min=590,floor='1',headcount=20),
   dict(session_id='b',start_min=600,end_min=650,floor='1',headcount=20),
   dict(session_id='c',start_min=660,end_min=710,floor='2',headcount=20)]
class Tests(unittest.TestCase):
    def test_chain_conservation_and_same_floor(self):
        r=generate_linked(S,1,8*3600,13*3600,1,0)
        a=r['audit'];self.assertGreater(a['transfer_links'],0)
        self.assertEqual(a['ledger']['class_intent'],2*a['attendance_slots']-a['transfer_links'])
        same=[p for p in r['movements'] if p['phase']=='transfer' and p['origin']==p['destination']]
        self.assertTrue(same);self.assertTrue(all(p['mode']=='same_floor' for p in same))
        self.assertEqual(len({x['source_token'] for x in r['links']}),len(r['links']))
        self.assertEqual(len({x['destination_token'] for x in r['links']}),len(r['links']))
    def test_overlap_and_gap_reject(self):
        for start in [585,595,625]:
            r=generate_linked([S[0],dict(S[1],start_min=start)],1,8*3600,13*3600,1,0)
            self.assertEqual(r['audit']['transfer_links'],0)
    def test_controlled_variants(self):
        a=generate_linked(S,2026,8*3600,13*3600,0,0)
        b=generate_linked(S,2026,8*3600,13*3600,.2,.5)
        self.assertEqual(a['audit']['session_attendance'],b['audit']['session_attendance'])
        self.assertEqual(a['audit']['ledger']['background_intent'],b['audit']['ledger']['background_intent'])
        self.assertEqual([p for p in a['trips'] if p['layer']=='through'][0]['t_seconds'],[p for p in b['trips'] if p['layer']=='through'][0]['t_seconds'])
        self.assertTrue(any(p['origin'] in ['4','5','6','7','8','9','10','11','12','13','14','15','16'] for p in b['trips']))
        self.assertTrue(all(0<=p['t_seconds']<5*3600 and p['origin']!=p['destination'] for p in b['trips']))
        self.assertEqual(b,generate_linked(S,2026,8*3600,13*3600,.2,.5))
if __name__=='__main__':unittest.main()
