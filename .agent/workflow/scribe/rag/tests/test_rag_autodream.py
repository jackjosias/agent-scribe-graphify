from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from rag_autodream import run_autodream
from rag_autodream_format import format_autodream_json, format_autodream_report
from rag_autodream_io import AutoDreamBudget, AutoDreamConfig, ReadOnlyGuard, ReadOnlyViolation


class RagAutoDreamTests(unittest.TestCase):
    def test_autodream_requires_explicit_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.build_repo(Path(tmp))
            config = AutoDreamConfig(project_root=root, scribe_path=root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe", index_path=root / "scribe-out" / "rag-index.json", read_only=False)

            report = run_autodream(config)

            self.assertEqual(report["status"], "READ_ONLY_VIOLATION")
            self.assertEqual(report["exit_code"], 4)

    def test_autodream_returns_structured_read_only_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.build_repo(Path(tmp))
            config = self.config_for(root)

            report = run_autodream(config)
            text_output = format_autodream_report(report)
            json_output = json.loads(format_autodream_json(report))

            self.assertEqual(report["status"], "OK")
            self.assertTrue(report["read_only_proof"]["unchanged"])
            self.assertIn("diff_digest", report)
            self.assertIn("candidate_memories", report)
            self.assertIn("SCRIBE-RAG AUTODREAM", text_output)
            self.assertEqual(json_output["command"], "scribe-rag autodream")

    def test_autodream_honors_cancel_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.build_repo(Path(tmp))
            cancel_file = root / ".cancel-autodream"
            cancel_file.write_text("stop", encoding="utf-8")
            config = self.config_for(root, cancel_file=cancel_file)

            report = run_autodream(config)

            self.assertEqual(report["status"], "CANCELLED")
            self.assertEqual(report["exit_code"], 130)

    def test_autodream_honors_file_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.build_repo(Path(tmp))
            config = self.config_for(root, max_files=1)

            report = run_autodream(config)

            self.assertEqual(report["status"], "BUDGET_EXCEEDED")
            self.assertEqual(report["exit_code"], 3)

    def test_read_only_guard_detects_protected_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.build_repo(Path(tmp))
            budget = AutoDreamBudget(max_files=10, max_read_bytes=100_000)
            guard = ReadOnlyGuard(root, ("AGENT-MEMOIRE_PROJECT_STATUS.scribe",), budget)
            guard.snapshot_before()
            (root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").write_text("changed\n", encoding="utf-8")

            with self.assertRaises(ReadOnlyViolation):
                guard.verify_after()

    def config_for(self, root: Path, **overrides) -> AutoDreamConfig:
        values = {
            "project_root": root,
            "scribe_path": root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe",
            "index_path": root / "scribe-out" / "rag-index.json",
            "protected_paths": ("AGENT-MEMOIRE_PROJECT_STATUS.scribe", "scribe-out/rag-index.json"),
            "max_files": 80,
            "max_read_bytes": 500_000,
            "timeout_ms": 2_000,
        }
        values.update(overrides)
        return AutoDreamConfig(**values)

    def build_repo(self, root: Path) -> Path:
        subprocess.run(["git", "init"], cwd=str(root), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (root / "scribe-out").mkdir()
        (root / ".agent" / "rules").mkdir(parents=True)
        (root / ".agent" / "workflow" / "scribe" / "rag").mkdir(parents=True)
        (root / ".agent" / "workflow" / "scribe" / "sel" / "docs").mkdir(parents=True)
        (root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").write_text("metrics:\n  sessions_total: 1\n", encoding="utf-8")
        index = {"version": 2, "mode": "bm25", "source_sha256": "", "stats": {"entities": 0, "negative_terms": 0}, "entities": []}
        (root / "scribe-out" / "rag-index.json").write_text(json.dumps(index), encoding="utf-8")
        for path in self.autodream_docs(root):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("AutoDream is a read-only review, not an automatic idle daemon.\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(root), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (root / ".agent" / "workflow" / "scribe" / "rag" / "README.md").write_text("AutoDream is a read-only review.\nchanged\n", encoding="utf-8")
        return root

    def autodream_docs(self, root: Path) -> list[Path]:
        return [
            root / "AGENTS.md",
            root / ".agent" / "rules" / "scribe.md",
            root / ".agent" / "workflow" / "scribe" / "README.md",
            root / ".agent" / "workflow" / "scribe" / "rag" / "README.md",
            root / ".agent" / "workflow" / "scribe" / "sel" / "docs" / "AGENTS.md",
            root / ".agent" / "workflow" / "scribe" / "sel" / "docs" / "friction-policy.md",
            root / ".agent" / "workflow" / "scribe" / "sel" / "docs" / "scribe.md",
        ]


if __name__ == "__main__":
    unittest.main()
