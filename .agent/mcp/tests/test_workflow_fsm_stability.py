from __future__ import annotations

import json
import os
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
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True, exist_ok=True)
        graphify_dir = self.root / "graphify-out"
        graphify_dir.mkdir(parents=True)
        (graphify_dir / "graph.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        (graphify_dir / "GRAPH_REPORT.md").write_text("# Graphify Report\n\nEmpty.\n", encoding="utf-8")
        (graphify_dir / "graph.html").write_text("<html><body></body></html>\n", encoding="utf-8")
        (self.root / "README.md").write_text("test project\n", encoding="utf-8")
        self.entry = self.root / ".agent" / "mcp" / "server_entry.py"
        # Pin workspace root so server_entry.py ignores leaked env var from
        # earlier test modules (e.g. test_lease_extend.py).
        self._sub_env = {**os.environ, "AGENT_SCRIBE_GRAPHIFY_ROOT": str(self.root)}
        subprocess.run(["git", "init"], cwd=str(self.root), capture_output=True, env=self._sub_env)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(self.root), capture_output=True, env=self._sub_env)
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(self.root), capture_output=True, env=self._sub_env)

    def tearDown(self) -> None:
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

    # ── V2.15.17: read-only FSM purity ─────────────────────────

    def test_resume_agent_not_recommended_during_active_read_task(self) -> None:
        """workflow_next never returns resume_agent during an active read task."""
        agent_id = "no-resume-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="inspect", intent="read", resource="README.md")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        ctx = bt["context_token"]

        for i in range(5):
            wn = self.call("workflow_next", agent_id=agent_id, request="inspect", intent="read",
                            task_id=task_id, context_token=ctx, last_verdict="BEFORE_TASK_OK")
            must_tool = wn.get("must_call", {}).get("tool", "")
            self.assertNotEqual(must_tool, "resume_agent",
                                f"iteration {i}: unexpected resume_agent during read task: {wn}")

    def test_write_task_still_requires_claim_and_lease(self) -> None:
        """Write task still requires claim + pre_action_guard for finish."""
        agent_id = "write-lease-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="edit tracked", intent="write", resource="README.md")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        ctx = bt["context_token"]

        self.call("scribe_query", agent_id=agent_id, task_id=task_id, context_token=ctx, query="edit", limit=1)
        self.call("graphify_query", agent_id=agent_id, task_id=task_id, context_token=ctx, query="impact", resource="README.md")

        wn = self.call("workflow_next", agent_id=agent_id, request="edit tracked", intent="write",
                        resource="README.md",
                        task_id=task_id, context_token=ctx, last_verdict="GRAPHIFY_QUERY_DONE")
        self.assertEqual(wn.get("must_call", {}).get("tool"), "resource_lock_claim",
                         f"write workflow_next should require resource_lock_claim, got {wn}")

    def test_fake_read_cannot_downgrade_write_task(self) -> None:
        """Stored write intent wins over declared read — claim still required."""
        agent_id = "fake-read-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="edit tracked", intent="write", resource="README.md")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        ctx = bt["context_token"]

        self.call("scribe_query", agent_id=agent_id, task_id=task_id, context_token=ctx, query="edit", limit=1)
        self.call("graphify_query", agent_id=agent_id, task_id=task_id, context_token=ctx, query="impact", resource="README.md")

        wn = self.call("workflow_next", agent_id=agent_id, request="inspect", intent="read",
                        resource="README.md",
                        task_id=task_id, context_token=ctx, last_verdict="GRAPHIFY_QUERY_DONE")
        self.assertEqual(wn.get("must_call", {}).get("tool"), "resource_lock_claim",
                         f"stored write intent should win over declared read, got {wn}")

    # ── V2.15.18: strict workflow_next task context + file_hash cleanup ──

    def test_workflow_next_unknown_task_id_hard_stops(self) -> None:
        """Unknown task_id returns TASK_CONTEXT_UNKNOWN_TASK / HARD_STOP."""
        agent_id = "strict-ctx-agent"
        self.register(agent_id)

        wn = self.call("workflow_next", agent_id=agent_id,
                        task_id="missing-task-42", context_token="fake",
                        intent="read")
        self.assertFalse(wn.get("ok"), f"should fail, got {wn}")
        self.assertEqual(wn.get("verdict"), "TASK_CONTEXT_UNKNOWN_TASK",
                         f"expected TASK_CONTEXT_UNKNOWN_TASK, got {wn.get('verdict')}")
        self.assertEqual(wn.get("state"), "HARD_STOP")
        self.assertNotIn("must_call", wn,
                         "must_call should be absent on unknown task_id")
        forbidden = wn.get("forbidden", [])
        self.assertIn("before_task", forbidden)
        self.assertIn("claim_resource", forbidden)
        self.assertIn("finish_task", forbidden)

    def test_workflow_next_agent_mismatch_hard_stops(self) -> None:
        """Agent B calling workflow_next with Agent A's task_id returns TASK_AGENT_MISMATCH."""
        agent_a = "mismatch-a"
        agent_b = "mismatch-b"
        self.register(agent_a)
        self.register(agent_b)

        bt = self.call("before_task", agent_id=agent_a, request="edit", intent="write", resource="README.md")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]

        wn = self.call("workflow_next", agent_id=agent_b,
                        task_id=task_id, context_token="irrelevant",
                        intent="read")
        self.assertFalse(wn.get("ok"), f"should fail, got {wn}")
        self.assertEqual(wn.get("verdict"), "TASK_AGENT_MISMATCH",
                         f"expected TASK_AGENT_MISMATCH, got {wn.get('verdict')}")
        self.assertEqual(wn.get("state"), "HARD_STOP")

    def test_workflow_next_unknown_task_does_not_clear_task_id_to_legacy(self) -> None:
        """Unknown task_id with valid request/intent/resource still hard-stops."""
        agent_id = "no-legacy-agent"
        self.register(agent_id)

        wn = self.call("workflow_next", agent_id=agent_id,
                        task_id="ghost-task", context_token="irrelevant",
                        request="inspect", intent="read", resource=".")
        self.assertFalse(wn.get("ok"), f"should hard-stop, got {wn}")
        self.assertEqual(wn.get("verdict"), "TASK_CONTEXT_UNKNOWN_TASK",
                         f"expected TASK_CONTEXT_UNKNOWN_TASK, got {wn.get('verdict')}")
        self.assertNotEqual(wn.get("verdict"), "INPUT_REQUIRED",
                            "must not fall back to INPUT_REQUIRED")
        self.assertNotEqual(wn.get("verdict"), "BEFORE_TASK_REQUIRED",
                            "must not fall back to BEFORE_TASK_REQUIRED")

    def test_read_only_forbidden_does_not_include_file_hash(self) -> None:
        """file_hash is not in the forbidden list during read task workflow_next."""
        agent_id = "no-fh-forbid-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="inspect", intent="read", resource=".")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        ctx = bt["context_token"]

        wn = self.call("workflow_next", agent_id=agent_id, request="inspect", intent="read",
                        task_id=task_id, context_token=ctx, last_verdict="BEFORE_TASK_OK")
        forbidden = wn.get("forbidden", [])
        self.assertNotIn("file_hash", forbidden,
                         f"file_hash should be allowed in read-only, got forbidden={forbidden}")

    def test_file_hash_allowed_during_read_task(self) -> None:
        """file_hash call succeeds during a read task."""
        agent_id = "fh-allowed-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="hash check", intent="read", resource="README.md")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")

        fh = self.call("file_hash", resource="README.md")
        self.assertTrue(fh.get("ok", True), f"file_hash should work, got {fh}")
        self.assertIn("hash", fh, f"file_hash response should include hash, got {fh}")
        self.assertIn("verdict", fh, f"file_hash response should include verdict, got {fh}")


if __name__ == "__main__":
    unittest.main()
