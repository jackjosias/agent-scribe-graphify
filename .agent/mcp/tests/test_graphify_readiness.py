from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import graphify_guard as guard
from runtime import graphify_readiness as readiness
from runtime import owned_file_lock as owned_locks


class GraphifyReadinessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        (self.root / ".agent" / "state" / "outputs" / "graphify-out").mkdir(parents=True)

    def tearDown(self) -> None:
        os.environ.pop(readiness.FIXTURE_ENV, None)
        self.tmp.cleanup()

    @property
    def out(self) -> Path:
        return self.root / ".agent" / "state" / "outputs" / "graphify-out"

    def snapshot_out(self) -> dict[str, bytes]:
        if not self.out.exists():
            return {}
        return {
            path.relative_to(self.out).as_posix(): path.read_bytes()
            for path in sorted(self.out.rglob("*"))
            if path.is_file()
        }

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

    def test_smoke_fixture_scope_removes_fixture_when_output_was_absent(self) -> None:
        shutil.rmtree(self.out)
        self.assertNotIn(readiness.FIXTURE_ENV, os.environ)
        with readiness.smoke_fixture_scope(self.root):
            self.assertTrue(self.out.is_dir())
            self.assertEqual(os.environ.get(readiness.FIXTURE_ENV), "1")
            self.assertEqual(
                readiness.inspect_graphify_readiness(self.root).verdict,
                readiness.GRAPHIFY_TEST_FIXTURE_READY,
            )
        self.assertFalse(self.out.exists())
        self.assertNotIn(readiness.FIXTURE_ENV, os.environ)

    def test_smoke_fixture_scope_restores_real_graph_exactly(self) -> None:
        self.write_node_link_real()
        self.assertTrue(readiness.write_graphify_manifest(self.root)["ok"])
        (self.out / "cache" / "ast").mkdir(parents=True)
        (self.out / "cache" / "ast" / "sentinel.bin").write_bytes(b"real-cache\x00\xff")
        before = self.snapshot_out()
        with readiness.smoke_fixture_scope(self.root):
            self.assertEqual(
                readiness.inspect_graphify_readiness(self.root).verdict,
                readiness.GRAPHIFY_TEST_FIXTURE_READY,
            )
        self.assertEqual(self.snapshot_out(), before)
        self.assertEqual(readiness.inspect_graphify_readiness(self.root).verdict, readiness.GRAPHIFY_READY)

    def test_smoke_fixture_scope_restores_state_and_env_after_exception(self) -> None:
        self.write_real()
        self.assertTrue(readiness.write_graphify_manifest(self.root)["ok"])
        before = self.snapshot_out()
        os.environ[readiness.FIXTURE_ENV] = "previous-value"
        with self.assertRaisesRegex(RuntimeError, "intentional smoke failure"):
            with readiness.smoke_fixture_scope(self.root):
                raise RuntimeError("intentional smoke failure")
        self.assertEqual(os.environ.get(readiness.FIXTURE_ENV), "previous-value")
        self.assertEqual(self.snapshot_out(), before)

    def test_empty_project_placeholder_can_be_bound(self) -> None:
        (self.out / "graph.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        (self.out / "GRAPH_REPORT.md").write_text("# Graph Report\n\nBootstrap placeholder: no application graph has been built yet.\n", encoding="utf-8")
        (self.out / "graph.html").write_text("<html></html>", encoding="utf-8")
        readiness.write_graphify_manifest(self.root, kind="empty_project")
        result = readiness.inspect_graphify_readiness(self.root)
        self.assertTrue(result.ok)
        self.assertEqual(result.verdict, readiness.GRAPHIFY_EMPTY_PROJECT_READY)


class AtomicWriterHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        (self.root / ".agent" / "state" / "outputs" / "graphify-out").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _capture_mkstemp(self, captured: dict) -> object:
        real_mkstemp = tempfile.mkstemp

        def fake_mkstemp(*args, **kwargs):
            captured["dir"] = kwargs.get("dir")
            captured["prefix"] = kwargs.get("prefix")
            captured["suffix"] = kwargs.get("suffix")
            fd, name = real_mkstemp(*args, **kwargs)
            captured["tmp_name"] = name
            return fd, name

        return fake_mkstemp

    def test_atomic_json_uses_exclusive_sibling_temp(self) -> None:
        target = self.root / ".agent" / "state" / "outputs" / "graphify-out" / "manifest.json"
        captured: dict[str, object] = {}
        payload = {"schema": "graphify_manifest_v1", "verdict": "GRAPHIFY_OUTPUTS_READY"}
        with mock.patch.object(tempfile, "mkstemp", self._capture_mkstemp(captured)):
            readiness._atomic_json(target, payload)
        self.assertEqual(captured["dir"], str(target.parent))
        self.assertTrue(str(captured["prefix"]).startswith(f".{target.name}."))
        self.assertEqual(captured["suffix"], ".tmp")
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), payload)

    def test_atomic_json_concurrent_no_orphans(self) -> None:
        target = self.root / ".agent" / "state" / "outputs" / "graphify-out" / "manifest.json"
        marker = "Y" * 4096

        def write(n: int) -> None:
            readiness._atomic_json(target, {"n": n, "marker": marker})

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(write, range(64)))
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(data["marker"], marker)
        self.assertEqual(len(data["marker"]), 4096)
        orphans = [p for p in target.parent.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(orphans, [])
        self.assertFalse(target.with_name(f".{target.name}.publish.lock").exists())
        self.assertEqual(owned_locks._thread_lock_registry_size(), 0)

    def test_atomic_replace_permission_retry_is_bounded(self) -> None:
        attempts = 0

        def always_denied(_src: str, _dst: str) -> None:
            nonlocal attempts
            attempts += 1
            raise PermissionError("sharing violation")

        with mock.patch.object(os, "replace", always_denied), mock.patch.object(
            time,
            "sleep",
        ) as sleeper:
            with self.assertRaises(PermissionError):
                readiness._atomic_replace("source.tmp", Path("destination.json"))
        self.assertEqual(attempts, 10)
        self.assertEqual(sleeper.call_count, 9)
        self.assertTrue(all(call.args[0] <= 0.25 for call in sleeper.call_args_list))

    def test_owned_lock_release_retries_exact_nonce(self) -> None:
        lock_path = self.root / "publish.lock"
        observed = {"nonce": "nonce-a"}
        lock_path.write_text(json.dumps(observed), encoding="utf-8")
        real_unlink = Path.unlink
        attempts = 0

        def flaky_unlink(candidate: Path, *args: object, **kwargs: object) -> None:
            nonlocal attempts
            if candidate == lock_path:
                attempts += 1
                if attempts < 3:
                    raise PermissionError("sharing violation")
            real_unlink(candidate, *args, **kwargs)

        with mock.patch.object(Path, "unlink", flaky_unlink), mock.patch.object(
            owned_locks.time,
            "sleep",
        ) as sleeper:
            self.assertTrue(owned_locks._remove_exact_lock(lock_path, observed))
        self.assertEqual(attempts, 3)
        self.assertEqual(sleeper.call_count, 2)
        self.assertFalse(lock_path.exists())

    def test_write_doc_atomic_uses_exclusive_sibling_temp(self) -> None:
        target = self.root / "GRAPHIFY_INSTALL_GUIDE.md"
        captured: dict[str, object] = {}
        content = "install guide\n" + "Z" * 2048 + "\n"
        with mock.patch.object(tempfile, "mkstemp", self._capture_mkstemp(captured)):
            result = guard._write_doc_atomic(target, content)
        self.assertTrue(result["ok"])
        self.assertEqual(captured["dir"], str(target.parent))
        self.assertTrue(str(captured["prefix"]).startswith(f".{target.name}."))
        self.assertEqual(captured["suffix"], ".tmp")
        self.assertEqual(target.read_text(encoding="utf-8"), content)

    def test_write_doc_atomic_concurrent_no_orphans(self) -> None:
        target = self.root / "GRAPHIFY_INSTALL_GUIDE.md"
        marker = "W" * 4096

        def write(n: int) -> None:
            guard._write_doc_atomic(target, f"guide {n}\n{marker}\n")

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(write, range(64)))
        text = target.read_text(encoding="utf-8")
        self.assertIn(marker, text)
        orphans = [p for p in target.parent.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(orphans, [])


if __name__ == "__main__":
    unittest.main()
