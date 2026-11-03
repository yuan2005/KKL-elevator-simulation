"""Verify time-grid isolation including exception paths, without loading the simulator."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import run_timestep_sensitivity as runner


class TimeGridTests(unittest.TestCase):
    def test_grid_applied_and_restored(self):
        config=SimpleNamespace(DT=.5)
        seen=[]
        def fake(args):
            seen.append(config.DT)
            return dict(rows=[dict(input_sha256='same')],input_audit={'n':1},logs={'decisions':[60]})
        with patch.dict(sys.modules,{'config':config}),patch.object(runner,'adaptive_worker',fake):
            result=runner.worker(('day','low',1))
        self.assertEqual(seen,[.25,.125])
        self.assertEqual(config.DT,.5)
        self.assertEqual([r['rows'][0]['dt'] for r in result['runs']],seen)

    def test_restore_after_failed_run(self):
        config=SimpleNamespace(DT=.5)
        with patch.dict(sys.modules,{'config':config}),patch.object(runner,'adaptive_worker',side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):runner.worker(('day','low',1))
        self.assertEqual(config.DT,.5)


if __name__=='__main__':unittest.main()
