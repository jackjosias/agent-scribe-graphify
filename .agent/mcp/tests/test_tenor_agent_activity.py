from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import server_ext as mcp
from runtime import db, presence, task_context, tenor_public_api
from _strict_cleanup import remove_tree_strict


class TenorAgentActivityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True)
        self.old_cwd = Path.cwd()
        self.old_root = mcp.server.ROOT
        self.old_bound = getattr(mcp.server, "_MCP_BOUND_AGENT_ID", "")
        os.chdir(self.root)
        mcp.server.ROOT = self.root
        db.init_db(self.root)
        db.register_agent("opencode", "small-a", "agent-a")
        db.register_agent("opencode", "small-b", "agent-b")
        mcp.server._MCP_BOUND_AGENT_ID = "agent-a"
        tenor_public_api.ensure_schema(self.root)

    def tearDown(self) -> None:
        presence.stop(self.root, "agent-a")
        presence.stop(self.root, "agent-b")
        tenor_public_api._TASK_TOKENS.clear()
        mcp.server._MCP_BOUND_AGENT_ID = self.old_bound
        mcp.server.ROOT = self.old_root
        os.chdir(self.old_cwd)
        remove_tree_strict(self.tmp.name)
        self.tmp.cleanup()

    def call(self, tool: str, **arguments: object) -> dict[str, object]:
        response = mcp.handle({
            "jsonrpc": "2.0",
            "id": tool,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        })
        return json.loads(response["result"]["content"][0]["text"])

    def test_activity_reports_two_parallel_agents_with_current_last_and_next_actions(self) -> None:
        tenor_public_api._write_activity(
            "task-a",
            "agent-a",
            "implement API",
            "write",
            "src/api",
            ["src/api/a.py"],
            status="active",
            current_action="validator",
            last_action="apply_changeset",
            next_action="scribe_record_and_finish",
        )
        tenor_public_api._write_activity(
            "task-b",
            "agent-b",
            "implement renderer",
            "write",
            "src/render",
            ["src/render/b.rs"],
            status="active",
            current_action="prepare_changeset",
            last_action="graphify_query",
            next_action="tenor_apply_changeset",
        )
        result = self.call("tenor_activity", include_history=20)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["active_agents"], 2)
        agents = {item["agent_id"]: item for item in result["agents"]}
        self.assertEqual(agents["agent-a"]["current_task"]["current_action"], "validator")
        self.assertEqual(agents["agent-a"]["current_task"]["last_action"], "apply_changeset")
        self.assertEqual(agents["agent-a"]["current_task"]["next_action"], "scribe_record_and_finish")
        self.assertEqual(agents["agent-b"]["current_task"]["objective"], "implement renderer")

    def test_live_registered_process_is_not_marked_idle_only_because_model_thinks_long(self) -> None:
        with db.connect(self.root) as con:
            con.execute(
                "UPDATE agents SET last_seen=?,pid=?,status='active' WHERE agent_id='agent-a'",
                (int(time.time()) - 3600, os.getpid()),
            )
        agents = {item["agent_id"]: item for item in db.list_agents()["agents"]}
        self.assertEqual(agents["agent-a"]["status"], "active")
        self.assertTrue(agents["agent-a"].get("process_alive"))

    def test_dead_process_with_old_heartbeat_becomes_idle(self) -> None:
        with db.connect(self.root) as con:
            con.execute(
                "UPDATE agents SET last_seen=?,pid=?,status='active' WHERE agent_id='agent-b'",
                (int(time.time()) - 3600, 2_000_000_000),
            )
        agents = {item["agent_id"]: item for item in db.list_agents()["agents"]}
        self.assertEqual(agents["agent-b"]["status"], "idle")
        self.assertFalse(agents["agent-b"].get("process_alive"))

    def test_expired_task_can_resume_when_same_agent_has_no_replacement_task(self) -> None:
        created = task_context.create_task_context(
            agent_id="agent-a",
            request="long implementation",
            intent="write",
            resource="src/api/a.py",
            requires_graphify=False,
            ttl_seconds=1,
        )
        time.sleep(1.1)
        resumed = task_context.resume_task_context("agent-a", created["task_id"])
        self.assertEqual(resumed["status"], "active")
        self.assertGreater(resumed["expires_at"], int(time.time()))

    def test_valid_context_use_renews_rolling_expiry(self) -> None:
        created = task_context.create_task_context(
            agent_id="agent-a",
            request="rolling task",
            intent="read",
            resource="src/api/a.py",
            requires_graphify=False,
            ttl_seconds=60,
        )
        with db.connect(self.root) as con:
            con.execute(
                "UPDATE task_context_v2 SET expires_at=? WHERE task_id=?",
                (int(time.time()) + 2, created["task_id"]),
            )
        task_context.verify_active_context(
            "agent-a",
            created["task_id"],
            created["context_token"],
        )
        refreshed = task_context.task_status(created["task_id"])
        self.assertGreater(refreshed["expires_at"], int(time.time()) + 100)


if __name__ == "__main__":
    unittest.main()
