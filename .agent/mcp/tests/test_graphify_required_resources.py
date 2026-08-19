from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP_DIR = Path(__file__).resolve().parent.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import graphify_guard as guard
from runtime import graphify_readiness as readiness


class DiscoverSourcesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_counts_sources_under_bounded_root(self) -> None:
        (self.root / ".agent" / "mcp").mkdir(parents=True)
        (self.root / ".agent" / "mcp" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / ".agent" / "mcp" / "notes.txt").write_text("not source\n", encoding="utf-8")
        result = readiness.discover_sources(self.root, required_resources=[".agent/mcp"])
        self.assertEqual(result["discovered_candidate_count"], 1)
        self.assertEqual(result["indexed_paths"], [".agent/mcp/a.py"])

    def test_internal_agent_state_is_excluded(self) -> None:
        (self.root / ".agent" / "mcp").mkdir(parents=True)
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True)
        (self.root / ".agent" / "mcp" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / ".agent" / "state" / "runtime" / "secret.py").write_text("s = 1\n", encoding="utf-8")
        result = readiness.discover_sources(self.root, required_resources=[".agent"])
        self.assertIn(".agent/mcp/a.py", result["indexed_paths"])
        excluded = {item["path"] for item in result["excluded_paths"]}
        self.assertIn(".agent/state/runtime/secret.py", excluded)

    def test_resource_escaping_project_root_is_refused(self) -> None:
        result = readiness.discover_sources(self.root, required_resources=["../outside"])
        self.assertEqual(result["discovered_candidate_count"], 0)
        self.assertTrue(any(item["path"] == "../outside" for item in result["excluded_paths"]))

    def test_missing_resource_is_excluded_not_fatal(self) -> None:
        result = readiness.discover_sources(self.root, required_resources=[".agent/absent"])
        self.assertEqual(result["discovered_candidate_count"], 0)
        self.assertTrue(any(item["path"] == ".agent/absent" for item in result["excluded_paths"]))

    def test_symlink_inside_resource_is_refused(self) -> None:
        (self.root / ".agent").mkdir()
        (self.root / ".agent" / "real.py").write_text("r = 1\n", encoding="utf-8")
        (self.root / ".agent" / "link.py").symlink_to(self.root / ".agent" / "real.py")
        result = readiness.discover_sources(self.root, required_resources=[".agent"])
        excluded = {item["path"] for item in result["excluded_paths"]}
        self.assertIn(".agent/link.py", excluded)


class CheckGraphifyRequiredResourcesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        self.out = self.root / ".agent" / "state" / "outputs" / "graphify-out"
        self.out.mkdir(parents=True)
        (self.root / ".agent" / "mcp").mkdir(parents=True)
        (self.root / ".agent" / "mcp" / "a.py").write_text("x = 1\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_artifacts(self, graph: dict[str, object]) -> None:
        (self.out / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
        (self.out / "GRAPH_REPORT.md").write_text("# Graph Report\n", encoding="utf-8")
        (self.out / "graph.html").write_text("<html></html>\n", encoding="utf-8")

    def test_required_resources_with_empty_project_is_blocked(self) -> None:
        self._write_artifacts({"nodes": [], "edges": []})
        manifest = readiness.write_graphify_manifest(self.root, kind="empty_project", purpose="test")
        self.assertTrue(manifest.get("ok"), manifest)
        result = guard.check_graphify_required(self.root, required_resources=[".agent/mcp"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "GRAPHIFY_REQUIRED_RESOURCES_UNINDEXED")
        self.assertFalse(result["write_allowed"])

    def test_required_resources_with_real_graph_is_allowed(self) -> None:
        self._write_artifacts({"nodes": [{"id": "a"}], "edges": []})
        manifest = readiness.write_graphify_manifest(self.root, kind="real", purpose="test")
        self.assertTrue(manifest.get("ok"), manifest)
        result = guard.check_graphify_required(self.root, required_resources=[".agent/mcp"])
        self.assertTrue(result["ok"])
        self.assertTrue(result["write_allowed"])

    def test_required_resources_without_candidates_falls_back(self) -> None:
        result = guard.check_graphify_required(self.root, required_resources=["nonexistent-root"])
        self.assertNotEqual(result.get("verdict"), "GRAPHIFY_REQUIRED_RESOURCES_UNINDEXED")


if __name__ == "__main__":
    unittest.main()
