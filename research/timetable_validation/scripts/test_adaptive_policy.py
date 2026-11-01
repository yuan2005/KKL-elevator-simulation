import unittest
from types import SimpleNamespace
from adaptive_policy import Gate,PastDemandController

class Tests(unittest.TestCase):
    def test_dwell_and_hysteresis(self):
        g=Gate(.25)
        self.assertEqual(g.update(300,20,.8,.1),'C1')
        self.assertEqual(g.update(360,20,.8,.1),'C1')
        self.assertEqual(g.update(600,20,.8,.1),'C7')
        self.assertEqual(g.update(660,20,.68,.28),'C7')
        self.assertEqual(g.update(720,20,.6,.4),'C7')
        self.assertEqual(g.update(1200,20,.6,.4),'C1')
    def test_no_future_or_current_boundary(self):
        idx={'G':2,'LG2':0,'4':6};combos={'C1':object(),'C7':object()}
        trips=[SimpleNamespace(t=100.,o=2,d=3),SimpleNamespace(t=300.,o=7,d=2),SimpleNamespace(t=301.,o=7,d=2)]
        c=PastDemandController(combos,trips,idx,.25);sim=SimpleNamespace(gen_end=1000)
        c.update(sim,300)
        self.assertEqual(c.log[-1]['n'],1)
        self.assertEqual(c.log[-1]['high_endpoint'],0)
        c.update(sim,1000);self.assertEqual(len(c.log),1)
    def test_sparse_reverts_without_chatter(self):
        g=Gate(.1);g.update(300,20,.8,0);g.update(600,20,.8,0)
        self.assertEqual(g.current,'C7')
        g.update(1200,0,0,0);self.assertEqual(g.current,'C7')
        g.update(1260,0,0,0);self.assertEqual(g.current,'C1')
if __name__=='__main__':unittest.main()
