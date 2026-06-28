from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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


class TenorInitOutputHygieneTests(unittest.TestCase):
    def assert_root_outputs_absent(self, root: Path) -> None:
        self.assertFalse((root / "scribe-out").exists(), "root scribe-out must not remain")
        self.assertFalse((root / "graphify-out").exists(), "root graphify-out must not remain")

    def test_tenor_init_and_rag_context_use_canonical_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copy_agent_bundle(root)

            tenor_init = run_command([".agent/workflow/scribe/scribe", "tenor-init", "--type", "cli"], root)
            self.assertEqual(tenor_init.returncode, 0, tenor_init.stderr + tenor_init.stdout)
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

            tenor_init = run_command([".agent/workflow/scribe/scribe", "tenor-init", "--type", "cli"], root)
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

            tenor_init = run_command([".agent/workflow/scribe/scribe", "tenor-init", "--type", "cli"], root)
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

            tenor_init = run_command([".agent/workflow/scribe/scribe", "tenor-init", "--type", "cli"], root)
            self.assertEqual(tenor_init.returncode, 0, tenor_init.stderr + tenor_init.stdout)
            self.assertFalse(legacy.exists())
            self.assertEqual((canonical / "same.txt").read_text(encoding="utf-8"), "canonical\n")
            migrated = list((canonical / "_legacy_migrated").rglob("same.txt"))
            self.assertEqual(len(migrated), 1)
            self.assertEqual(migrated[0].read_text(encoding="utf-8"), "legacy\n")


if __name__ == "__main__":
    sys.exit(unittest.main())
