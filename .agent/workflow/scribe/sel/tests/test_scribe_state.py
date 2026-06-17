from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from scribe_test_utils import load_script_module, write_fixture


scribe_state = load_script_module("scribe_state")
state_main = getattr(scribe_state, "main")
state_path_for_scribe = getattr(scribe_state, "state_path_for_scribe")

scribe_doctor_lib = load_script_module("scribe_doctor_lib")
run_doctor = getattr(scribe_doctor_lib, "run_doctor")


class ScribeStateTests(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        old_argv = sys.argv[:]
        sys.argv = ["scribe state", *args]
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = state_main()
        finally:
            sys.argv = old_argv
        return code, stdout.getvalue(), stderr.getvalue()

    @contextlib.contextmanager
    def isolated_state_path(self, root: Path):
        old_state_path = os.environ.get("SCRIBE_STATE_PATH")
        os.environ["SCRIBE_STATE_PATH"] = str(root / "scribe-out" / "state.json")
        try:
            yield
        finally:
            if old_state_path is None:
                os.environ.pop("SCRIBE_STATE_PATH", None)
            else:
                os.environ["SCRIBE_STATE_PATH"] = old_state_path

    @contextlib.contextmanager
    def isolated_workflow_ack_path(self, root: Path):
        old_ack_path = os.environ.get("SCRIBE_WORKFLOW_ACK_PATH")
        os.environ["SCRIBE_WORKFLOW_ACK_PATH"] = str(root / "scribe-out" / "workflow-acks.json")
        try:
            yield
        finally:
            if old_ack_path is None:
                os.environ.pop("SCRIBE_WORKFLOW_ACK_PATH", None)
            else:
                os.environ["SCRIBE_WORKFLOW_ACK_PATH"] = old_ack_path

    def test_sync_reports_missing_state_without_rewriting_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_state_path(Path(tmp)):
            path = write_fixture(Path(tmp))
            code, output, error = self.run_cli("sync", "--agent", "codex-cli", "--type", "cli", "--scribe", str(path))

        self.assertEqual(code, 1, error)
        self.assertIn("verdict: STALE_STATE_MISSING", output)

    def test_sync_repair_writes_state_and_whoami_reads_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_state_path(Path(tmp)):
            root = Path(tmp)
            path = write_fixture(root)
            code, output, error = self.run_cli(
                "sync",
                "--repair",
                "--agent",
                "codex-cli",
                "--type",
                "cli",
                "--session",
                "JOURNAL-100",
                "--changed-id",
                "PAT-100",
                "--scribe",
                str(path),
            )
            state = json.loads(state_path_for_scribe(path).read_text(encoding="utf-8"))
            whoami_code, whoami_output, whoami_error = self.run_cli("whoami", "--scribe", str(path))

        self.assertEqual(code, 0, error)
        self.assertIn("verdict: IN_SYNC", output)
        self.assertEqual(state["writer"], {"agent": "codex-cli", "type": "cli"})
        self.assertEqual(state["changed_ids"], ["PAT-100"])
        self.assertEqual(whoami_code, 0, whoami_error)
        self.assertIn("last_writer: codex-cli", whoami_output)

    def test_sync_detects_hash_mismatch_after_manual_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_state_path(Path(tmp)):
            root = Path(tmp)
            path = write_fixture(root)
            code, _, error = self.run_cli(
                "sync",
                "--repair",
                "--agent",
                "codex-cli",
                "--type",
                "cli",
                "--session",
                "JOURNAL-100",
                "--scribe",
                str(path),
            )
            self.assertEqual(code, 0, error)
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            code, output, error = self.run_cli("sync", "--agent", "codex-ide", "--type", "extension", "--scribe", str(path))

        self.assertEqual(code, 1, error)
        self.assertIn("verdict: STALE_HASH", output)
        self.assertIn("last_writer: codex-cli", output)

    def test_doctor_warns_on_invalid_write_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_state_path(Path(tmp)):
            root = Path(tmp)
            path = write_fixture(root)
            code, _, error = self.run_cli(
                "sync",
                "--repair",
                "--agent",
                "codex-cli",
                "--type",
                "cli",
                "--session",
                "JOURNAL-100",
                "--scribe",
                str(path),
            )
            self.assertEqual(code, 0, error)
            state_path = state_path_for_scribe(path)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["write_kind"] = "tier_update"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                doctor_code = run_doctor(path, root / "doctor.md", suggest_fix=True)
            report = (root / "doctor.md").read_text(encoding="utf-8")

        self.assertEqual(doctor_code, 0)
        self.assertIn("W013", report)

    def test_workflow_read_check_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_workflow_ack_path(Path(tmp)):
            root = Path(tmp)
            (root / "AGENTS.md").write_text("workflow v1\n", encoding="utf-8")

            code, output, error = self.run_cli("workflow", "check", "--agent", "codex", "--root", str(root))
            self.assertEqual(code, 2, error)
            self.assertIn("verdict: ACK_REQUIRED", output)

            code, output, error = self.run_cli("workflow", "read", "--agent", "codex", "--type", "cli", "--root", str(root))
            self.assertEqual(code, 0, error)
            self.assertIn("verdict: ACK_RECORDED", output)
            self.assertIn("AGENTS.md", output)

            code, output, error = self.run_cli("workflow", "check", "--agent", "codex", "--root", str(root))
            self.assertEqual(code, 0, error)
            self.assertIn("verdict: ACK_OK", output)

            code, output, error = self.run_cli("workflow", "status", "--strict", "--root", str(root))
            self.assertEqual(code, 0, error)
            self.assertIn("required_agents: none", output)
            self.assertIn("recorded_agents: codex", output)
            self.assertIn("verdict: POOL_STATUS_OK", output)

            code, output, error = self.run_cli(
                "workflow",
                "status",
                "--required",
                "codex,claude",
                "--strict",
                "--root",
                str(root),
            )
            self.assertEqual(code, 2, error)
            self.assertIn("agent[codex]: ACK_OK", output)
            self.assertIn("agent[claude]: ACK_REQUIRED", output)


if __name__ == "__main__":
    unittest.main()
