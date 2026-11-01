"""Check audit edge cases and avoid treating repeated dates as independent seeds."""
from types import SimpleNamespace
import unittest
from pathlib import Path
import json
from unittest.mock import patch
import numpy as np
from repair_study_utils import event_violations, save, load
from report_repaired_studies import contrast_rows, paired_index


class RepairedStudyTests(unittest.TestCase):
    def test_scenario_numpy_metrics_saved_without_loss(self):
        with patch.object(Path,'write_text') as write:
            save('row.json',dict(stairs=np.int64(31),rate=np.float64(.25),passed=np.bool_(True)))
            self.assertEqual(json.loads(write.call_args.args[0]),dict(stairs=31,rate=.25,passed=True))

    def test_event_order_and_end_of_simulation(self):
        pax=[SimpleNamespace(t=1,t_board=2,t_alight=3),
             SimpleNamespace(t=1,t_board=0,t_alight=3),
             SimpleNamespace(t=1,t_board=None,t_alight=3),
             SimpleNamespace(t=1,t_board=4,t_alight=3),
             SimpleNamespace(t=1,t_board=2,t_alight=11),
             SimpleNamespace(t=1,t_board=None,t_alight=None)]
        self.assertEqual(event_violations(SimpleNamespace(pax=pax,t=10)),4)

    def test_interval_uses_seed_averages_not_repeated_days(self):
        rows=[]
        for seed,effect in [(1,-1),(2,0),(3,1)]:
            for day in ['a','b','c','d']:
                for controller,value in [('C1',10),('D10',10+effect)]:
                    rows.append(dict(date=day,variant='low',seed=seed,dt=.25,controller=controller,
                        awt=value,n=10,input_sha256=f'{day}_{seed}'))
        result=contrast_rows(rows,controls=['D10'])[0]
        self.assertEqual(result['n_seeds'],3)
        self.assertAlmostEqual(result['mean'],0)
        self.assertAlmostEqual(result['high'],2.4841377,places=5)

    def test_duplicate_and_unpaired_inputs_rejected(self):
        row=dict(date='a',variant='low',seed=1,dt=.25,controller='C1',awt=10,n=10,input_sha256='one')
        with self.assertRaises(AssertionError):paired_index([row,row])
        other=dict(row,controller='D10',input_sha256='different')
        with self.assertRaises(AssertionError):contrast_rows([row,other],controls=['D10'])


if __name__=='__main__':unittest.main()
