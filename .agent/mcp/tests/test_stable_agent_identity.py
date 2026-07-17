from __future__ import annotations

import datetime
import json
import sqlite3
import sys
import time
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import graphify_readiness, installation_state, tenor_public_api
from _strict_cleanup import remove_tree_strict


class StableAgentIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        shutil.copytree(ROOT / ".agent" / "mcp", self.root / ".agent" / "mcp")
        (self.root / "README.md").write_text("test project\n", encoding="utf-8")
        self.entry = self.root / ".agent" / "mcp" / "server_entry.py"
        # Build subprocess env: inherit parent but pin the workspace root so
        # server_entry.py does NOT pick up a leaked AGENT_SCRIBE_GRAPHIFY_ROOT
        # from other test modules (e.g. test_lease_extend.py line 57).
        self._sub_env = {
            **os.environ,
            "AGENT_SCRIBE_GRAPHIFY_ROOT": str(self.root),
            graphify_readiness.FIXTURE_ENV: "1",
        }
        subprocess.run(["git", "init"], cwd=str(self.root), capture_output=True, env=self._sub_env)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(self.root), capture_output=True, env=self._sub_env)
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(self.root), capture_output=True, env=self._sub_env)
        prepared = installation_state.ensure_fresh_installation_state(self.root)
        self.assertTrue(prepared["ok"], prepared)
        finalized = installation_state.finalize_installation_state(self.root)
        self.assertTrue(finalized["ok"], finalized)
        fixture = graphify_readiness.write_smoke_fixture(self.root)
        self.assertTrue(fixture["ok"], fixture)

    def tearDown(self) -> None:
        remove_tree_strict(self.tmp.name)
        self.tmp.cleanup()

    def call(self, tool: str, **args: object) -> dict[str, object]:
        proc = subprocess.run(
            ["python3", str(self.entry), "--call", tool, "--args", json.dumps(args)],
            cwd=str(self.root),
            env=self._sub_env,
            text=True,
            capture_output=True,
            timeout=20,
        )
        if proc.returncode != 0:
            payload = json.loads(proc.stderr or proc.stdout)
            return {"ok": False, **payload}
        raw = json.loads(proc.stdout)
        text = raw["content"][0]["text"]
        return json.loads(text)

    def register(self, agent_id: str) -> dict[str, object]:
        return self.call("register_agent", host_tool="test", model_name="unit", agent_id=agent_id)

    def test_public_task_schemas_never_accept_caller_identity_or_context_token(self) -> None:
        for tool in tenor_public_api.PUBLIC_TASK_TOOLS:
            schema = tenor_public_api.tool_schema(tool)
            properties = set(schema.get("properties", {}))
            self.assertNotIn("agent_id", properties, tool)
            self.assertNotIn("context_token", properties, tool)

    def test_register_agent_is_idempotent(self) -> None:
        first = self.register("agent-a")
        second = self.register("agent-a")
        self.assertEqual(first["agent"]["agent_id"], "agent-a")
        self.assertEqual(second["agent"]["agent_id"], "agent-a")
        agents = self.call("list_agents")
        self.assertEqual(agents["count"], 1)

    def test_workflow_next_unknown_agent_does_not_bootstrap(self) -> None:
        result = self.call("workflow_next", agent_id="missing", request="edit", intent="write", resource="README.md")
        self.assertEqual(result["verdict"], "AGENT_UNKNOWN_OR_UNREGISTERED")
        self.assertNotEqual(result.get("must_call", {}).get("tool"), "bootstrap")
        self.assertIn("bootstrap", result["forbidden"])

    def test_before_task_duplicate_returns_active_task_exists(self) -> None:
        self.register("agent-a")
        first = self.call("before_task", agent_id="agent-a", request="edit readme", intent="write", resource="README.md")
        self.assertEqual(first["verdict"], "BEFORE_TASK_OK")
        second = self.call("before_task", agent_id="agent-a", request="edit readme", intent="write", resource="README.md")
        self.assertEqual(second["verdict"], "ACTIVE_TASK_EXISTS")

    def test_one_agent_cannot_open_a_replacement_task_on_another_resource(self) -> None:
        self.register("agent-a")
        first = self.call("before_task", agent_id="agent-a", request="edit readme", intent="write", resource="README.md")
        self.assertEqual(first["verdict"], "BEFORE_TASK_OK", first)
        second = self.call("before_task", agent_id="agent-a", request="inspect another file", intent="read", resource="other.txt")
        self.assertEqual(second["verdict"], "ACTIVE_TASK_EXISTS", second)
        self.assertEqual(second["must_call"]["tool"], "resume_task_context", second)

    def test_propose_patch_wrong_agent_returns_context_agent_mismatch(self) -> None:
        self.register("agent-a")
        self.register("agent-b")
        ctx = self.call("before_task", agent_id="agent-a", request="edit readme", intent="write", resource="README.md")
        self.call("scribe_query", agent_id="agent-a", task_id=ctx["task_id"], context_token=ctx["context_token"], query="edit readme", limit=1)
        self.call("graphify_query", agent_id="agent-a", task_id=ctx["task_id"], context_token=ctx["context_token"], query="impact", resource="README.md")
        base = self.call("file_hash", resource="README.md")["hash"]
        result = self.call("propose_patch", agent_id="agent-b", target="README.md", base_hash=base, diff_text="@@ -1 +1 @@\n-test project\n+changed\n", task_id=ctx["task_id"], context_token=ctx["context_token"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "TASK_CONTEXT_AGENT_MISMATCH")

    def test_read_context_cannot_write(self) -> None:
        self.register("agent-a")
        ctx = self.call("before_task", agent_id="agent-a", request="read readme", intent="read", resource="README.md")
        self.call("scribe_query", agent_id="agent-a", task_id=ctx["task_id"], context_token=ctx["context_token"], query="read readme", limit=1)
        base = self.call("file_hash", resource="README.md")["hash"]
        result = self.call("propose_patch", agent_id="agent-a", target="README.md", base_hash=base, diff_text="@@ -1 +1 @@\n-test project\n+changed\n", task_id=ctx["task_id"], context_token=ctx["context_token"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "READ_INTENT_CANNOT_WRITE")

    def test_retry_loop_detected(self) -> None:
        self.register("agent-a")
        first = self.call("workflow_next", agent_id="agent-a", request="edit readme", intent="write", resource="README.md", last_verdict="TASK_CONTEXT_AGENT_MISMATCH")
        second = self.call("workflow_next", agent_id="agent-a", request="edit readme", intent="write", resource="README.md", last_verdict="TASK_CONTEXT_AGENT_MISMATCH")
        self.assertIn(second["verdict"], {"RETRY_LOOP_DETECTED", "MAX_WORKFLOW_RETRIES_EXCEEDED"})
        self.assertIn("direct_file_edit", second["forbidden"])

    def test_two_agents_can_be_observed_and_waited(self) -> None:
        self.register("agent-a")
        self.register("agent-b")
        a = self.call("before_task", agent_id="agent-a", request="edit readme", intent="write", resource="README.md")
        b = self.call("before_task", agent_id="agent-b", request="read readme", intent="read", resource="README.md")
        agents = self.call("list_agents")
        self.assertEqual(agents["count"], 2)
        tasks = self.call("list_tasks")
        self.assertGreaterEqual(tasks["count"], 2)
        waiting = self.call("wait_for_tasks", task_ids=[a["task_id"], b["task_id"]])
        self.assertEqual(waiting["verdict"], "TASKS_WAITING")
        timeout = self.call("wait_for_tasks", task_ids=[a["task_id"]], timeout_seconds=1, poll_interval_seconds=0.1)
        self.assertEqual(timeout["verdict"], "WAIT_TIMEOUT")

    def test_workflow_next_after_bypass_routes_to_audit_only(self) -> None:
        self.register("agent-a")
        ctx = self.call("before_task", agent_id="agent-a", request="edit readme", intent="write", resource="README.md")
        result = self.call("workflow_next", agent_id="agent-a", request="edit readme", intent="write", resource="README.md", task_id=ctx["task_id"], context_token=ctx["context_token"], last_verdict="DIRECT_WRITE_BYPASS_DETECTED")
        self.assertEqual(result["state"], "BYPASS_BLOCKED", result)
        self.assertEqual(result["must_call"]["tool"], "workspace_audit", result)
        self.assertIn("claim_resource", result["forbidden"])
        self.assertIn("apply_patch", result["forbidden"])
        self.assertIn("finish_task", result["forbidden"])

    def test_abandoned_agent_still_becomes_idle_after_timeout(self) -> None:
        self.register("agent-a")
        db_path = self.root / ".agent" / "state" / "runtime" / "coordination.sqlite"
        with closing(sqlite3.connect(str(db_path))) as con:
            with con:
                con.execute("UPDATE agents SET last_seen=? WHERE agent_id=?", (int(time.time()) - 901, "agent-a"))
        agents = self.call("list_agents")
        match = [agent for agent in agents["agents"] if agent["agent_id"] == "agent-a"]
        self.assertEqual(match[0]["status"], "idle", agents)

    def test_json_response_serializes_date_values(self) -> None:
        mcp_dir = ROOT / ".agent" / "mcp"
        if str(mcp_dir) not in sys.path:
            sys.path.insert(0, str(mcp_dir))
        from server import ok

        payload = json.loads(ok({"today": datetime.date(2026, 7, 7)})["content"][0]["text"])
        self.assertEqual(payload["today"], "2026-07-07")

    def test_require_agent_active_refreshes_last_seen(self) -> None:
        self.register("agent-a")
        db_path = self.root / ".agent" / "state" / "runtime" / "coordination.sqlite"
        stale_seen = int(time.time()) - 300
        with closing(sqlite3.connect(str(db_path))) as con:
            with con:
                con.execute("UPDATE agents SET last_seen=? WHERE agent_id=?", (stale_seen, "agent-a"))
        result = self.call("before_task", agent_id="agent-a", request="edit readme", intent="write", resource="README.md")
        self.assertEqual(result["verdict"], "BEFORE_TASK_OK", result)
        with closing(sqlite3.connect(str(db_path))) as con:
            last_seen = con.execute("SELECT last_seen FROM agents WHERE agent_id=?", ("agent-a",)).fetchone()[0]
        self.assertGreaterEqual(int(last_seen), int(time.time()) - 5)


if __name__ == "__main__":
    unittest.main()
