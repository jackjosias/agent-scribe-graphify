from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class WorkflowFsmStabilityTest(unittest.TestCase):
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
        raw_text = proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout)
        raw = json.loads(raw_text)
        if "content" in raw:
            return json.loads(raw["content"][0]["text"])
        return raw

    def register(self, agent_id: str = "host-gemini") -> dict[str, object]:
        return self.call("register_agent", agent_id=agent_id, host_tool="test", model_name="unit")

    def ready_context(self, agent_id: str = "host-gemini") -> dict[str, object]:
        self.register(agent_id)
        ctx = self.call("before_task", agent_id=agent_id, request="edit README", intent="write", resource="README.md")
        self.assertEqual(ctx["verdict"], "BEFORE_TASK_OK")
        self.call("scribe_query", agent_id=agent_id, task_id=ctx["task_id"], context_token=ctx["context_token"], query="edit README", limit=1)
        self.call("graphify_query", agent_id=agent_id, task_id=ctx["task_id"], context_token=ctx["context_token"], query="impact", resource="README.md")
        return ctx

    def test_before_task_unknown_agent_creates_no_context(self) -> None:
        result = self.call("before_task", agent_id="host-gemini", request="edit README", intent="write", resource="README.md")
        self.assertEqual(result["verdict"], "AGENT_UNKNOWN_OR_UNREGISTERED")
        self.assertIn("direct_file_edit", result["forbidden"])
        tasks = self.call("list_tasks", agent_id="host-gemini")
        self.assertEqual(tasks["count"], 0)

    def test_duplicate_before_task_returns_existing_task(self) -> None:
        self.register()
        first = self.call("before_task", agent_id="host-gemini", request="claim resource README for write", intent="write", resource="README.md")
        self.assertEqual(first["verdict"], "BEFORE_TASK_OK")
        second = self.call("before_task", agent_id="host-gemini", request="convert README and write patch", intent="write", resource="README.md")
        self.assertEqual(second["verdict"], "ACTIVE_TASK_EXISTS")
        self.assertEqual(second["must_call"]["tool"], "resume_task_context")
        self.assertEqual(second["must_call"]["args"]["task_id"], first["task_id"])
        self.assertIn("direct_file_edit", second["forbidden"])
        tasks = self.call("list_tasks", agent_id="host-gemini", status="active")
        self.assertEqual(tasks["count"], 1)

    def test_resume_task_context_rotates_token(self) -> None:
        self.register()
        first = self.call("before_task", agent_id="host-gemini", request="edit README", intent="write", resource="README.md")
        resumed = self.call("resume_task_context", agent_id="host-gemini", task_id=first["task_id"])
        self.assertEqual(resumed["verdict"], "TASK_CONTEXT_RESUMED")
        self.assertEqual(resumed["task_id"], first["task_id"])
        self.assertNotEqual(resumed["context_token"], first["context_token"])

    def test_claim_write_requires_ready_context(self) -> None:
        self.register()
        refused = self.call("claim_resource", agent_id="host-gemini", resource="README.md", mode="write", ttl_seconds=600)
        self.assertEqual(refused["verdict"], "CLAIM_CONTEXT_NOT_READY")
        self.assertIn("direct_file_edit", refused["forbidden"])
        ctx = self.ready_context()
        lease = self.call("pre_action_guard", agent_id="host-gemini", task_id=ctx["task_id"], context_token=ctx["context_token"], resource="README.md", planned_action="claim_resource")
        self.assertEqual(lease["verdict"], "PRE_ACTION_GUARD_OK")
        lease_id = lease["action_lease"]["lease_id"]
        claim = self.call("claim_resource", agent_id="host-gemini", resource="README.md", mode="write", ttl_seconds=600, task_id=ctx["task_id"], context_token=ctx["context_token"], action_lease_id=lease_id)
        self.assertEqual(claim["verdict"], "CLAIM_GRANTED")

    def test_workflow_next_does_not_loop_before_task_when_task_exists(self) -> None:
        self.register()
        ctx = self.call("before_task", agent_id="host-gemini", request="edit README", intent="write", resource="README.md")
        result = self.call("workflow_next", agent_id="host-gemini", request="edit README", intent="write", resource="README.md", task_id=ctx["task_id"])
        self.assertEqual(result["verdict"], "NEXT_ACTION_REQUIRED")
        self.assertEqual(result["must_call"]["tool"], "resume_task_context")
        self.assertIn("before_task", result["forbidden"])
        resumed = self.call("resume_task_context", agent_id="host-gemini", task_id=ctx["task_id"])
        next_step = self.call("workflow_next", agent_id="host-gemini", request="edit README", intent="write", resource="README.md", task_id=ctx["task_id"], context_token=resumed["context_token"], last_verdict="TASK_NOT_ACKED")
        self.assertEqual(next_step["verdict"], "NEXT_ACTION_REQUIRED")
        self.assertEqual(next_step["must_call"]["tool"], "scribe_query")

    def test_loop_breaker_forbids_direct_write(self) -> None:
        self.register()
        args = {"agent_id": "host-gemini", "request": "edit README", "intent": "write", "resource": "README.md", "last_verdict": "CLAIM_CONTEXT_NOT_READY"}
        self.call("workflow_next", **args)
        self.call("workflow_next", **args)
        result = self.call("workflow_next", **args)
        self.assertEqual(result["verdict"], "STOP_WORKFLOW_LOOP_DIRECT_WRITE_FORBIDDEN")
        self.assertIn("direct_file_edit", result["forbidden"])


if __name__ == "__main__":
    unittest.main()
