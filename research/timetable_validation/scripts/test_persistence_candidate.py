import random
import unittest
from adaptive_policy import Gate
from persistence_candidate import PersistenceGate


class PersistenceTests(unittest.TestCase):
    def test_two_confirmation_control_matches_original(self):
        old=Gate(.10);new=PersistenceGate(.10,2);rng=random.Random(173)
        # Include sustained regimes to exercise switches and dwell, plus mixed conditions.
        values=[(20,.8,0)]*15+[(20,.5,.2)]*15
        values += [(rng.choice([0,9,10,30]),rng.choice([.64,.65,.69,.70,.8]),rng.choice([.0,.10,.15,.151])) for _ in range(3000)]
        for i,(n,entry,high) in enumerate(values):
            self.assertEqual(old.update(i*60,n,entry,high),new.update(i*60,n,entry,high))
            for field in ['current','pending','count','last_switch']:
                self.assertEqual(getattr(old,field),getattr(new,field))

    def test_three_confirmations_both_directions_and_dwell(self):
        gate=PersistenceGate(.10,3)
        self.assertEqual(gate.update(600,20,.8,0),'C1')
        self.assertEqual(gate.update(660,20,.8,0),'C1')
        self.assertEqual(gate.update(720,20,.8,0),'C7')
        for t in [780,840,900,960,1020,1080,1140,1200,1260]:
            self.assertEqual(gate.update(t,20,.5,.2),'C7')
        self.assertEqual(gate.update(1320,20,.5,.2),'C1')

    def test_interrupted_evidence_resets_count(self):
        gate=PersistenceGate(.10,3)
        for t,args in [(600,(20,.8,0)),(660,(20,.8,0)),(720,(20,.5,0)),(780,(20,.8,0)),(840,(20,.8,0))]:
            self.assertEqual(gate.update(t,*args),'C1')
        self.assertEqual(gate.update(900,20,.8,0),'C7')


if __name__=='__main__':unittest.main()
