from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import installation_state


def make_project(root: Path) -> None:
    (root / ".agent" / "mcp").mkdir(parents=True)
    (root / ".agent" / "mcp" / "server_entry.py").write_text("# marker\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("agents\n", encoding="utf-8")
    (root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").write_text("version: 1\n", encoding="utf-8")


class InstallationOutputPreservationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        make_project(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @property
    def state(self) -> Path:
        return self.root / ".agent" / "state"

    @property
    def outputs(self) -> Path:
        return self.state / "outputs"

    def write_runtime_and_outputs(self) -> None:
        runtime = self.state / "runtime"
        proof = self.state / "proof"
        locks = self.state / "locks"
        runtime.mkdir(parents=True)
        proof.mkdir(parents=True)
        locks.mkdir(parents=True)
        (runtime / "coordination.sqlite").write_bytes(b"old-runtime")
        (proof / "proof.json").write_text('{"token":"old"}\n', encoding="utf-8")
        (locks / "active.lock").write_text("old-lock\n", encoding="utf-8")

        scribe = self.outputs / "scribe-out" / "nested"
        graphify = self.outputs / "graphify-out" / "cache" / "ast"
        scribe.mkdir(parents=True)
        graphify.mkdir(parents=True)
        (scribe / "memory.json").write_bytes(b"canonical-output")
        (graphify / "graph.bin").write_bytes(b"graph-output")

    def test_purge_removes_project_runtime_but_preserves_nested_outputs_byte_for_byte(self) -> None:
        self.write_runtime_and_outputs()
        scribe_before = (self.outputs / "scribe-out" / "nested" / "memory.json").read_bytes()
        graph_before = (self.outputs / "graphify-out" / "cache" / "ast" / "graph.bin").read_bytes()

        result = installation_state.purge_project_bound_state(self.root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["verdict"], installation_state.PROJECT_BOUND_STATE_PURGED)
        self.assertEqual(len(result["preserved_state_dirs"]), 1)
        self.assertEqual(tuple(Path(result["preserved_state_dirs"][0]).parts), (".agent", "state", "outputs"))
        self.assertFalse((self.state / "runtime").exists())
        self.assertFalse((self.state / "proof").exists())
        self.assertFalse((self.state / "locks").exists())
        self.assertTrue((self.state / "install").is_dir())
        self.assertEqual((self.outputs / "scribe-out" / "nested" / "memory.json").read_bytes(), scribe_before)
        self.assertEqual((self.outputs / "graphify-out" / "cache" / "ast" / "graph.bin").read_bytes(), graph_before)

    def test_legacy_prepare_preserves_canonical_outputs_while_refreshing_runtime(self) -> None:
        self.write_runtime_and_outputs()

        result = installation_state.ensure_fresh_installation_state(self.root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["verdict"], installation_state.LEGACY_STATE_WITHOUT_INSTALL_MANIFEST_PURGED)
        self.assertEqual((self.outputs / "scribe-out" / "nested" / "memory.json").read_bytes(), b"canonical-output")
        self.assertEqual((self.outputs / "graphify-out" / "cache" / "ast" / "graph.bin").read_bytes(), b"graph-output")
        self.assertFalse((self.state / "runtime").exists())
        self.assertTrue((self.state / "install" / "agent-installation.json").is_file())

    def test_unsafe_outputs_symlink_refuses_before_partial_purge(self) -> None:
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "must-stay.txt").write_text("untouched\n", encoding="utf-8")
        runtime = self.state / "runtime"
        runtime.mkdir(parents=True)
        (runtime / "must-also-stay.txt").write_text("runtime\n", encoding="utf-8")
        try:
            self.outputs.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        result = installation_state.purge_project_bound_state(self.root, attempts=1)

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["verdict"], installation_state.PROJECT_BOUND_STATE_PURGE_REFUSED)
        self.assertTrue((outside / "must-stay.txt").is_file())
        self.assertTrue((runtime / "must-also-stay.txt").is_file(), "unsafe preserve validation must precede deletion")


if __name__ == "__main__":
    unittest.main()
