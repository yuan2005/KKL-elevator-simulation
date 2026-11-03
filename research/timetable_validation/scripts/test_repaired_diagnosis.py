import unittest
from diagnose_repaired_windows import canonical_switches,passenger_groups


class DiagnosisTests(unittest.TestCase):
    def test_verbose_names_and_exact_switch_boundary(self):
        switches=canonical_switches([[0,'C1 ETA distributed'],[600,'C7 ETA entrance'],[1200,'C1 ETA distributed']])
        self.assertEqual(passenger_groups(599.999,switches),('C1','initial'))
        self.assertEqual(passenger_groups(600,switches),('C7','after_switch_lt300s'))
        self.assertEqual(passenger_groups(900,switches),('C7','after_switch_ge300s'))
        self.assertEqual(passenger_groups(1200,switches),('C1','after_switch_lt300s'))

    def test_unknown_or_unsorted_state_log_rejected(self):
        for switches in [[[0,'D10']],[[600,'C1']],[[0,'C1'],[600,'C7'],[600,'C1']]]:
            with self.assertRaises(AssertionError):canonical_switches(switches)


if __name__=='__main__':unittest.main()
