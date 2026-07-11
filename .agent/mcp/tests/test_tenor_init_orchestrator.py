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

from runtime import installation_state, tenor_init_orchestrator as orchestrator


def make_project(root: Path, *, memory: str | None = "project-memory\n") -> None:
    (root / ".agent" / "mcp").mkdir(parents=True)
    (root / ".agent" / "mcp" / "server_entry.py").write_text("# marker\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("agents\n", encoding="utf-8")
    if memory is not None:
        (root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").write_text(memory, encoding="utf-8")


class TenorInitOrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_new_installation_is_decided_before_memory_adoption(self) -> None:
        root = self.base / "new-with-existing-memory"
        root.mkdir()
        make_project(root, memory="existing-project-history\n")

        plan = orchestrator.prepare_tenor_init(root)

        self.assertEqual(plan.classification, orchestrator.TENOR_INIT_NEW_INSTALLATION)
        self.assertTrue(plan.project_changed)
        self.assertEqual(plan.memory_action, orchestrator.SCRIBE_MEMORY_ADOPT)
        self.assertEqual(
            (root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").read_text(encoding="utf-8"),
            "existing-project-history\n",
        )

    def test_second_init_same_project_preserves_runtime(self) -> None:
        root = self.base / "same-project"
        root.mkdir()
        make_project(root)
        orchestrator.prepare_tenor_init(root)
        sentinel = root / ".agent" / "state" / "runtime" / "active-agents.sentinel"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("six agents may coexist\n", encoding="utf-8")

        plan = orchestrator.prepare_tenor_init(root)

        self.assertEqual(plan.classification, orchestrator.TENOR_INIT_SAME_PROJECT)
        self.assertFalse(plan.project_changed)
        self.assertFalse(plan.purge_executed)
        self.assertTrue(sentinel.exists())

    def test_relocation_purges_old_state_but_adopts_target_memory(self) -> None:
        source = self.base / "project-a"
        source.mkdir()
        make_project(source, memory="memory-a\n")
        orchestrator.prepare_tenor_init(source)
        old_state = source / ".agent" / "state" / "runtime" / "old-agent.txt"
        old_state.parent.mkdir(parents=True, exist_ok=True)
        old_state.write_text("agent-from-a\n", encoding="utf-8")

        target = self.base / "project-b"
        target.mkdir()
        make_project(target, memory="memory-b-must-survive\n")
        shutil.rmtree(target / ".agent")
        shutil.copytree(source / ".agent", target / ".agent")

        plan = orchestrator.prepare_tenor_init(target)

        self.assertEqual(plan.classification, orchestrator.TENOR_INIT_RELOCATED_PROJECT)
        self.assertTrue(plan.project_changed)
        self.assertTrue(plan.relocated)
        self.assertTrue(plan.purge_executed)
        self.assertFalse((target / ".agent" / "state" / "runtime" / "old-agent.txt").exists())
        self.assertEqual(plan.memory_action, orchestrator.SCRIBE_MEMORY_ADOPT)
        self.assertEqual(
            (target / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").read_text(encoding="utf-8"),
            "memory-b-must-survive\n",
        )

    def test_relocation_without_target_memory_requests_creation(self) -> None:
        source = self.base / "source"
        source.mkdir()
        make_project(source)
        orchestrator.prepare_tenor_init(source)

        target = self.base / "target-without-memory"
        target.mkdir()
        make_project(target, memory=None)
        shutil.rmtree(target / ".agent")
        shutil.copytree(source / ".agent", target / ".agent")

        plan = orchestrator.prepare_tenor_init(target)

        self.assertEqual(plan.classification, orchestrator.TENOR_INIT_RELOCATED_PROJECT)
        self.assertEqual(plan.memory_action, orchestrator.SCRIBE_MEMORY_CREATE)
        self.assertFalse((target / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").exists())

    def test_shared_init_lock_serializes_bootstrap(self) -> None:
        root = self.base / "lock-project"
        root.mkdir()
        make_project(root)

        with orchestrator.tenor_init_lock(root, wait_timeout_seconds=0.0):
            with self.assertRaises(orchestrator.TenorInitBusy):
                orchestrator.acquire_tenor_init_lock(root, wait_timeout_seconds=0.0)

        lock = orchestrator.acquire_tenor_init_lock(root, wait_timeout_seconds=0.0)
        orchestrator.release_tenor_init_lock(lock)
        self.assertFalse((root / orchestrator.LOCK_RELATIVE).exists())

    def test_six_sequential_init_sessions_never_purge_same_project_runtime(self) -> None:
        root = self.base / "six-terminals"
        root.mkdir()
        make_project(root)
        orchestrator.prepare_tenor_init(root)
        shared_runtime = root / ".agent" / "state" / "runtime" / "coordination.sqlite"
        shared_runtime.parent.mkdir(parents=True, exist_ok=True)
        shared_runtime.write_bytes(b"shared-agent-runtime")

        for _ in range(6):
            plan = orchestrator.prepare_tenor_init(root)
            self.assertEqual(plan.classification, orchestrator.TENOR_INIT_SAME_PROJECT)
            self.assertFalse(plan.purge_executed)
            self.assertEqual(shared_runtime.read_bytes(), b"shared-agent-runtime")

    def test_orchestrator_uses_installation_manifest_not_scribe_presence(self) -> None:
        root = self.base / "manifest-authority"
        root.mkdir()
        make_project(root, memory=None)
        first = orchestrator.prepare_tenor_init(root)
        self.assertEqual(first.classification, orchestrator.TENOR_INIT_NEW_INSTALLATION)
        self.assertEqual(first.memory_action, orchestrator.SCRIBE_MEMORY_CREATE)

        # Still no SCRIBE file, but the installation manifest now proves this is
        # the same project. Absence of memory must not reclassify the project.
        second = orchestrator.prepare_tenor_init(root)
        self.assertEqual(second.classification, orchestrator.TENOR_INIT_SAME_PROJECT)
        self.assertEqual(second.memory_action, orchestrator.SCRIBE_MEMORY_CREATE)


if __name__ == "__main__":
    unittest.main()
