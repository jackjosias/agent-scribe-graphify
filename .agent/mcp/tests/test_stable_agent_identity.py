from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class StableAgentIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        shutil.copytree(ROOT / ".agent" / "mcp", self.root / ".agent" / "mcp")
        (self.root / "README.md").write_text("test project\n", encoding="utf-8")
        self.entry = self.root / ".agent" / "mcp" / "server_entry.py"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def call(self, tool: str, **args: object) -> dict[str, object]:
        proc = subprocess.run(
            ["python3", str(self.entry), "--call", tool, "--args", json.dumps(args)],
            cwd=str(self.root),
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


if __name__ == "__main__":
    unittest.main()
