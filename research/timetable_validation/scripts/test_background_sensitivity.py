import unittest
from linked_demand import generate_linked
from run_background_sensitivity import layer_fingerprint


class BackgroundIsolationTests(unittest.TestCase):
    def test_rate_change_keeps_other_layers_and_cohorts_fixed(self):
        sessions=[dict(session_id='A',start_min=60,end_min=110,floor='1',headcount=60),
                  dict(session_id='B',start_min=120,end_min=180,floor='2',headcount=60)]
        demands=[generate_linked(sessions,71,2700,11400,background_rate=rate) for rate in [6,12,24]]
        for layer in ['class','through']:
            self.assertEqual(len({layer_fingerprint(d,layer) for d in demands}),1)
        self.assertEqual(len({layer_fingerprint(d,'background') for d in demands}),3)
        for d in demands:
            self.assertEqual(d['audit']['session_attendance'],demands[0]['audit']['session_attendance'])
            ledger=d['audit']['ledger']
            self.assertEqual(ledger['background_intent'],sum(ledger.get('background_'+mode,0) for mode in ['lift','stairs','same_floor']))


if __name__=='__main__':unittest.main()
