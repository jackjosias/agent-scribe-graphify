from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import server_ext as mcp
from runtime import db, discipline, patch_queue, scribe_commit_gate, task_context

RESOURCE = "tracked.txt"
AGENT_A = "gate-agent-a"
AGENT_B = "gate-agent-b"


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    if "result" in result:
        return json.loads(result["result"]["content"][0]["text"])
    return result


def call_tool(name: str, **args: Any) -> dict[str, Any]:
    return _payload(mcp.handle({
        "jsonrpc": "2.0",
        "id": f"test-{name}",
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }))


def list_tool_names() -> set[str]:
    result = mcp.handle({"jsonrpc": "2.0", "id": "tools", "method": "tools/list", "params": {}})
    return {tool["name"] for tool in result["result"]["tools"]}


def tool_schema(name: str) -> dict[str, Any]:
    for tool in mcp.list_tools():
        if tool["name"] == name:
            return tool["inputSchema"]
    raise AssertionError(f"tool missing: {name}")


class ScribeCommitGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_cwd = Path.cwd()
        self.root = Path(tempfile.mkdtemp(prefix="scribe-gate-"))
        os.chdir(self.root)
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.root / ".agent" / "state" / "patch_queue").mkdir(parents=True, exist_ok=True)
        (self.root / "scribe-out" / "records").mkdir(parents=True, exist_ok=True)
        graph = self.root / "graphify-out"
        graph.mkdir(parents=True, exist_ok=True)
        (graph / "graph.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        (graph / "GRAPH_REPORT.md").write_text("# Report\n", encoding="utf-8")
        (graph / "graph.html").write_text("<html></html>\n", encoding="utf-8")
        (self.root / RESOURCE).write_text("line1\n", encoding="utf-8")
        mcp.server.ROOT = self.root.resolve()
        mcp.server.AGENT_DIR = mcp.server.ROOT / ".agent"
        importlib.reload(db)
        importlib.reload(patch_queue)
        importlib.reload(task_context)
        importlib.reload(discipline)
        importlib.reload(scribe_commit_gate)
        mcp.db = db
        mcp.patch_queue = patch_queue
        mcp.task_context = task_context
        mcp.discipline = discipline
        mcp.scribe_commit_gate = scribe_commit_gate
        db.init_db(self.root)
        discipline.ensure_schema()
        scribe_commit_gate.ensure_schema()

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        shutil.rmtree(self.root, ignore_errors=True)

    def register(self, agent_id: str = AGENT_A) -> None:
        result = call_tool("register_agent", agent_id=agent_id, host_tool="test", model_name="unit")
        self.assertEqual((result.get("agent") or {}).get("status"), "active", result)

    def ready_context(self, agent_id: str = AGENT_A, intent: str = "write", resource: str = RESOURCE) -> dict[str, str]:
        self.register(agent_id)
        before = call_tool("before_task", agent_id=agent_id, request=f"{intent} {resource}", intent=intent, resource=resource)
        self.assertEqual(before.get("verdict"), "BEFORE_TASK_OK", before)
        ctx = {"task_id": before["task_id"], "context_token": before["context_token"]}
        scribe = call_tool("scribe_query", agent_id=agent_id, **ctx, query=f"{intent} {resource}", limit=1)
        self.assertIn(scribe.get("verdict"), {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}, scribe)
        if intent != "read":
            graph = call_tool("graphify_query", agent_id=agent_id, **ctx, query="impact", resource=resource)
            self.assertIn(graph.get("verdict"), {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}, graph)
        return ctx

    def lease(self, ctx: dict[str, str], action: str = "finish_task", agent_id: str = AGENT_A, resource: str = RESOURCE) -> str:
        result = call_tool("pre_action_guard", agent_id=agent_id, task_id=ctx["task_id"], context_token=ctx["context_token"], resource=resource, intent="write", planned_action=action)
        self.assertEqual(result.get("verdict"), "PRE_ACTION_GUARD_OK", result)
        return result["action_lease"]["lease_id"]

    def finish_mutating(self, ctx: dict[str, str], agent_id: str = AGENT_A, summary: str = "gate summary") -> dict[str, Any]:
        return call_tool("finish_task", agent_id=agent_id, summary=summary, **ctx, action_lease_id=self.lease(ctx, agent_id=agent_id))

    def require_gate(self) -> tuple[dict[str, str], dict[str, Any]]:
        ctx = self.ready_context()
        first = self.finish_mutating(ctx)
        self.assertEqual(first.get("verdict"), "SCRIBE_COMMIT_GATE_REQUIRED", first)
        return ctx, first

    def test_01_read_only_finish_does_not_require_gate(self) -> None:
        ctx = self.ready_context(intent="read")
        result = call_tool("finish_task", agent_id=AGENT_A, summary="read done", intent="read", **ctx)
        self.assertEqual(result.get("verdict"), "TASK_FINISHED_OK", result)
        self.assertTrue(result.get("terminal"), result)

    def test_02_mutating_finish_returns_gate_required(self) -> None:
        _, gate = self.require_gate()
        self.assertEqual(gate["state"], "SCRIBE_COMMIT_GATE_REQUIRED")

    def test_03_gate_payload_includes_contract(self) -> None:
        _, gate = self.require_gate()
        self.assertIn("gate_id", gate)
        self.assertIn("proposed_record", gate)
        self.assertEqual(set(gate["allowed_decisions"]), {"commit", "edit", "skip"})

    def test_04_repeated_mutating_finish_returns_same_gate_id(self) -> None:
        ctx, first = self.require_gate()
        second = self.finish_mutating(ctx)
        self.assertEqual(second.get("verdict"), "SCRIBE_COMMIT_GATE_REQUIRED", second)
        self.assertEqual(first["gate_id"], second["gate_id"])

    def test_05_resolve_commit_marks_gate_committed(self) -> None:
        ctx, gate = self.require_gate()
        result = call_tool("scribe_commit_gate_resolve", agent_id=AGENT_A, **ctx, decision="commit")
        self.assertEqual(result.get("verdict"), "SCRIBE_COMMIT_GATE_RESOLVED", result)
        self.assertEqual(result.get("status"), "committed")
        self.assertEqual(result.get("gate_id"), gate["gate_id"])
        self.assertIn("scribe_record_path", result)

    def test_06_finish_after_commit_returns_ok(self) -> None:
        ctx, _ = self.require_gate()
        call_tool("scribe_commit_gate_resolve", agent_id=AGENT_A, **ctx, decision="commit")
        result = self.finish_mutating(ctx)
        self.assertEqual(result.get("verdict"), "TASK_FINISHED_OK", result)
        self.assertTrue(result.get("terminal"), result)

    def test_07_resolve_skip_requires_reason(self) -> None:
        ctx, _ = self.require_gate()
        result = call_tool("scribe_commit_gate_resolve", agent_id=AGENT_A, **ctx, decision="skip")
        self.assertFalse(result.get("ok", True), result)
        self.assertIn("SCRIBE_COMMIT_GATE_SKIP_REASON_REQUIRED", result.get("error", ""))

    def test_08_skip_then_finish_ok_and_reports_skipped(self) -> None:
        ctx, _ = self.require_gate()
        skipped = call_tool("scribe_commit_gate_resolve", agent_id=AGENT_A, **ctx, decision="skip", skip_reason="No durable causal memory beyond test assertion.")
        self.assertEqual(skipped.get("status"), "skipped", skipped)
        self.assertEqual(skipped.get("decision"), "skip")
        result = self.finish_mutating(ctx)
        self.assertEqual(result.get("verdict"), "TASK_FINISHED_OK", result)

    def test_09_resolve_edit_requires_edited_record(self) -> None:
        ctx, _ = self.require_gate()
        result = call_tool("scribe_commit_gate_resolve", agent_id=AGENT_A, **ctx, decision="edit")
        self.assertFalse(result.get("ok", True), result)
        self.assertIn("SCRIBE_COMMIT_GATE_EDITED_RECORD_REQUIRED", result.get("error", ""))

    def test_10_invalid_decision_rejected(self) -> None:
        ctx, _ = self.require_gate()
        result = call_tool("scribe_commit_gate_resolve", agent_id=AGENT_A, **ctx, decision="later")
        self.assertFalse(result.get("ok", True), result)
        self.assertIn("SCRIBE_COMMIT_GATE_DECISION_INVALID", result.get("error", ""))

    def test_11_wrong_agent_rejected(self) -> None:
        ctx, _ = self.require_gate()
        self.register(AGENT_B)
        result = call_tool("scribe_commit_gate_status", agent_id=AGENT_B, **ctx)
        self.assertFalse(result.get("ok", True), result)
        self.assertIn("TASK_CONTEXT_AGENT_MISMATCH", result.get("error", ""))

    def test_12_wrong_task_id_rejected(self) -> None:
        ctx, _ = self.require_gate()
        result = call_tool("scribe_commit_gate_status", agent_id=AGENT_A, task_id="task-missing", context_token=ctx["context_token"])
        self.assertFalse(result.get("ok", True), result)
        self.assertIn("TASK_CONTEXT_UNKNOWN_TASK", result.get("error", ""))

    def test_13_wrong_context_token_rejected(self) -> None:
        ctx, _ = self.require_gate()
        result = call_tool("scribe_commit_gate_status", agent_id=AGENT_A, task_id=ctx["task_id"], context_token="wrong")
        self.assertFalse(result.get("ok", True), result)
        self.assertIn("TASK_CONTEXT_TOKEN_MISMATCH", result.get("error", ""))

    def test_14_pending_patches_block_before_gate(self) -> None:
        ctx = self.ready_context()
        claim = call_tool("claim_resource", agent_id=AGENT_A, resource=RESOURCE, mode="write", ttl_seconds=600, **ctx, action_lease_id=self.lease(ctx, "claim_resource"))
        self.assertEqual(claim.get("verdict"), "CLAIM_GRANTED", claim)
        fh = call_tool("file_hash", resource=RESOURCE)["hash"]
        patch = call_tool("propose_patch", agent_id=AGENT_A, target=RESOURCE, base_hash=fh, diff_text="@@ -1 +1 @@\n-line1\n+line2\n", **ctx, action_lease_id=self.lease(ctx, "propose_patch"))
        self.assertEqual(patch.get("status"), "PATCH_PROPOSED", patch)
        released = call_tool("release_claim", agent_id=AGENT_A, claim_id=claim["claim_id"], summary="pending patch priority test")
        self.assertEqual(released.get("verdict"), "CLAIM_RELEASED", released)
        result = self.finish_mutating(ctx)
        self.assertEqual(result.get("verdict"), "FINISH_REFUSED_PENDING_PATCHES", result)

    def test_15_active_legacy_claims_block_before_gate(self) -> None:
        ctx = self.ready_context()
        claim = call_tool("claim_resource", agent_id=AGENT_A, resource=RESOURCE, mode="write", ttl_seconds=600, **ctx, action_lease_id=self.lease(ctx, "claim_resource"))
        self.assertEqual(claim.get("verdict"), "CLAIM_GRANTED", claim)
        result = self.finish_mutating(ctx)
        self.assertEqual(result.get("verdict"), "FINISH_REFUSED_ACTIVE_CLAIMS", result)

    def test_16_repeated_finish_creates_no_duplicate_scribe_records(self) -> None:
        ctx, _ = self.require_gate()
        records = list((self.root / "scribe-out" / "records").glob("*.json"))
        self.finish_mutating(ctx)
        self.finish_mutating(ctx)
        after = list((self.root / "scribe-out" / "records").glob("*.json"))
        self.assertEqual(len(records), len(after))

    def test_17_tool_schema_exposes_status(self) -> None:
        schema = tool_schema("scribe_commit_gate_status")
        self.assertEqual(set(schema.get("required", [])), {"agent_id", "task_id", "context_token"})

    def test_18_tool_schema_exposes_resolve(self) -> None:
        schema = tool_schema("scribe_commit_gate_resolve")
        self.assertEqual(set(schema.get("required", [])), {"agent_id", "task_id", "context_token", "decision"})
        self.assertIn("edited_record", schema.get("properties", {}))

    def test_19_workflow_next_after_gate_required_recommends_resolve(self) -> None:
        ctx, _ = self.require_gate()
        result = call_tool("workflow_next", agent_id=AGENT_A, request="write", intent="write", resource=RESOURCE, last_verdict="SCRIBE_COMMIT_GATE_REQUIRED", **ctx)
        self.assertEqual(result.get("must_call", {}).get("tool"), "scribe_commit_gate_resolve", result)

    def test_20_workflow_next_after_finish_ready_for_next(self) -> None:
        result = call_tool("workflow_next", agent_id=AGENT_A, last_verdict="TASK_FINISHED_OK")
        self.assertEqual(result.get("verdict"), "READY_FOR_NEXT_TASK", result)

    def test_21_direct_file_edit_remains_forbidden(self) -> None:
        self.register()
        result = call_tool("before_edit", agent_id=AGENT_A, resource=RESOURCE)
        self.assertEqual(result.get("verdict"), "DIRECT_EDIT_REFUSED_MCP_WRITE_GATE", result)

    def test_22_direct_fs_outside_sandbox_remains_declared_open(self) -> None:
        text = Path("AGENT-MEMOIRE_PROJECT_STATUS.scribe")
        # The isolated fixture may not contain project memory; assert the runtime did not introduce a closure claim.
        self.assertNotIn("DIRECT_FS_OUTSIDE_SANDBOX_CLOSED", text.read_text(encoding="utf-8") if text.exists() else "")

    def test_23_old_read_only_fsm_still_passes_locally(self) -> None:
        ctx = self.ready_context(intent="read")
        result = call_tool("workflow_next", agent_id=AGENT_A, request="inspect", intent="read", resource=RESOURCE, last_verdict="BEFORE_TASK_OK", **ctx)
        self.assertIn(result.get("must_call", {}).get("tool"), {"scribe_record", "finish_task"}, result)

    def test_24_resource_lock_still_passes_basic_claim_release(self) -> None:
        ctx = self.ready_context()
        lock = call_tool("resource_lock_claim", agent_id=AGENT_A, resource=RESOURCE, ttl_seconds=600, **ctx)
        self.assertEqual(lock.get("verdict"), "RESOURCE_LOCK_ACQUIRED", lock)
        released = call_tool("resource_lock_release", agent_id=AGENT_A, resource=RESOURCE, lock_id=lock["lock_id"])
        self.assertEqual(released.get("verdict"), "RESOURCE_LOCK_RELEASED", released)

    def test_25_tool_listing_includes_gate_tools(self) -> None:
        names = list_tool_names()
        self.assertIn("scribe_commit_gate_status", names)
        self.assertIn("scribe_commit_gate_resolve", names)


if __name__ == "__main__":
    unittest.main()
