from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import tenor_memory_admission
from _strict_cleanup import remove_tree_strict


class TenorMemoryAdmissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        records = self.root / ".agent" / "state" / "outputs" / "scribe-out" / "records"
        records.mkdir(parents=True)
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True)
        self.memory = self.root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
        self.memory.write_text("version: 1\nmetrics:\n  count: 0\n", encoding="utf-8")
        self.record_path = records / "record.json"
        self.record = {
            "timestamp": 1784290000,
            "record_type": "bug_fix",
            "request": "fix production regression",
            "summary": "Validated atomic fix for the production regression",
            "resources": ["src/feature.py"],
            "verdict": "CHANGESET_COMMITTED",
            "memory_policy": "canonical_required",
        }
        self.record_path.write_text(json.dumps(self.record), encoding="utf-8")
        self.validators = [{"argv": ["python3", "-m", "pytest"], "returncode": 0, "ok": True}]
        self.files = [{"path": "src/feature.py", "operation": "edit", "new_hash": "a" * 64}]

    def tearDown(self) -> None:
        remove_tree_strict(self.tmp.name)
        self.tmp.cleanup()

    def test_verified_source_fix_is_promoted_and_retrievable_from_canonical_file(self) -> None:
        result = tenor_memory_admission.admit_runtime_record(
            project_root=self.root,
            task_id="task-a",
            agent_id="agent-a",
            objective="fix production regression",
            intent="write",
            summary=self.record["summary"],
            files=self.files,
            validators=self.validators,
            record=self.record,
            record_path=self.record_path,
            scope="src/feature.py",
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "promote")
        self.assertIn(result["entry_id"], self.memory.read_text(encoding="utf-8"))
        stored = tenor_memory_admission.get_admission(self.root, "task-a", "agent-a")
        self.assertEqual(stored["decision"], "promote")

    def test_read_only_or_ephemeral_document_noise_is_filtered_with_reason(self) -> None:
        read_only = tenor_memory_admission.classify_outcome(
            objective="inspect current state",
            intent="read",
            summary="inspection only",
            files=[],
            validators=[],
            canonical_memory_active=True,
        )
        self.assertEqual(read_only["decision"], "runtime_only")
        self.assertGreaterEqual(len(read_only["reason"]), 24)

        docs = tenor_memory_admission.classify_outcome(
            objective="correct a typo",
            intent="write",
            summary="spelling only",
            files=[{"path": "README.md", "operation": "edit"}],
            validators=self.validators,
            canonical_memory_active=True,
        )
        self.assertEqual(docs["decision"], "runtime_only")
        self.assertIn("non-source", docs["reason"])

    def test_missing_or_failed_validation_is_a_conflict_not_a_memory_record(self) -> None:
        missing = tenor_memory_admission.classify_outcome(
            objective="fix feature",
            intent="write",
            summary="done",
            files=self.files,
            validators=[],
            canonical_memory_active=True,
        )
        self.assertEqual(missing["decision"], "conflict")
        failed = tenor_memory_admission.classify_outcome(
            objective="fix feature",
            intent="write",
            summary="done",
            files=self.files,
            validators=[{"ok": False, "returncode": 1}],
            canonical_memory_active=True,
        )
        self.assertEqual(failed["decision"], "conflict")

    def test_explicit_unresolved_choice_waits_for_user_instead_of_guessing(self) -> None:
        result = tenor_memory_admission.classify_outcome(
            objective="choose between two incompatible persistence architectures",
            intent="write",
            summary="decision pending user approval",
            files=self.files,
            validators=self.validators,
            canonical_memory_active=True,
        )
        self.assertEqual(result["decision"], "ask_user")


if __name__ == "__main__":
    unittest.main()
