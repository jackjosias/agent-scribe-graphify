from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scribe_test_utils import load_script_module


scribe_workflow_ack = load_script_module("scribe_workflow_ack")
check_workflow_ack = getattr(scribe_workflow_ack, "check_workflow_ack")
read_workflow_acks = getattr(scribe_workflow_ack, "read_workflow_acks")
record_workflow_ack = getattr(scribe_workflow_ack, "record_workflow_ack")


class ScribeWorkflowAckTests(unittest.TestCase):
    def write_workflow_file(self, root: Path, content: str = "workflow v1") -> Path:
        path = root / "AGENTS.md"
        path.write_text(content, encoding="utf-8")
        return path

    def test_ack_created_with_current_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_workflow_file(root)
            ack_path = root / "scribe-out" / "workflow-acks.json"

            ack, digest, written_path = record_workflow_ack("agent-a", "cli", root=root, ack_path=ack_path)
            payload = read_workflow_acks(ack_path)

        self.assertEqual(written_path, ack_path)
        self.assertEqual(ack["workflow_sha256"], digest.sha256)
        self.assertEqual(payload["workflow_sha256"], digest.sha256)
        self.assertIn("agent-a", payload["acks"])

    def test_ack_stale_when_workflow_modified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow_file = self.write_workflow_file(root)
            ack_path = root / "scribe-out" / "workflow-acks.json"
            record_workflow_ack("agent-a", "cli", root=root, ack_path=ack_path)
            workflow_file.write_text("workflow v2", encoding="utf-8")

            ok, verdict, _ack, _digest, _path = check_workflow_ack("agent-a", root=root, ack_path=ack_path)

        self.assertFalse(ok)
        self.assertEqual(verdict, "ACK_STALE")

    def test_ack_fresh_when_workflow_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_workflow_file(root)
            ack_path = root / "scribe-out" / "workflow-acks.json"
            record_workflow_ack("agent-a", "cli", root=root, ack_path=ack_path)

            ok, verdict, ack, _digest, _path = check_workflow_ack("agent-a", root=root, ack_path=ack_path)

        self.assertTrue(ok)
        self.assertEqual(verdict, "ACK_OK")
        self.assertEqual(ack["agent"], "agent-a")


if __name__ == "__main__":
    unittest.main()
