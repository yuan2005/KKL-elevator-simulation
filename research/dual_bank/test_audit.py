import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config as C
from research.dual_bank.audit import reconstruct
from research.dual_bank.control import FixedControl
from research.dual_bank.demand import REGIMES
from research.dual_bank.engine import DualBankSim
from research.dual_bank.experiment import (
    digest, payload, read_raw, request_record, save, transition_day, verify,
)
from traffic import Passenger


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.settings = C.DT, C.DRAIN_TIME
        C.DT, C.DRAIN_TIME = .5, 3600.

    def tearDown(self):
        C.DT, C.DRAIN_TIME = self.settings

    def raw(self):
        passengers = [Passenger(0, 0., 3, 10), Passenger(1, 25., 10, 3)]
        inputs = [request_record(p) for p in passengers]
        sim = DualBankSim(passengers, FixedControl("pickup_uniform_b0"), 60.).run()
        return payload(sim, {}, "test", inputs, dict(sim.metrics(), phase="daily_eval"))

    def test_service_latch_and_empty_stop_reconstruction(self):
        raw = self.raw()
        result = reconstruct(raw)
        self.assertEqual(result["passengers"], 2)
        self.assertGreater(result["empty_stops"], 0)

    def test_published_spec_does_not_include_machine_path(self):
        passengers = [Passenger(0, 0., 3, 10)]
        sim = DualBankSim(passengers, FixedControl("pickup_uniform_b0"), 30.).run()
        raw = payload(sim, {"root": r"C:\private-workspace"}, "test",
                      [request_record(p) for p in passengers], sim.metrics())
        self.assertEqual(raw["spec"]["root"], ".")

    def test_portable_manifest_rejects_source_and_protocol_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "model.py"
            source.write_text("MODEL = 1\n", encoding="utf-8")
            specification = {"model": "dual_bank"}
            save(root / "protocol.json", specification)
            save(root / "source_manifest.json", {
                "protocol_hash": digest(specification),
                "sources": {"model.py": hashlib.sha256(source.read_bytes()).hexdigest()},
            })
            with patch("research.dual_bank.experiment.ROOT", root):
                self.assertEqual(verify(root), specification)
                source.write_text("MODEL = 2\n", encoding="utf-8")
                with self.assertRaisesRegex(AssertionError, "Packaged source changed"):
                    verify(root)
                source.write_text("MODEL = 1\n", encoding="utf-8")
                save(root / "protocol.json", {"model": "changed"})
                with self.assertRaisesRegex(AssertionError, "Published protocol changed"):
                    verify(root)

    def test_duplicate_passenger_rejected(self):
        raw = self.raw()
        raw["service"][1] = copy.deepcopy(raw["service"][0])
        with self.assertRaises(AssertionError):
            reconstruct(raw)

    def test_cross_bank_assignment_rejected(self):
        raw = self.raw()
        raw["decisions"][0]["car"] = (raw["decisions"][0]["bank"]+1)%2*3
        with self.assertRaises(AssertionError):
            reconstruct(raw)

    def test_silent_remote_cancellation_rejected(self):
        raw = self.raw()
        event = next(e for e in raw["latches"] if e["action"] == "clear")
        event["car"] = (event["bank"]+1)%2*3
        with self.assertRaises(AssertionError):
            reconstruct(raw)

    def test_transition_disk_roundtrip_same_state_and_resume(self):
        def tiny_generate(level, seed):
            return [Passenger(0, 45., 3, 10), Passenger(1, 65., 10, 3)], 120., []
        with tempfile.TemporaryDirectory() as directory:
            specification = dict(
                root=directory, key="tiny", phase="transition_dev", dt=.5,
                drain_time=3600., reassign_interval=10., level="low", seed=1,
                initial="pickup_uniform_b0", development=True, horizon=30.,
                ranking={r: ["pickup_uniform_b0", "marginal_uniform_b1"] for r in REGIMES},
            )
            with patch("research.dual_bank.experiment.generate", tiny_generate), \
                    patch("research.dual_bank.experiment.BOUNDARIES", (1,)):
                self.assertEqual(transition_day(specification)["branches"], 5)
                files = sorted((Path(directory) / "raw" / "transition_dev").glob("*.json.gz"))
                hashes = [read_raw(path)["extra"]["snapshot_hash"] for path in files]
                self.assertEqual(len(set(hashes)), 1)
                for path in files:
                    reconstruct(read_raw(path))
                before = [path.read_bytes() for path in files]
                transition_day(specification)
                self.assertEqual(before, [path.read_bytes() for path in files])


if __name__ == "__main__":
    unittest.main()
