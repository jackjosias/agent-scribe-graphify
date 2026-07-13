from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

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
        self.assertEqual((root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").read_text(encoding="utf-8"), "existing-project-history\n")
        self.assertFalse(installation_state.inspect_installation_state(root)["ready"])
        finalized = orchestrator.finalize_tenor_init(root)
        self.assertTrue(finalized["ok"])
        self.assertTrue(installation_state.inspect_installation_state(root)["ready"])

    def test_second_init_same_project_preserves_runtime(self) -> None:
        root = self.base / "same-project"
        root.mkdir()
        make_project(root)
        orchestrator.prepare_tenor_init(root)
        orchestrator.finalize_tenor_init(root)
        sentinel = root / ".agent" / "state" / "runtime" / "active-agents.sentinel"
        sentinel.parent.mkdir(parents=True)
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
        orchestrator.finalize_tenor_init(source)
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
        self.assertTrue(plan.relocated)
        self.assertTrue(plan.purge_executed)
        self.assertFalse((target / ".agent" / "state" / "runtime" / "old-agent.txt").exists())
        self.assertEqual(plan.memory_action, orchestrator.SCRIBE_MEMORY_ADOPT)
        self.assertEqual((target / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").read_text(encoding="utf-8"), "memory-b-must-survive\n")

    def test_relocation_without_target_memory_requests_creation(self) -> None:
        source = self.base / "source"
        source.mkdir()
        make_project(source)
        orchestrator.prepare_tenor_init(source)
        orchestrator.finalize_tenor_init(source)
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

    def test_pid_probe_reports_current_process_alive_without_side_effects(self) -> None:
        current_pid = os.getpid()
        self.assertTrue(orchestrator._pid_is_alive(current_pid))
        self.assertEqual(os.getpid(), current_pid)

    def test_live_owner_lock_is_not_stolen_even_when_old(self) -> None:
        root = self.base / "live-lock"
        root.mkdir()
        make_project(root)
        lock = orchestrator.acquire_tenor_init_lock(root, wait_timeout_seconds=0.0)
        payload = json.loads(lock.path.read_text(encoding="utf-8"))
        payload["updated_epoch"] = time.time() - 10_000
        lock.path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(orchestrator.TenorInitBusy):
            orchestrator.acquire_tenor_init_lock(root, wait_timeout_seconds=0.0, stale_after_seconds=1.0)
        self.assertTrue(lock.path.exists())
        orchestrator.release_tenor_init_lock(lock)

    def test_fresh_partial_lock_is_not_stolen(self) -> None:
        root = self.base / "fresh-partial-lock"
        root.mkdir()
        make_project(root)
        lock_path = root / orchestrator.LOCK_RELATIVE
        lock_path.write_text("", encoding="utf-8")
        with self.assertRaises(orchestrator.TenorInitBusy):
            orchestrator.acquire_tenor_init_lock(root, wait_timeout_seconds=0.0, stale_after_seconds=30.0)
        self.assertTrue(lock_path.exists())

    def test_old_partial_lock_is_recovered_from_mtime(self) -> None:
        root = self.base / "old-partial-lock"
        root.mkdir()
        make_project(root)
        lock_path = root / orchestrator.LOCK_RELATIVE
        lock_path.write_text("", encoding="utf-8")
        old = time.time() - 10_000
        os.utime(lock_path, (old, old))
        lock = orchestrator.acquire_tenor_init_lock(root, wait_timeout_seconds=0.0, stale_after_seconds=1.0)
        self.assertNotEqual(lock.nonce, "")
        orchestrator.release_tenor_init_lock(lock)

    def test_dead_stale_lock_is_recovered(self) -> None:
        root = self.base / "dead-lock"
        root.mkdir()
        make_project(root)
        lock_path = root / orchestrator.LOCK_RELATIVE
        lock_path.write_text(
            json.dumps(
                {
                    "schema": "tenor_init_lock_v2",
                    "nonce": "dead",
                    "pid": 999999,
                    "hostname": orchestrator.socket.gethostname(),
                    "created_epoch": time.time() - 10_000,
                    "updated_epoch": time.time() - 10_000,
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.object(orchestrator, "_pid_is_alive", return_value=False):
            lock = orchestrator.acquire_tenor_init_lock(root, wait_timeout_seconds=0.0, stale_after_seconds=1.0)
        self.assertNotEqual(lock.nonce, "dead")
        orchestrator.release_tenor_init_lock(lock)

    def test_refresh_requires_lock_ownership(self) -> None:
        root = self.base / "refresh-lock"
        root.mkdir()
        make_project(root)
        lock = orchestrator.acquire_tenor_init_lock(root, wait_timeout_seconds=0.0)
        other = orchestrator.TenorInitLock(lock.path, "wrong", {})
        with self.assertRaises(orchestrator.TenorInitLockOwnershipLost):
            orchestrator.refresh_tenor_init_lock(other, stage="should-fail")
        refreshed = orchestrator.refresh_tenor_init_lock(lock, stage="bootstrap")
        self.assertEqual(refreshed.payload["stage"], "bootstrap")
        orchestrator.release_tenor_init_lock(refreshed)

    def test_six_concurrent_init_sessions_preserve_shared_runtime(self) -> None:
        root = self.base / "six-terminals"
        root.mkdir()
        make_project(root)
        orchestrator.prepare_tenor_init(root)
        orchestrator.finalize_tenor_init(root)
        shared_runtime = root / ".agent" / "state" / "runtime" / "coordination.sqlite"
        shared_runtime.parent.mkdir(parents=True, exist_ok=True)
        shared_runtime.write_bytes(b"shared-agent-runtime")
        barrier = threading.Barrier(6)

        def run_one(index: int) -> str:
            barrier.wait(timeout=5)
            with orchestrator.tenor_init_lock(root, wait_timeout_seconds=10.0) as lock:
                lock = orchestrator.refresh_tenor_init_lock(lock, stage=f"terminal-{index}")
                plan = orchestrator.prepare_tenor_init(root)
                self.assertEqual(plan.classification, orchestrator.TENOR_INIT_SAME_PROJECT)
                self.assertFalse(plan.purge_executed)
                self.assertEqual(shared_runtime.read_bytes(), b"shared-agent-runtime")
                self.assertTrue(orchestrator.finalize_tenor_init(root)["ok"])
                return lock.payload["stage"]

        with ThreadPoolExecutor(max_workers=6) as executor:
            stages = sorted(executor.map(run_one, range(6)))
        self.assertEqual(stages, [f"terminal-{index}" for index in range(6)])
        self.assertEqual(shared_runtime.read_bytes(), b"shared-agent-runtime")

    def test_orchestrator_uses_manifest_not_scribe_presence(self) -> None:
        root = self.base / "manifest-authority"
        root.mkdir()
        make_project(root, memory=None)
        first = orchestrator.prepare_tenor_init(root)
        self.assertEqual(first.classification, orchestrator.TENOR_INIT_NEW_INSTALLATION)
        self.assertEqual(first.memory_action, orchestrator.SCRIBE_MEMORY_CREATE)
        orchestrator.finalize_tenor_init(root)
        second = orchestrator.prepare_tenor_init(root)
        self.assertEqual(second.classification, orchestrator.TENOR_INIT_SAME_PROJECT)
        self.assertEqual(second.memory_action, orchestrator.SCRIBE_MEMORY_CREATE)


class AtomicLockWriteHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_atomic_lock_write_uses_exclusive_sibling_temp(self) -> None:
        root = self.base / "lockproj"
        root.mkdir()
        lock_path = root / ".agent" / "mcp" / "tenor_init.lock"
        lock_path.parent.mkdir(parents=True)
        captured: dict[str, object] = {}
        real_mkstemp = tempfile.mkstemp

        def fake_mkstemp(*args, **kwargs):
            captured["dir"] = kwargs.get("dir")
            captured["prefix"] = kwargs.get("prefix")
            captured["suffix"] = kwargs.get("suffix")
            fd, name = real_mkstemp(*args, **kwargs)
            captured["tmp_name"] = name
            return fd, name

        payload = {"schema": "tenor_init_lock_v2", "nonce": "abc", "pid": 999999}
        with mock.patch.object(tempfile, "mkstemp", fake_mkstemp):
            orchestrator._atomic_lock_write(lock_path, payload)
        self.assertEqual(captured["dir"], str(lock_path.parent))
        self.assertTrue(str(captured["prefix"]).startswith(f".{lock_path.name}."))
        self.assertEqual(captured["suffix"], ".tmp")
        self.assertNotIn(str(os.getpid()), captured["tmp_name"])
        self.assertNotIn(str(time.time_ns()), captured["tmp_name"])
        self.assertEqual(json.loads(lock_path.read_text(encoding="utf-8")), payload)

    def test_atomic_lock_write_concurrent_no_orphans(self) -> None:
        root = self.base / "lockproj"
        root.mkdir()
        lock_path = root / ".agent" / "mcp" / "tenor_init.lock"
        lock_path.parent.mkdir(parents=True)
        marker = "X" * 4096

        def write(n: int) -> None:
            orchestrator._atomic_lock_write(lock_path, {"schema": "tenor_init_lock_v2", "nonce": f"n{n}", "marker": marker})

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(write, range(64)))
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(data["marker"], marker)
        self.assertEqual(len(data["marker"]), 4096)
        orphans = [p for p in lock_path.parent.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(orphans, [])


if __name__ == "__main__":
    unittest.main()
