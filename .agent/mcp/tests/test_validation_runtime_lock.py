from __future__ import annotations

import multiprocessing as mp
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime.validation_lock import (
    ValidationRuntimeBusy,
    reset_validation_runtime_database,
    validation_runtime_busy_message,
    validation_runtime_lock,
)
from runtime import db, patch_queue

ROOT = Path(__file__).resolve().parents[3]


def hold_lock(root: str, seconds: float) -> None:
    with validation_runtime_lock(Path(root), timeout_seconds=1):
        time.sleep(seconds)


class ValidationRuntimeLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_01_lock_can_be_acquired_and_released(self) -> None:
        with validation_runtime_lock(self.root, timeout_seconds=1) as path:
            self.assertTrue(path.exists())
        with validation_runtime_lock(self.root, timeout_seconds=1):
            self.assertTrue(True)

    def test_02_second_attempt_times_out_while_held(self) -> None:
        with validation_runtime_lock(self.root, timeout_seconds=1):
            with self.assertRaises(ValidationRuntimeBusy):
                with validation_runtime_lock(self.root, timeout_seconds=0.05, poll_interval=0.01):
                    pass

    def test_03_lock_releases_after_exception(self) -> None:
        with self.assertRaises(ValueError):
            with validation_runtime_lock(self.root, timeout_seconds=1):
                raise ValueError("boom")
        with validation_runtime_lock(self.root, timeout_seconds=1):
            self.assertTrue(True)

    def test_04_timeout_reports_busy(self) -> None:
        with validation_runtime_lock(self.root, timeout_seconds=1):
            try:
                with validation_runtime_lock(self.root, timeout_seconds=0.01, poll_interval=0.01):
                    pass
            except ValidationRuntimeBusy as exc:
                self.assertEqual(str(exc), "VALIDATION_RUNTIME_BUSY")
                return
        self.fail("expected ValidationRuntimeBusy")

    def test_05_busy_message_tells_agents_to_run_sequentially(self) -> None:
        message = validation_runtime_busy_message(self.root / ".agent" / "state" / "runtime" / "validation-smoke.lock")
        self.assertIn("VALIDATION_RUNTIME_BUSY_RUN_SEQUENTIALLY", message)
        self.assertIn("validation-smoke.lock", message)

    def test_06_mcp_smoke_uses_validation_lock(self) -> None:
        text = (ROOT / ".agent" / "scripts" / "mcp_smoke.py").read_text(encoding="utf-8")
        self.assertIn("validation_runtime_lock", text)
        self.assertIn("VALIDATION_RUNTIME_LOCK_ACQUIRED", text)
        self.assertIn("validation_runtime_busy_message", text)
        self.assertIn("clean_smoke_workspaces", text)
        self.assertIn("SMOKE_CLEANUP_FAILED", text)
        self.assertIn("os.path.lexists", text)
        self.assertIn("finally:", text)

    def test_07_redteam_smoke_uses_validation_lock(self) -> None:
        text = (ROOT / ".agent" / "scripts" / "enforcement_redteam_smoke.py").read_text(encoding="utf-8")
        self.assertIn("validation_runtime_lock", text)
        self.assertIn("VALIDATION_RUNTIME_LOCK_ACQUIRED", text)
        self.assertIn("validation_runtime_busy_message", text)
        self.assertEqual(text.count("clean_runtime()"), 2)
        self.assertNotIn(".agent/mcp/tests/fixtures/direct_fs_tripwire_target.txt", text)
        self.assertIn("tenor-redteam-tripwire-target.txt", text)
        self.assertIn("class PersistentMcpClient", text)
        self.assertIn("subprocess.Popen", text)
        self.assertNotIn("subprocess.run(", text)

    def test_08_same_runtime_is_serialized(self) -> None:
        proc = mp.Process(target=hold_lock, args=(str(self.root), 0.3))
        proc.start()
        time.sleep(0.05)
        start = time.monotonic()
        with validation_runtime_lock(self.root, timeout_seconds=2, poll_interval=0.02):
            elapsed = time.monotonic() - start
        proc.join(timeout=2)
        self.assertEqual(proc.exitcode, 0)
        self.assertGreaterEqual(elapsed, 0.20)

    def test_09_validation_suite_runs_lock_test_before_runtime_smokes(self) -> None:
        text = (ROOT / ".agent" / "scripts" / "validation_suite.py").read_text(encoding="utf-8")
        lock_pos = text.index("test_validation_runtime_lock.py")
        smoke_pos = text.index("mcp_smoke.py")
        redteam_pos = text.index("enforcement_redteam_smoke.py")
        self.assertLess(lock_pos, smoke_pos)
        self.assertLess(smoke_pos, redteam_pos)

    def test_10_reset_removes_valid_database_and_wal_sidecars(self) -> None:
        database = self.root / ".agent" / "state" / "runtime" / "coordination.sqlite"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE sample(value TEXT)")
            connection.execute("INSERT INTO sample(value) VALUES('ok')")
            connection.commit()
        reset_validation_runtime_database(self.root)
        self.assertFalse(database.exists())
        self.assertFalse((database.parent / "coordination.sqlite-wal").exists())
        self.assertFalse((database.parent / "coordination.sqlite-shm").exists())

    def test_11_reset_removes_malformed_database_and_sidecars(self) -> None:
        runtime = self.root / ".agent" / "state" / "runtime"
        database = runtime / "coordination.sqlite"
        database.write_bytes(b"not sqlite")
        (runtime / "coordination.sqlite-wal").write_bytes(b"broken wal")
        (runtime / "coordination.sqlite-shm").write_bytes(b"broken shm")
        reset_validation_runtime_database(self.root)
        self.assertFalse(database.exists())
        self.assertFalse((runtime / "coordination.sqlite-wal").exists())
        self.assertFalse((runtime / "coordination.sqlite-shm").exists())

    def test_12_repeated_cross_process_wal_resets_remain_integral(self) -> None:
        database = self.root / ".agent" / "state" / "runtime" / "coordination.sqlite"
        writer = """
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1], timeout=5.0, isolation_level=None)
try:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE IF NOT EXISTS churn(id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
    connection.execute("BEGIN IMMEDIATE")
    connection.executemany(
        "INSERT INTO churn(payload) VALUES(?)",
        [(f"cycle-row-{index}-" + "x" * 512,) for index in range(128)],
    )
    connection.execute("COMMIT")
finally:
    connection.close()
"""
        for cycle in range(20):
            process = subprocess.run(
                [sys.executable, "-c", writer, str(database)],
                text=True,
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(process.returncode, 0, f"cycle={cycle}: {process.stderr}")
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM churn").fetchone()[0], 128)
            reset_validation_runtime_database(self.root)
            self.assertFalse(database.exists())
            self.assertFalse((database.parent / "coordination.sqlite-wal").exists())
            self.assertFalse((database.parent / "coordination.sqlite-shm").exists())

    def test_13_patch_queue_context_closes_sqlite_connection(self) -> None:
        database = self.root / ".agent" / "state" / "runtime" / "coordination.sqlite"
        with mock.patch.object(patch_queue, "db_path", return_value=database):
            with patch_queue.connect() as connection:
                self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_14_init_db_runs_migrations_once_under_thread_contention(self) -> None:
        database = self.root / ".agent" / "state" / "runtime" / "coordination.sqlite"
        db._init_db_checked.pop(str(database), None)
        original = db._run_migrations
        with mock.patch.object(db, "_run_migrations", wraps=original) as migrations:
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda _index: db.init_db(self.root), range(32)))
        self.assertEqual(migrations.call_count, 1)
        self.assertTrue(all(result["db"] == str(database) for result in results))
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        db._init_db_checked.pop(str(database), None)




if __name__ == "__main__":
    unittest.main()
