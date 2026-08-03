from __future__ import annotations

import multiprocessing
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import db


def _connection_wave(
    root_text: str,
    iterations: int,
    result_queue: multiprocessing.Queue,
) -> None:
    root = Path(root_text)
    try:
        for _ in range(iterations):
            with db.connect(root) as connection:
                row = connection.execute("SELECT COUNT(*) FROM agents").fetchone()
                if row is None:
                    raise RuntimeError("COUNT query returned no row")
        result_queue.put({"ok": True})
    except BaseException as exc:
        result_queue.put({"ok": False, "error": str(exc)})


class SqliteConnectionPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project"
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True)
        self.previous_mode = os.environ.pop(
            db.SQLITE_JOURNAL_MODE_ENV,
            None,
        )
        db.init_db(self.root)

    def tearDown(self) -> None:
        if self.previous_mode is None:
            os.environ.pop(db.SQLITE_JOURNAL_MODE_ENV, None)
        else:
            os.environ[db.SQLITE_JOURNAL_MODE_ENV] = self.previous_mode
        self.temporary.cleanup()

    def test_default_policy_is_delete_and_full(self) -> None:
        with db.connect(self.root) as connection:
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0].upper(),
                "DELETE",
            )
            self.assertEqual(
                int(connection.execute("PRAGMA synchronous").fetchone()[0]),
                2,
            )

    def test_invalid_journal_mode_fails_closed(self) -> None:
        os.environ[db.SQLITE_JOURNAL_MODE_ENV] = "memory"
        with self.assertRaisesRegex(
            db.CoordinationError,
            "SQLITE_JOURNAL_MODE_INVALID",
        ):
            with db.connect(self.root):
                pass

    def test_unrelated_sqlite_application_id_is_rejected(self) -> None:
        database = (
            self.root
            / ".agent"
            / "state"
            / "runtime"
            / "coordination.sqlite"
        )
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA application_id=12345")
        connection.close()
        with self.assertRaisesRegex(
            db.CoordinationError,
            "SQLITE_APPLICATION_ID_MISMATCH",
        ):
            with db.connect(self.root):
                pass

    def test_six_processes_open_six_hundred_fresh_connections(self) -> None:
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        processes = [
            context.Process(
                target=_connection_wave,
                args=(str(self.root), 100, result_queue),
            )
            for _ in range(6)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=60)
        self.assertFalse(
            any(process.is_alive() for process in processes),
            "a connection worker exceeded the bounded timeout",
        )
        self.assertEqual([process.exitcode for process in processes], [0] * 6)
        results = [result_queue.get(timeout=5) for _ in range(6)]
        self.assertTrue(all(item["ok"] for item in results), results)
        with db.connect(self.root) as connection:
            self.assertEqual(
                connection.execute("PRAGMA quick_check").fetchone()[0],
                "ok",
            )
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0],
                "ok",
            )

    def test_repeated_connections_preserve_application_identity(self) -> None:
        for _ in range(50):
            with db.connect(self.root) as connection:
                self.assertEqual(
                    int(
                        connection.execute(
                            "PRAGMA application_id"
                        ).fetchone()[0]
                    ),
                    db._DB_APP_ID,
                )


if __name__ == "__main__":
    unittest.main()
