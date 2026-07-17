from __future__ import annotations

"""Portable V2.16 tests plus runtime-policy regression coverage."""

import importlib.util
import json
import os
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
AGENT_DIR = MCP_DIR.parent
SCRIPTS_DIR = AGENT_DIR / "workflow" / "scribe" / "sel" / "scripts"
for path in (MCP_DIR, AGENT_DIR, SCRIPTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

_IMPL_PATH = HERE / "_test_v216_cross_platform_impl.py"
_IMPL_SPEC = importlib.util.spec_from_file_location(
    "_test_v216_cross_platform_impl",
    _IMPL_PATH,
)
if _IMPL_SPEC is None or _IMPL_SPEC.loader is None:
    raise RuntimeError(f"cannot load portable test implementation: {_IMPL_PATH}")
_IMPL_MODULE = importlib.util.module_from_spec(_IMPL_SPEC)
_IMPL_SPEC.loader.exec_module(_IMPL_MODULE)
for _name in dir(_IMPL_MODULE):
    _value = getattr(_IMPL_MODULE, _name)
    if isinstance(_value, type) and issubclass(_value, unittest.TestCase):
        globals()[_name] = _value

import scribe_bootstrap
from host_adapter import instructions as host_instructions
from runtime import db, graphify_scribe_bridge, task_context


class RuntimePolicyPortabilityTest(unittest.TestCase):
    def test_coordination_windows_pid_probe_never_calls_os_kill(self) -> None:
        with (
            mock.patch.object(db, "IS_WINDOWS", True),
            mock.patch.object(db, "_windows_pid_is_alive", return_value=True) as windows_probe,
            mock.patch.object(db.os, "kill", side_effect=AssertionError("os.kill must not run on Windows")),
        ):
            self.assertTrue(db.process_is_alive(424242))

        windows_probe.assert_called_once_with(424242)

    def test_graphify_bridge_uses_coordination_process_probe(self) -> None:
        with mock.patch.object(db, "process_is_alive", return_value=False) as process_probe:
            self.assertFalse(graphify_scribe_bridge._pid_alive(424242))

        process_probe.assert_called_once_with(424242)

    def test_read_or_research_is_canonical_read(self) -> None:
        aliases = {
            "read",
            "read_or_research",
            "read-or-research",
            "research",
            "inspect",
            "query",
            "ask",
            "explain",
            "list",
            "show",
            "status",
        }
        self.assertEqual(
            {task_context.normalize_intent(value) for value in aliases},
            {"read"},
        )
        self.assertEqual(task_context.normalize_intent("edit"), "write")
        self.assertEqual(task_context.normalize_intent("remove"), "delete")

    def test_atomic_text_write_serializes_same_destination_and_cleans_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.json"
            marker = "R" * 8192
            payloads = [
                json.dumps({"n": index, "marker": marker}, sort_keys=True) + "\n"
                for index in range(64)
            ]

            def write(content: str) -> None:
                scribe_bootstrap._atomic_text_write(target, content)

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(write, payloads))

            final = target.read_text(encoding="utf-8")
            self.assertIn(final, payloads)
            self.assertEqual(json.loads(final)["marker"], marker)
            self.assertEqual(
                [path for path in target.parent.iterdir() if path.name.endswith(".tmp")],
                [],
            )
            self.assertEqual(scribe_bootstrap._atomic_lock_registry_size(), 0)

    def test_atomic_text_write_uses_independent_destination_locks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.txt"
            second = Path(tmp) / "second.txt"
            barrier = threading.Barrier(2)
            original_replace = scribe_bootstrap._atomic_replace

            def synchronized_replace(src: str, dst: Path) -> None:
                barrier.wait(timeout=3)
                original_replace(src, dst)

            with mock.patch.object(
                scribe_bootstrap,
                "_atomic_replace",
                synchronized_replace,
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(
                            scribe_bootstrap._atomic_text_write,
                            first,
                            "first\n",
                        ),
                        executor.submit(
                            scribe_bootstrap._atomic_text_write,
                            second,
                            "second\n",
                        ),
                    ]
                    for future in futures:
                        future.result(timeout=5)

            self.assertEqual(first.read_text(encoding="utf-8"), "first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "second\n")
            self.assertEqual(scribe_bootstrap._atomic_lock_registry_size(), 0)

    def test_atomic_replace_retry_is_bounded(self) -> None:
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
                scribe_bootstrap._atomic_replace(
                    "source.tmp",
                    Path("destination.txt"),
                )
        self.assertEqual(attempts, 10)
        self.assertEqual(sleeper.call_count, 9)
        self.assertTrue(
            all(call.args[0] <= 0.25 for call in sleeper.call_args_list)
        )

    def test_dead_same_host_instruction_owner_is_recoverable_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".AGENTS.md.agent-instructions.lock"
            lock_path.write_text(
                json.dumps({
                    "nonce": "dead-owner",
                    "pid": 999999,
                    "hostname": host_instructions.socket.gethostname(),
                    "created_epoch": time.time(),
                }),
                encoding="utf-8",
            )
            with mock.patch.object(
                host_instructions._impl,
                "_pid_is_alive",
                return_value=False,
            ):
                self.assertTrue(
                    host_instructions._lock_is_stale(
                        lock_path,
                        host_instructions._impl._read_lock(lock_path),
                    )
                )

    def test_instruction_lock_release_retries_windows_sharing_and_cleans_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".AGENTS.md.agent-instructions.lock"
            lock_path.write_text(
                json.dumps({"nonce": "owned", "pid": os.getpid()}),
                encoding="utf-8",
            )
            real_unlink = Path.unlink
            attempts = 0

            def flaky_unlink(path: Path, *args, **kwargs) -> None:
                nonlocal attempts
                if path == lock_path and attempts < 2:
                    attempts += 1
                    raise PermissionError("sharing violation")
                real_unlink(path, *args, **kwargs)

            with mock.patch.object(Path, "unlink", flaky_unlink), mock.patch.object(
                time,
                "sleep",
            ) as sleeper:
                self.assertTrue(
                    host_instructions._release_owned_lock(lock_path, "owned")
                )
            self.assertEqual(attempts, 2)
            self.assertEqual(sleeper.call_count, 2)
            self.assertFalse(lock_path.exists())
            self.assertEqual(host_instructions._process_lock_registry_size(), 0)


if __name__ == "__main__":
    unittest.main()
