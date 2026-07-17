from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[5]
AGENT_ROOT = REPO_ROOT / ".agent"


def run_command(args: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=timeout)


def copy_agent_bundle(target: Path) -> None:
    shutil.copytree(
        AGENT_ROOT,
        target / ".agent",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache", "state"),
    )


def run_tenor_init(root: Path) -> subprocess.CompletedProcess[str]:
    command = [".agent/workflow/scribe/scribe", "tenor-init", "--type", "cli", "--host", "opencode"]
    first = run_command(command, root)
    if first.returncode == 76:
        return run_command(command, root)
    return first


class TenorInitOutputHygieneTests(unittest.TestCase):
    def assert_root_outputs_absent(self, root: Path) -> None:
        self.assertFalse((root / "scribe-out").exists(), "root scribe-out must not remain")
        self.assertFalse((root / "graphify-out").exists(), "root graphify-out must not remain")

    def test_tenor_init_and_rag_context_use_canonical_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copy_agent_bundle(root)

            tenor_init = run_tenor_init(root)
            self.assertEqual(tenor_init.returncode, 0, tenor_init.stderr + tenor_init.stdout)
            self.assertIn("Proof receipt        : SERVER_SIDE_ONE_TIME_READY", tenor_init.stdout)
            self.assertIn("Status init          : LOCAL_VALID_HOST_UNBOUND", tenor_init.stdout)
            self.assertIn("MCP local server     : READY", tenor_init.stdout)
            self.assertIn("TENOR_INIT_LOCAL_MCP_READY tools=9", tenor_init.stdout)
            self.assertIn("Init status          : LOCAL_INIT_READY_HOST_MCP_UNBOUND", tenor_init.stdout)
            self.assertIn("TENOR_INIT_TERMINAL=false", tenor_init.stdout)
            self.assertIn("TENOR_INIT_NEXT_TOOL=tenor_init_bridge", tenor_init.stdout)
            self.assertIn("TENOR_INIT_RESPONSE_POLICY=CONTINUE_WITHOUT_USER_RESPONSE", tenor_init.stdout)
            self.assertIn("Contexte ciblé       : DEFERRED_TO_TENOR_TASK_START", tenor_init.stdout)
            self.assertNotIn("TENOR_INIT_STAGE load_scribe_and_graphify_context", tenor_init.stdout)
            self.assertNotIn("scribe-rag query", tenor_init.stdout)
            self.assertNotIn("Proof token", tenor_init.stdout + tenor_init.stderr)
            self.assertNotIn("v1.", tenor_init.stdout + tenor_init.stderr)
            self.assert_root_outputs_absent(root)

            scribe_out = root / ".agent" / "state" / "outputs" / "scribe-out"
            graphify_out = root / ".agent" / "state" / "outputs" / "graphify-out"
            self.assertTrue((scribe_out / "state.json").is_file())
            self.assertTrue((scribe_out / "scribe-doctor-report.md").is_file())
            self.assertTrue((graphify_out / "GRAPH_REPORT.md").is_file())

            rag_context = run_command([".agent/workflow/scribe/scribe-rag", "context"], root)
            self.assertEqual(rag_context.returncode, 0, rag_context.stderr + rag_context.stdout)
            self.assertTrue((scribe_out / "rag-index.json").is_file())
            self.assert_root_outputs_absent(root)

    def test_v216_init_defers_heavy_rag_queries_to_tenor_task(self) -> None:
        source = (
            AGENT_ROOT / "workflow" / "scribe" / "sel" / "scripts" / "scribe_tenor_init_v216.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('run_command((rag, "query"', source)
        self.assertNotIn("load_scribe_and_graphify_context", source)

    def test_legacy_root_outputs_are_migrated_without_dangerous_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copy_agent_bundle(root)
            legacy_scribe = root / "scribe-out"
            legacy_graphify = root / "graphify-out"
            legacy_scribe.mkdir()
            legacy_graphify.mkdir()
            (legacy_scribe / "legacy-note.txt").write_text("keep me\n", encoding="utf-8")
            (legacy_graphify / "legacy-graph.json").write_text("{}\n", encoding="utf-8")

            tenor_init = run_tenor_init(root)
            self.assertEqual(tenor_init.returncode, 0, tenor_init.stderr + tenor_init.stdout)
            self.assert_root_outputs_absent(root)

            outputs = root / ".agent" / "state" / "outputs"
            self.assertEqual((outputs / "scribe-out" / "legacy-note.txt").read_text(encoding="utf-8"), "keep me\n")
            self.assertEqual((outputs / "graphify-out" / "legacy-graph.json").read_text(encoding="utf-8"), "{}\n")
            self.assertTrue((outputs / "scribe-out" / "state.json").is_file())
            self.assertTrue((outputs / "graphify-out" / "GRAPH_REPORT.md").is_file())


    def test_output_paths_match_mcp_runtime_state_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copy_agent_bundle(root)
            sys.path.insert(0, str(root / ".agent" / "workflow" / "scribe" / "sel" / "scripts"))
            sys.path.insert(0, str(root / ".agent" / "mcp" / "runtime"))
            try:
                from scribe_output_paths import graphify_out_dir, scribe_out_dir
                from state_paths import prepare_state_dirs

                runtime_paths = prepare_state_dirs(root)
                self.assertEqual(scribe_out_dir(root), runtime_paths["scribe_out"])
                self.assertEqual(graphify_out_dir(root), runtime_paths["graphify_out"])
            finally:
                sys.path = [entry for entry in sys.path if entry not in {str(root / ".agent" / "workflow" / "scribe" / "sel" / "scripts"), str(root / ".agent" / "mcp" / "runtime")}]

    def test_legacy_root_output_symlinks_are_not_followed_or_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copy_agent_bundle(root)
            outside = root / "outside-target"
            outside.mkdir()
            (outside / "must-stay.txt").write_text("untouched\n", encoding="utf-8")
            (root / "scribe-out").symlink_to(outside, target_is_directory=True)
            (root / "graphify-out").symlink_to(outside, target_is_directory=True)

            tenor_init = run_tenor_init(root)
            self.assertEqual(tenor_init.returncode, 0, tenor_init.stderr + tenor_init.stdout)
            self.assertTrue((root / "scribe-out").is_symlink())
            self.assertTrue((root / "graphify-out").is_symlink())
            self.assertEqual((outside / "must-stay.txt").read_text(encoding="utf-8"), "untouched\n")
            self.assertTrue((root / ".agent" / "state" / "outputs" / "scribe-out" / "state.json").is_file())

    def test_existing_canonical_destination_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copy_agent_bundle(root)
            canonical = root / ".agent" / "state" / "outputs" / "scribe-out"
            canonical.mkdir(parents=True)
            (canonical / "same.txt").write_text("canonical\n", encoding="utf-8")
            legacy = root / "scribe-out"
            legacy.mkdir()
            (legacy / "same.txt").write_text("legacy\n", encoding="utf-8")

            tenor_init = run_tenor_init(root)
            self.assertEqual(tenor_init.returncode, 0, tenor_init.stderr + tenor_init.stdout)
            self.assertFalse(legacy.exists())
            self.assertEqual((canonical / "same.txt").read_text(encoding="utf-8"), "canonical\n")
            migrated = list((canonical / "_legacy_migrated").rglob("same.txt"))
            self.assertEqual(len(migrated), 1)
            self.assertEqual(migrated[0].read_text(encoding="utf-8"), "legacy\n")

    def test_project_graph_build_never_materializes_root_graphify_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copy_agent_bundle(root)
            (root / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
            scripts = root / ".agent" / "workflow" / "scribe" / "sel" / "scripts"
            sys.path.insert(0, str(scripts))
            try:
                import scribe_bundle_graph

                observed_targets: list[Path] = []

                def fake_graphify(target: Path, **_: object) -> subprocess.CompletedProcess[str]:
                    target_path = Path(target)
                    observed_targets.append(target_path)
                    self.assertNotEqual(target_path, Path("."), "project build must run in an isolated mirror")
                    out = target_path / "graphify-out"
                    out.mkdir(parents=True)
                    (out / "graph.json").write_text(
                        '{"nodes":[{"id":"answer"}],"edges":[]}\n', encoding="utf-8"
                    )
                    (out / "GRAPH_REPORT.md").write_text("# Graph\n", encoding="utf-8")
                    (out / "graph.html").write_text("<html></html>\n", encoding="utf-8")
                    (out / ".graphify_root").write_text(str(target_path), encoding="utf-8")
                    return subprocess.CompletedProcess(["graphify"], 0, stdout="ok\n")

                with mock.patch.object(scribe_bundle_graph, "PROJECT_ROOT", root), mock.patch.object(
                    scribe_bundle_graph, "run_graphify_update", side_effect=fake_graphify
                ):
                    status = scribe_bundle_graph.build_project_graph(timeout=30)

                self.assertEqual(status, 0)
                self.assertEqual(len(observed_targets), 1)
                self.assertFalse((root / "graphify-out").exists())
                canonical = root / ".agent" / "state" / "outputs" / "graphify-out"
                self.assertTrue((canonical / "graph.json").is_file())
                self.assertEqual((canonical / ".graphify_root").read_text(encoding="utf-8"), str(root))
            finally:
                sys.path = [entry for entry in sys.path if entry != str(scripts)]


if __name__ == "__main__":
    sys.exit(unittest.main())
