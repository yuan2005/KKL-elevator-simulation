"""Archived JSON logs must compare by values, not tuple/list representation."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import run_third_grid as runner


class ThirdGridTests(unittest.TestCase):
    def result(self):
        return dict(rows=[dict(input_sha256='same')],input_audit={'n':1},
                    logs={'D10':dict(decisions=[{'t':600.,'choice':'C7'}],switches=[(0.,'C1'),(600.,'C7')])})

    def test_archived_list_and_in_memory_tuple_are_equivalent(self):
        import json
        archived=json.loads(json.dumps(self.result()))
        config=SimpleNamespace(DT=.25);seen=[]
        def run(args):
            seen.append(config.DT)
            return self.result()
        with patch.dict(sys.modules,{'config':config}),patch.object(runner,'load',return_value={'runs':[archived,archived]}),patch.object(runner,'adaptive_worker',run):
            result=runner.worker((('day','low',1),'prior.json'))
        self.assertEqual(seen,[.0625]);self.assertEqual(config.DT,.25)
        self.assertEqual(result['dt'],.0625)

    def test_real_switch_difference_rejected_and_dt_restored(self):
        import json
        archived=json.loads(json.dumps(self.result()))
        archived['logs']['D10']['switches'][1][0]=660.
        config=SimpleNamespace(DT=.25)
        with patch.dict(sys.modules,{'config':config}),patch.object(runner,'load',return_value={'runs':[archived]}),patch.object(runner,'adaptive_worker',return_value=self.result()):
            with self.assertRaises(AssertionError):runner.worker((('day','low',1),'prior.json'))
        self.assertEqual(config.DT,.25)


if __name__=='__main__':unittest.main()
