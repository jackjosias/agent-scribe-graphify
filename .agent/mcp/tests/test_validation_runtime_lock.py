from __future__ import annotations

import multiprocessing as mp
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime.validation_lock import ValidationRuntimeBusy, validation_runtime_busy_message, validation_runtime_lock

ROOT = Path(__file__).resolve().parents[3]


def hold_lock(root: str, seconds: float) -> None:
    with validation_runtime_lock(Path(root), timeout_seconds=1):
        time.sleep(seconds)


class ValidationRuntimeLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_01_lock_can_be_acquired_and_released(self) -> None:
        with validation_runtime_lock(self.root, timeout_seconds=1) as path:
            self.assertTrue(path.exists())
        with validation_runtime_lock(self.root, timeout_seconds=1):
            self.assertTrue(True)

    def test_02_second_attempt_times_out_while_held(self) -> None:
        with validation_runtime_lock(self.root, timeout_seconds=1):
            with self.assertRaises(ValidationRuntimeBusy):
                with validation_runtime_lock(self.root, timeout_seconds=0.05, poll_interval=0.01):
                    pass

    def test_03_lock_releases_after_exception(self) -> None:
        with self.assertRaises(ValueError):
            with validation_runtime_lock(self.root, timeout_seconds=1):
                raise ValueError("boom")
        with validation_runtime_lock(self.root, timeout_seconds=1):
            self.assertTrue(True)

    def test_04_timeout_reports_busy(self) -> None:
        with validation_runtime_lock(self.root, timeout_seconds=1):
            try:
                with validation_runtime_lock(self.root, timeout_seconds=0.01, poll_interval=0.01):
                    pass
            except ValidationRuntimeBusy as exc:
                self.assertEqual(str(exc), "VALIDATION_RUNTIME_BUSY")
                return
        self.fail("expected ValidationRuntimeBusy")

    def test_05_busy_message_tells_agents_to_run_sequentially(self) -> None:
        message = validation_runtime_busy_message(self.root / ".agent" / "state" / "runtime" / "validation-smoke.lock")
        self.assertIn("VALIDATION_RUNTIME_BUSY_RUN_SEQUENTIALLY", message)
        self.assertIn("validation-smoke.lock", message)

    def test_06_mcp_smoke_uses_validation_lock(self) -> None:
        text = (ROOT / ".agent" / "scripts" / "mcp_smoke.py").read_text(encoding="utf-8")
        self.assertIn("validation_runtime_lock", text)
        self.assertIn("VALIDATION_RUNTIME_LOCK_ACQUIRED", text)
        self.assertIn("validation_runtime_busy_message", text)

    def test_07_redteam_smoke_uses_validation_lock(self) -> None:
        text = (ROOT / ".agent" / "scripts" / "enforcement_redteam_smoke.py").read_text(encoding="utf-8")
        self.assertIn("validation_runtime_lock", text)
        self.assertIn("VALIDATION_RUNTIME_LOCK_ACQUIRED", text)
        self.assertIn("validation_runtime_busy_message", text)

    def test_08_same_runtime_is_serialized(self) -> None:
        proc = mp.Process(target=hold_lock, args=(str(self.root), 0.3))
        proc.start()
        time.sleep(0.05)
        start = time.monotonic()
        with validation_runtime_lock(self.root, timeout_seconds=2, poll_interval=0.02):
            elapsed = time.monotonic() - start
        proc.join(timeout=2)
        self.assertEqual(proc.exitcode, 0)
        self.assertGreaterEqual(elapsed, 0.20)

    def test_09_validation_suite_runs_lock_test_before_runtime_smokes(self) -> None:
        text = (ROOT / ".agent" / "scripts" / "validation_suite.py").read_text(encoding="utf-8")
        lock_pos = text.index("test_validation_runtime_lock.py")
        smoke_pos = text.index("mcp_smoke.py")
        redteam_pos = text.index("enforcement_redteam_smoke.py")
        self.assertLess(lock_pos, smoke_pos)
        self.assertLess(smoke_pos, redteam_pos)



if __name__ == "__main__":
    unittest.main()
