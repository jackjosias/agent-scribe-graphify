from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from scribe_test_utils import load_script_module


scribe_lock = load_script_module("scribe_lock")
lock_main = getattr(scribe_lock, "main")
configured_owner_pid = getattr(scribe_lock, "configured_owner_pid")
scribe_workflow_ack = load_script_module("scribe_workflow_ack")
record_workflow_ack = getattr(scribe_workflow_ack, "record_workflow_ack")


class ScribeLockTests(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        old_argv = sys.argv[:]
        sys.argv = ["scribe lock", *args]
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = lock_main()
        finally:
            sys.argv = old_argv
        return code, stdout.getvalue(), stderr.getvalue()

    @contextlib.contextmanager
    def isolated_lock_path(self, root: Path):
        old_lock_path = os.environ.get("SCRIBE_LOCK_PATH")
        os.environ["SCRIBE_LOCK_PATH"] = str(root / "locks" / "scribe.lock")
        try:
            yield
        finally:
            if old_lock_path is None:
                os.environ.pop("SCRIBE_LOCK_PATH", None)
            else:
                os.environ["SCRIBE_LOCK_PATH"] = old_lock_path

    @contextlib.contextmanager
    def isolated_workflow_ack_path(self, root: Path):
        old_ack_path = os.environ.get("SCRIBE_WORKFLOW_ACK_PATH")
        os.environ["SCRIBE_WORKFLOW_ACK_PATH"] = str(root / "workflow-acks.json")
        try:
            yield
        finally:
            if old_ack_path is None:
                os.environ.pop("SCRIBE_WORKFLOW_ACK_PATH", None)
            else:
                os.environ["SCRIBE_WORKFLOW_ACK_PATH"] = old_ack_path

    def test_configured_owner_pid_defaults_to_current_process_pid(self) -> None:
        old_owner_pid = os.environ.pop("SCRIBE_OWNER_PID", None)
        try:
            self.assertEqual(configured_owner_pid(), os.getpid())
        finally:
            if old_owner_pid is not None:
                os.environ["SCRIBE_OWNER_PID"] = old_owner_pid

    def test_acquire_status_release_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_lock_path(Path(tmp)), self.isolated_workflow_ack_path(Path(tmp)):
            record_workflow_ack("unit", "unknown")
            code, output, error = self.run_cli("acquire", "--agent", "unit", "--session", "JOURNAL-100")
            self.assertEqual(code, 0, error)
            self.assertIn("acquired", output)

            code, output, error = self.run_cli("status")
            self.assertEqual(code, 0, error)
            self.assertIn("state: locked", output)
            self.assertIn("agent: unit", output)
            self.assertIn("owner_id: unit", output)
            self.assertIn("owner_pid:", output)

            code, output, error = self.run_cli("release", "--agent", "unit")
            self.assertEqual(code, 0, error)
            self.assertIn("released", output)

    def test_status_releases_stale_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_lock_path(Path(tmp)):
            lock_path = Path(os.environ["SCRIBE_LOCK_PATH"])
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_text(
                '{"agent":"dead","session":"JOURNAL-999","pid":0,"acquired_at":"2026-01-01T00:00:00Z","surface":"scribe-memory","ttl_minutes":30}\n',
                encoding="utf-8",
            )

            code, output, error = self.run_cli("status")

        self.assertEqual(code, 0, error)
        self.assertIn("state: unlocked", output)
        self.assertIn("stale_released:", output)

    def test_release_removes_own_stale_lock_without_status_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_lock_path(Path(tmp)):
            lock_path = Path(os.environ["SCRIBE_LOCK_PATH"])
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_text(
                json.dumps(
                    {
                        "agent": "unit",
                        "session": "JOURNAL-100",
                        "pid": 0,
                        "acquired_at": "2026-01-01T00:00:00Z",
                        "surface": "scribe-memory",
                        "ttl_minutes": 30,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            code, output, error = self.run_cli("release", "--agent", "unit")
            lock_exists = lock_path.exists()

        self.assertEqual(code, 0, error)
        self.assertIn("released stale lock", output)
        self.assertFalse(lock_exists)

    def test_release_refuses_stale_lock_owned_by_another_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_lock_path(Path(tmp)):
            lock_path = Path(os.environ["SCRIBE_LOCK_PATH"])
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_text(
                json.dumps(
                    {
                        "agent": "other",
                        "session": "JOURNAL-100",
                        "pid": 0,
                        "acquired_at": "2026-01-01T00:00:00Z",
                        "surface": "scribe-memory",
                        "ttl_minutes": 30,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            code, output, error = self.run_cli("release", "--agent", "unit")
            lock_exists = lock_path.exists()

        self.assertEqual(code, 2)
        self.assertEqual(error, "")
        self.assertIn("lock held by other, not unit", output)
        self.assertTrue(lock_exists)

    def test_acquire_refuses_without_workflow_ack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_lock_path(Path(tmp)), self.isolated_workflow_ack_path(Path(tmp)):
            code, output, error = self.run_cli("acquire", "--agent", "unit", "--session", "JOURNAL-100")

        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("workflow ACK_REQUIRED", error)


if __name__ == "__main__":
    unittest.main()
