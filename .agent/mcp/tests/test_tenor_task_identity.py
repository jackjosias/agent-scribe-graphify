from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import db, tenor_public_api


class LogicalTaskKeyTest(unittest.TestCase):
    def test_key_is_deterministic_and_resource_order_insensitive(self) -> None:
        first = tenor_public_api._logical_task_key("Fix graph build", "write", "graphify", ["b.py", "a.py"])
        second = tenor_public_api._logical_task_key("Fix graph build", "write", "graphify", ["a.py", "b.py"])
        different = tenor_public_api._logical_task_key("Fix graph build", "write", "graphify", ["a.py"])
        self.assertEqual(first, second)
        self.assertNotEqual(first, different)

    def test_key_is_sensitive_to_objective_intent_and_scope(self) -> None:
        base = tenor_public_api._logical_task_key("X", "write", "s", [])
        self.assertNotEqual(base, tenor_public_api._logical_task_key("X", "read", "s", []))
        self.assertNotEqual(base, tenor_public_api._logical_task_key("Y", "write", "s", []))
        self.assertNotEqual(base, tenor_public_api._logical_task_key("X", "write", "t", []))

    def test_key_normalizes_whitespace_and_duplicate_resources(self) -> None:
        loose = tenor_public_api._logical_task_key("  Objective  ", "write", "scope", ["a.py", "a.py"])
        tight = tenor_public_api._logical_task_key("Objective", "write", "scope", ["a.py"])
        self.assertEqual(loose, tight)


class ExistingLogicalTaskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True)
        self._previous_server = tenor_public_api._SERVER
        self._previous_cwd = Path.cwd()
        os.chdir(self.root)
        tenor_public_api._SERVER = type("FakeTenorServer", (), {"ROOT": str(self.root)})()
        db.init_db(self.root)
        tenor_public_api.ensure_schema(self.root)

    def tearDown(self) -> None:
        tenor_public_api._SERVER = self._previous_server
        os.chdir(self._previous_cwd)
        self.tmp.cleanup()

    def _insert(self, task_id: str, objective: str, intent: str, scope: str, resources: list[str], status: str = "active") -> None:
        now = int(time.time())
        with db.connect(self.root) as con:
            con.execute(
                f"INSERT INTO {tenor_public_api.ACTIVITY_TABLE} "
                "(task_id, agent_id, objective, intent, scope, resources_json, status, current_action, last_action, next_action, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, "agent-other", objective, intent, scope, json.dumps(resources), status, "", "", "", now, now),
            )

    def test_finds_logical_equivalent_task_owned_by_another_agent(self) -> None:
        self._insert("task-1", "Fix graph", "write", "graphify", ["b.py", "a.py"])
        found = tenor_public_api._existing_logical_task("Fix graph", "write", "graphify", ["a.py", "b.py"])
        self.assertIsNotNone(found)
        self.assertEqual(found["task_id"], "task-1")

    def test_does_not_match_different_objective(self) -> None:
        self._insert("task-1", "Fix graph", "write", "graphify", ["a.py"])
        found = tenor_public_api._existing_logical_task("Something else", "write", "graphify", ["a.py"])
        self.assertIsNone(found)

    def test_ignores_finished_tasks(self) -> None:
        self._insert("task-1", "Fix graph", "write", "graphify", ["a.py"], status="committed")
        found = tenor_public_api._existing_logical_task("Fix graph", "write", "graphify", ["a.py"])
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
