from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import graphify_readiness as readiness


class GraphifyReadinessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        (self.root / ".agent" / "state" / "outputs" / "graphify-out").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @property
    def out(self) -> Path:
        return self.root / ".agent" / "state" / "outputs" / "graphify-out"

    def write_graph(self, graph: dict[str, object]) -> None:
        (self.root / "app.py").write_text("print('ok')\n", encoding="utf-8")
        (self.out / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
        (self.out / "GRAPH_REPORT.md").write_text("# Graph Report\nNodes: 1\nEdges: 0\n", encoding="utf-8")
        (self.out / "graph.html").write_text("<html></html>\n", encoding="utf-8")

    def write_real(self) -> None:
        self.write_graph({"nodes": [{"id": "app"}], "edges": []})

    def write_node_link_real(self) -> None:
        self.write_graph(
            {
                "directed": False,
                "multigraph": False,
                "graph": {},
                "nodes": [{"id": "app"}],
                "links": [{"source": "app", "target": "app", "relation": "self"}],
                "hyperedges": [],
            }
        )

    def test_missing_outputs(self) -> None:
        result = readiness.inspect_graphify_readiness(self.root)
        self.assertFalse(result.ok)
        self.assertEqual(result.verdict, readiness.GRAPHIFY_MISSING)

    def test_smoke_stub_without_manifest_is_invalid(self) -> None:
        (self.out / "graph.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        (self.out / "GRAPH_REPORT.md").write_text("# Smoke stub Graph Report\n", encoding="utf-8")
        (self.out / "graph.html").write_text("<html></html>", encoding="utf-8")
        result = readiness.inspect_graphify_readiness(self.root)
        self.assertEqual(result.verdict, readiness.GRAPHIFY_STUB_INVALID)

    def test_corrupt_graph_is_rejected(self) -> None:
        (self.out / "graph.json").write_text("not json", encoding="utf-8")
        (self.out / "GRAPH_REPORT.md").write_text("# Graph Report\n", encoding="utf-8")
        (self.out / "graph.html").write_text("<html></html>", encoding="utf-8")
        self.assertEqual(readiness.inspect_graphify_readiness(self.root).verdict, readiness.GRAPHIFY_CORRUPT)

    def test_real_graph_requires_manifest(self) -> None:
        self.write_real()
        self.assertEqual(readiness.inspect_graphify_readiness(self.root).verdict, readiness.GRAPHIFY_LEGACY_UNBOUND)

    def test_real_graph_bound_to_current_workspace_is_ready(self) -> None:
        self.write_real()
        self.assertTrue(readiness.write_graphify_manifest(self.root)["ok"])
        result = readiness.inspect_graphify_readiness(self.root)
        self.assertTrue(result.ok)
        self.assertEqual(result.verdict, readiness.GRAPHIFY_READY)
        self.assertEqual(result.node_count, 1)
        self.assertEqual(result.edge_count, 0)

    def test_node_link_graph_bound_to_current_workspace_is_ready(self) -> None:
        self.write_node_link_real()
        manifest = readiness.write_graphify_manifest(self.root)
        self.assertTrue(manifest["ok"])
        self.assertEqual(manifest["manifest"]["edge_field"], "links")
        result = readiness.inspect_graphify_readiness(self.root)
        self.assertTrue(result.ok)
        self.assertEqual(result.verdict, readiness.GRAPHIFY_READY)
        self.assertEqual(result.node_count, 1)
        self.assertEqual(result.edge_count, 1)

    def test_non_list_links_are_rejected(self) -> None:
        self.write_graph({"nodes": [{"id": "app"}], "links": {"bad": "shape"}})
        result = readiness.inspect_graphify_readiness(self.root)
        self.assertFalse(result.ok)
        self.assertEqual(result.verdict, readiness.GRAPHIFY_CORRUPT)
        self.assertIn("links", result.reason)
        self.assertIn("must be lists", result.reason)

    def test_conflicting_edges_and_links_are_rejected(self) -> None:
        self.write_graph(
            {
                "nodes": [{"id": "app"}],
                "edges": [],
                "links": [{"source": "app", "target": "app"}],
            }
        )
        result = readiness.inspect_graphify_readiness(self.root)
        self.assertFalse(result.ok)
        self.assertEqual(result.verdict, readiness.GRAPHIFY_CORRUPT)
        self.assertIn("contradictory", result.reason)

    def test_equal_edges_and_links_are_accepted(self) -> None:
        edge = {"source": "app", "target": "app"}
        self.write_graph({"nodes": [{"id": "app"}], "edges": [edge], "links": [edge]})
        manifest = readiness.write_graphify_manifest(self.root)
        self.assertTrue(manifest["ok"])
        self.assertEqual(manifest["manifest"]["edge_field"], "edges+links")

    def test_changed_source_makes_graph_stale(self) -> None:
        self.write_real()
        readiness.write_graphify_manifest(self.root)
        (self.root / "app.py").write_text("print('changed')\n", encoding="utf-8")
        result = readiness.inspect_graphify_readiness(self.root)
        self.assertEqual(result.verdict, readiness.GRAPHIFY_STALE_WORKSPACE)

    def test_manifest_from_another_root_is_rejected(self) -> None:
        self.write_real()
        readiness.write_graphify_manifest(self.root)
        manifest = readiness.manifest_path(self.root)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["project_root"] = str(self.root.parent / "other")
        manifest.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(readiness.inspect_graphify_readiness(self.root).verdict, readiness.GRAPHIFY_STALE_ROOT)

    def test_smoke_fixture_is_forbidden_by_default(self) -> None:
        readiness.write_smoke_fixture(self.root)
        self.assertEqual(readiness.inspect_graphify_readiness(self.root).verdict, readiness.GRAPHIFY_FIXTURE_FORBIDDEN)

    def test_smoke_fixture_requires_explicit_allowance(self) -> None:
        readiness.write_smoke_fixture(self.root)
        result = readiness.inspect_graphify_readiness(self.root, allow_fixture=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.verdict, readiness.GRAPHIFY_TEST_FIXTURE_READY)

    def test_empty_project_placeholder_can_be_bound(self) -> None:
        (self.out / "graph.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        (self.out / "GRAPH_REPORT.md").write_text("# Graph Report\n\nBootstrap placeholder: no application graph has been built yet.\n", encoding="utf-8")
        (self.out / "graph.html").write_text("<html></html>", encoding="utf-8")
        readiness.write_graphify_manifest(self.root, kind="empty_project")
        result = readiness.inspect_graphify_readiness(self.root)
        self.assertTrue(result.ok)
        self.assertEqual(result.verdict, readiness.GRAPHIFY_EMPTY_PROJECT_READY)


if __name__ == "__main__":
    unittest.main()
