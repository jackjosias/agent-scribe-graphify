from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import installation_state
from runtime.state_paths import prepare_state_dirs

ENGINE_DIRS = ("mcp", "skills", "workflow")


def make_project(root: Path) -> None:
    (root / ".git").mkdir()
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("agents\n", encoding="utf-8")
    for name in ENGINE_DIRS:
        (root / ".agent" / name).mkdir(parents=True, exist_ok=True)
    (root / ".agent" / "mcp" / "server_entry.py").write_text("# marker\n", encoding="utf-8")


def write_legacy(path: Path, filename: str = "legacy.txt") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / filename).write_text(path.name + "\n", encoding="utf-8")


class GeneratedOutputHygieneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        make_project(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_01_prepare_state_dirs_returns_outputs(self) -> None:
        paths = prepare_state_dirs(self.root)
        self.assertIn("outputs", paths)
        self.assertEqual(paths["outputs"], self.root / ".agent" / "state" / "outputs")

    def test_02_scribe_out_is_under_state_outputs(self) -> None:
        paths = prepare_state_dirs(self.root)
        self.assertEqual(paths["scribe_out"], self.root / ".agent" / "state" / "outputs" / "scribe-out")

    def test_03_graphify_out_is_under_state_outputs(self) -> None:
        paths = prepare_state_dirs(self.root)
        self.assertEqual(paths["graphify_out"], self.root / ".agent" / "state" / "outputs" / "graphify-out")

    def test_04_prepare_state_dirs_does_not_create_root_scribe_out(self) -> None:
        prepare_state_dirs(self.root)
        self.assertFalse((self.root / "scribe-out").exists())

    def test_05_prepare_state_dirs_does_not_create_root_graphify_out(self) -> None:
        prepare_state_dirs(self.root)
        self.assertFalse((self.root / "graphify-out").exists())

    def test_06_legacy_root_scribe_out_is_migrated(self) -> None:
        write_legacy(self.root / "scribe-out")
        prepare_state_dirs(self.root)
        self.assertFalse((self.root / "scribe-out").exists())
        self.assertTrue((self.root / ".agent" / "state" / "outputs" / "scribe-out" / "legacy.txt").exists())

    def test_07_legacy_root_graphify_out_is_migrated(self) -> None:
        write_legacy(self.root / "graphify-out")
        prepare_state_dirs(self.root)
        self.assertFalse((self.root / "graphify-out").exists())
        self.assertTrue((self.root / ".agent" / "state" / "outputs" / "graphify-out" / "legacy.txt").exists())

    def test_08_legacy_agent_scribe_out_is_migrated(self) -> None:
        write_legacy(self.root / ".agent" / "scribe-out")
        prepare_state_dirs(self.root)
        self.assertTrue((self.root / ".agent" / "state" / "outputs" / "scribe-out" / "legacy.txt").exists())

    def test_09_legacy_agent_graphify_out_is_migrated(self) -> None:
        write_legacy(self.root / ".agent" / "graphify-out")
        prepare_state_dirs(self.root)
        self.assertTrue((self.root / ".agent" / "state" / "outputs" / "graphify-out" / "legacy.txt").exists())

    def test_10_legacy_state_scribe_out_is_migrated(self) -> None:
        write_legacy(self.root / ".agent" / "state" / "scribe-out")
        prepare_state_dirs(self.root)
        self.assertTrue((self.root / ".agent" / "state" / "outputs" / "scribe-out" / "legacy.txt").exists())

    def test_11_legacy_state_graphify_out_is_migrated(self) -> None:
        write_legacy(self.root / ".agent" / "state" / "graphify-out")
        prepare_state_dirs(self.root)
        self.assertTrue((self.root / ".agent" / "state" / "outputs" / "graphify-out" / "legacy.txt").exists())

    def test_12_symlink_legacy_output_is_not_followed(self) -> None:
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (self.root / "scribe-out").symlink_to(outside, target_is_directory=True)
        prepare_state_dirs(self.root)
        self.assertTrue((self.root / "scribe-out").is_symlink())
        self.assertTrue(outside.exists())

    def test_13_relocation_purge_removes_outputs(self) -> None:
        paths = prepare_state_dirs(self.root)
        (paths["outputs"] / "sentinel.txt").write_text("remove\n", encoding="utf-8")
        installation_state.purge_project_bound_state(self.root)
        self.assertFalse((self.root / ".agent" / "state" / "outputs").exists())

    def test_14_relocation_purge_preserves_mcp(self) -> None:
        prepare_state_dirs(self.root)
        installation_state.purge_project_bound_state(self.root)
        self.assertTrue((self.root / ".agent" / "mcp" / "server_entry.py").exists())

    def test_15_relocation_purge_preserves_skills(self) -> None:
        prepare_state_dirs(self.root)
        installation_state.purge_project_bound_state(self.root)
        self.assertTrue((self.root / ".agent" / "skills").is_dir())

    def test_16_relocation_purge_preserves_workflow(self) -> None:
        prepare_state_dirs(self.root)
        installation_state.purge_project_bound_state(self.root)
        self.assertTrue((self.root / ".agent" / "workflow").is_dir())

    def test_17_existing_destination_gets_suffix_not_overwrite(self) -> None:
        destination = self.root / ".agent" / "state" / "outputs" / "scribe-out"
        write_legacy(destination, "existing.txt")
        write_legacy(self.root / "scribe-out", "legacy.txt")
        prepare_state_dirs(self.root)
        self.assertTrue((destination / "existing.txt").exists())
        self.assertTrue((destination.with_name("scribe-out.legacy-1") / "legacy.txt").exists())

    def test_18_legacy_runtime_still_migrates_to_state_runtime(self) -> None:
        legacy_runtime = self.root / ".agent" / "runtime"
        write_legacy(legacy_runtime)
        prepare_state_dirs(self.root)
        self.assertFalse(legacy_runtime.exists())
        self.assertTrue((self.root / ".agent" / "state" / "runtime" / "legacy.txt").exists())


if __name__ == "__main__":
    unittest.main()
