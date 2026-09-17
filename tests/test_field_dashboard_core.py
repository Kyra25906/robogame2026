from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

from tools.field_dashboard_core import (
    DashboardState, SafetyStatus, SessionArchive, action_blockers, clamp_drive,
)


def ready(now=10.0):
    return SafetyStatus(received_at=now, communication_ok=True, physical_start=True)


class SafetyTests(unittest.TestCase):
    def test_each_safety_condition_blocks_actions(self):
        cases = [
            (SafetyStatus(), "NO_STATUS"),
            (SafetyStatus(received_at=1.0, communication_ok=True, physical_start=True), "STATUS_STALE"),
            (SafetyStatus(received_at=10.0, physical_start=True), "COMMUNICATION"),
            (SafetyStatus(received_at=10.0, communication_ok=True, physical_start=True, emergency_stop=True), "EMERGENCY_STOP"),
            (SafetyStatus(received_at=10.0, communication_ok=True), "PHYSICAL_START"),
            (SafetyStatus(received_at=10.0, communication_ok=True, physical_start=True, mechanism_fault=True), "MECHANISM_FAULT"),
        ]
        for status, code in cases:
            with self.subTest(code=code):
                self.assertIn(code, [item["code"] for item in action_blockers(status, 10.1)])

    def test_drive_is_clamped_and_non_finite_rejected(self):
        self.assertEqual(clamp_drive(2, -2, 9), (0.3, -0.3, 0.8))
        with self.assertRaises(ValueError):
            clamp_drive(float("nan"), 0, 0)

    def test_manual_mode_rejects_conflict_and_expires(self):
        state = DashboardState(safety=ready())
        with self.assertRaisesRegex(ValueError, "other_node"):
            state.acquire_manual(now=10.0, conflicting_publishers=["other_node"])
        state.acquire_manual(now=10.0, conflicting_publishers=[])
        state.accept_drive(sequence=1, now=10.0)
        self.assertFalse(state.manual_expired(10.29))
        self.assertTrue(state.manual_expired(10.31))


class ArchiveTests(unittest.TestCase):
    def test_archive_keeps_structured_and_raw_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = SessionArchive(Path(directory), now=datetime(2026, 8, 20, 1, 2, 3))
            archive.append("event", {"type": "process", "name": "bridge"})
            archive.append("telemetry", {"topic": "/robot/status"})
            archive.append_raw("bridge", "handshake complete")
            event = json.loads((archive.path / "events.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(event["name"], "bridge")
            self.assertIn("/robot/status", (archive.path / "telemetry.jsonl").read_text(encoding="utf-8"))
            self.assertIn("handshake complete", (archive.raw_path / "bridge.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
