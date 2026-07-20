from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import tenor_decision
from _strict_cleanup import remove_tree_strict


class TenorDecisionCapsuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True)
        graphify_out = self.root / ".agent" / "state" / "outputs" / "graphify-out"
        graphify_out.mkdir(parents=True)
        (graphify_out / "GRAPHIFY_READY.json").write_text('{"state":"ready"}\n', encoding="utf-8")
        (self.root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").write_text("version: 1\n", encoding="utf-8")

    def tearDown(self) -> None:
        remove_tree_strict(self.tmp.name)
        self.tmp.cleanup()

    def build(self) -> dict[str, object]:
        return tenor_decision.build_capsule(
            project_root=self.root,
            task_id="task-a",
            agent_id="agent-a",
            objective="fix the production regression",
            intent="write",
            scope="src/feature.py",
            resources=["src/feature.py"],
            scribe_result={"ok": True, "verdict": "SCRIBE_QUERY_DONE", "result": {"stdout": "known scar"}},
            graphify_result={"ok": True, "verdict": "GRAPHIFY_QUERY_DONE", "result": {"stdout": "caller -> feature"}},
            graphify_required=True,
        )

    def test_capsule_binds_scribe_graphify_and_resources(self) -> None:
        created = self.build()
        self.assertTrue(created["ok"], created)
        self.assertEqual(created["verdict"], "TENOR_DECISION_CAPSULE_READY")
        verified = tenor_decision.verify_capsule(
            self.root,
            "task-a",
            "agent-a",
            ["src/feature.py"],
        )
        self.assertTrue(verified["ok"], verified)
        self.assertEqual(verified["capsule_hash"], created["capsule_hash"])
        self.assertEqual(verified["scribe"]["verdict"], "SCRIBE_QUERY_DONE")
        self.assertEqual(verified["graphify"]["verdict"], "GRAPHIFY_QUERY_DONE")

    def test_mutating_capsule_requires_real_scribe_and_graphify_evidence(self) -> None:
        missing_scribe = tenor_decision.build_capsule(
            project_root=self.root,
            task_id="task-missing-scribe",
            agent_id="agent-a",
            objective="fix feature",
            intent="write",
            scope="src/feature.py",
            resources=["src/feature.py"],
            scribe_result={},
            graphify_result={"ok": True, "verdict": "GRAPHIFY_QUERY_DONE"},
            graphify_required=True,
        )
        self.assertEqual(missing_scribe["verdict"], "TENOR_DECISION_SCRIBE_EVIDENCE_REQUIRED")

        missing_graph = tenor_decision.build_capsule(
            project_root=self.root,
            task_id="task-missing-graph",
            agent_id="agent-a",
            objective="fix feature",
            intent="write",
            scope="src/feature.py",
            resources=["src/feature.py"],
            scribe_result={"ok": True, "verdict": "SCRIBE_QUERY_DONE"},
            graphify_result={},
            graphify_required=True,
        )
        self.assertEqual(missing_graph["verdict"], "TENOR_DECISION_GRAPHIFY_EVIDENCE_REQUIRED")

    def test_memory_or_graph_manifest_drift_invalidates_capsule(self) -> None:
        self.build()
        memory = self.root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
        memory.write_text("version: 2\n", encoding="utf-8")
        stale_memory = tenor_decision.verify_capsule(self.root, "task-a", "agent-a", ["src/feature.py"])
        self.assertEqual(stale_memory["verdict"], "TENOR_DECISION_CAPSULE_STALE")
        self.assertIn("scribe_memory", stale_memory["stale_components"])

        memory.write_text("version: 1\n", encoding="utf-8")
        manifest = self.root / ".agent" / "state" / "outputs" / "graphify-out" / "GRAPHIFY_READY.json"
        manifest.write_text('{"state":"changed"}\n', encoding="utf-8")
        stale_graph = tenor_decision.verify_capsule(self.root, "task-a", "agent-a", ["src/feature.py"])
        self.assertIn("graphify_manifest", stale_graph["stale_components"])

    def test_capsule_cannot_be_reused_for_another_resource_or_after_resolution(self) -> None:
        self.build()
        wrong_resource = tenor_decision.verify_capsule(self.root, "task-a", "agent-a", ["src/other.py"])
        self.assertEqual(wrong_resource["verdict"], "TENOR_DECISION_RESOURCE_MISMATCH")
        resolved = tenor_decision.resolve_capsule(self.root, "task-a", "agent-a", "changeset-1")
        self.assertTrue(resolved["ok"], resolved)
        reused = tenor_decision.verify_capsule(self.root, "task-a", "agent-a", ["src/feature.py"])
        self.assertEqual(reused["verdict"], "TENOR_DECISION_CAPSULE_NOT_ACTIVE")

    def test_active_capsule_can_be_refreshed_in_place_with_new_evidence(self) -> None:
        first = self.build()
        self.memory = self.root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
        self.memory.write_text("version: 2\n", encoding="utf-8")
        refreshed = tenor_decision.build_capsule(
            project_root=self.root,
            task_id="task-a",
            agent_id="agent-a",
            objective="fix the production regression",
            intent="write",
            scope="src/feature.py",
            resources=["src/feature.py"],
            scribe_result={"ok": True, "verdict": "SCRIBE_QUERY_DONE", "result": {"stdout": "new relevant scar"}},
            graphify_result={"ok": True, "verdict": "GRAPHIFY_QUERY_DONE", "result": {"stdout": "updated impact"}},
            graphify_required=True,
            refresh_existing=True,
        )
        self.assertTrue(refreshed["ok"], refreshed)
        self.assertEqual(refreshed["verdict"], "TENOR_DECISION_CAPSULE_REFRESHED")
        self.assertNotEqual(refreshed["capsule_hash"], first["capsule_hash"])
        self.assertTrue(tenor_decision.verify_capsule(self.root, "task-a", "agent-a", ["src/feature.py"])["ok"])


if __name__ == "__main__":
    unittest.main()
