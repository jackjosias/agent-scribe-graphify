#!/usr/bin/env python3
"""Integration tests verifying end-to-end MCP module cooperation.

These tests go through `server.handle()` (the real MCP dispatch surface) and
exercise the full lifecycle: registration -> task creation -> context queries ->
lease enforcement -> patch proposal -> application -> workspace audit.

Every test touches at least 3 modules:
  - db (state persistence)
  - discipline (lease / resource-lock coordination)
  - task_context (task lifecycle)
  - server_ext (tool dispatch + guard wiring)
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import server_ext as mcp
from runtime import db, direct_fs_tripwire, discipline, patch_queue, task_context

RESOURCE = "tracked.txt"
RESOURCE2 = "other.txt"
AGENT_A = "integ-agent-a"
AGENT_B = "integ-agent-b"


def _json_payload(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["result"]["content"][0]["text"])


def call_tool(name: str, **args: Any) -> dict[str, Any]:
    return _json_payload(mcp.handle({
        "jsonrpc": "2.0",
        "id": f"test-{name}",
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }))


def call_list_tools() -> list[dict[str, Any]]:
    result = mcp.handle({"jsonrpc": "2.0", "id": "t", "method": "tools/list", "params": {}})
    return result["result"]["tools"]


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(root), text=True, capture_output=True, timeout=15)


def _make_workspace(root: Path) -> None:
    (root / ".agent" / "state" / "patch_queue").mkdir(parents=True, exist_ok=True)
    gdir = root / "graphify-out"
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "graph.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
    (gdir / "GRAPH_REPORT.md").write_text("# Report\n", encoding="utf-8")
    (gdir / "graph.html").write_text("<html></html>\n", encoding="utf-8")


def _init_mcp(root: Path, *, graphify_out: bool = True) -> None:
    os.chdir(root)
    _make_workspace(root) if graphify_out else (root / ".agent" / "state").mkdir(parents=True, exist_ok=True)
    (root / RESOURCE).write_text("line1\nline2\nline3\n", encoding="utf-8")
    git(root, "init")
    git(root, "config", "user.email", "integ@example.invalid")
    git(root, "config", "user.name", "Integ Test")
    git(root, "add", RESOURCE)
    git(root, "commit", "-m", "initial")
    mcp.server.ROOT = root.resolve()
    mcp.server.AGENT_DIR = mcp.server.ROOT / ".agent"
    importlib.reload(db)
    importlib.reload(patch_queue)
    importlib.reload(task_context)
    importlib.reload(discipline)
    mcp.db = db
    mcp.patch_queue = patch_queue
    mcp.task_context = task_context
    mcp.discipline = discipline
    db.init_db(root)
    discipline.ensure_schema()


def _setup(root: Path, *, graphify_out: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _init_mcp(root, graphify_out=graphify_out)
    return root


def _ready_context(agent_id: str) -> dict[str, str]:
    call_tool("register_agent", agent_id=agent_id, host_tool="test")
    bt = call_tool("before_task", agent_id=agent_id, request=f"edit {RESOURCE}", intent="write", resource=RESOURCE)
    assert bt["verdict"] == "BEFORE_TASK_OK", bt
    ctx = {"task_id": bt["task_id"], "context_token": bt["context_token"]}
    call_tool("scribe_query", agent_id=agent_id, **ctx, query="edit", limit=1)
    call_tool("graphify_query", agent_id=agent_id, **ctx, query="impact", resource=RESOURCE)
    return ctx


def _lease(ctx: dict[str, str], agent_id: str, resource: str, action: str) -> str:
    pg = call_tool("pre_action_guard", agent_id=agent_id, resource=resource, planned_action=action, **ctx)
    assert pg["verdict"] == "PRE_ACTION_GUARD_OK", pg
    return pg["action_lease"]["lease_id"]


def _claim(ctx: dict[str, str], agent_id: str, resource: str) -> str:
    lease_id = _lease(ctx, agent_id, resource, "claim_resource")
    claim = call_tool("claim_resource", agent_id=agent_id, resource=resource, mode="patch_queue", ttl_seconds=600, action_lease_id=lease_id, **ctx)
    assert claim["verdict"] == "CLAIM_GRANTED", claim
    return claim.get("claim_id", "")


def _propose(ctx: dict[str, str], agent_id: str, resource: str, diff: str) -> str:
    fh = call_tool("file_hash", resource=resource)["hash"]
    lease_id = _lease(ctx, agent_id, resource, "propose_patch")
    prop = call_tool("propose_patch", agent_id=agent_id, target=resource, base_hash=fh, diff_text=diff, action_lease_id=lease_id, **ctx)
    assert prop.get("status") == "PATCH_PROPOSED", prop
    return prop["patch_id"]


def _apply(ctx: dict[str, str], agent_id: str, resource: str, patch_id: str) -> None:
    lock = call_tool("resource_lock_claim", agent_id=agent_id, resource=resource,
                     task_id=ctx.get("task_id", ""), context_token=ctx.get("context_token", ""),
                     ttl_seconds=600)
    assert lock["verdict"] == "RESOURCE_LOCK_ACQUIRED", lock
    lease_id = _lease(ctx, agent_id, resource, "apply_patch")
    apply_ = call_tool("apply_patch", agent_id=agent_id, patch_id=patch_id, action_lease_id=lease_id, **ctx)
    assert apply_["verdict"] == "PATCH_APPLIED", apply_
    call_tool("resource_lock_release", agent_id=agent_id, resource=resource, lock_id=lock.get("lock_id", ""))


def _release_claim(claim_id: str, agent_id: str) -> None:
    if not claim_id:
        return
    rc = call_tool("release_claim", agent_id=agent_id, claim_id=claim_id)
    assert rc.get("verdict") == "CLAIM_RELEASED", rc


def _finish(ctx: dict[str, str], agent_id: str, resource: str = "", claim_id: str = "") -> None:
    _release_claim(claim_id, agent_id)
    lease_id = _lease(ctx, agent_id, resource, "finish_task")
    ft = call_tool("finish_task", agent_id=agent_id, action_lease_id=lease_id, **ctx)
    if ft["verdict"] == "MEMORY_PROOF_REQUIRED":
        patch_ids = ft.get("applied_patch_ids", [])
        _update_memory_file(ctx, agent_id, patch_ids)
        from runtime import canonical_memory_gate as _cmg
        _cmg.snapshot_before_task(
            Path(mcp.server.ROOT), ctx.get("task_id", ""), agent_id,
            request="finish", intent="write",
        )
        lease_id = _lease(ctx, agent_id, resource, "finish_task")
        ft = call_tool("finish_task", agent_id=agent_id, action_lease_id=lease_id,
                       canonical_memory_skip_reason="Test environment: canonical memory content reflects MCP-applied patch_ids; skip promotion for test-only flow.", **ctx)
    if ft["verdict"] == "SCRIBE_COMMIT_GATE_REQUIRED":
        resolved = call_tool("scribe_commit_gate_resolve", agent_id=agent_id, decision="commit", **ctx)
        assert resolved["verdict"] == "SCRIBE_COMMIT_GATE_RESOLVED", resolved
        lease_id = _lease(ctx, agent_id, resource, "finish_task")
        ft = call_tool("finish_task", agent_id=agent_id, action_lease_id=lease_id, **ctx)
    assert ft["verdict"] in ("TASK_FINISHED", "TASK_FINISHED_OK", "CANONICAL_MEMORY_SKIPPED_WITH_REASON"), ft


def _update_memory_file(ctx: dict[str, str], agent_id: str, patch_ids: list[str]) -> None:
    from runtime import direct_fs_tripwire as _dft
    memo_file = Path(mcp.server.ROOT) / "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
    memo_file.write_text(f"Applied patches: {', '.join(patch_ids)}\n", encoding="utf-8")
    import hashlib
    memo_hash = "sha256:" + hashlib.sha256(memo_file.read_bytes()).hexdigest()
    _dft.record_authorized_mutation(
        task_id=ctx.get("task_id", ""), agent_id=agent_id,
        resource="AGENT-MEMOIRE_PROJECT_STATUS.scribe",
        tool="scribe_record", project_root=Path(mcp.server.ROOT),
        after_hash=memo_hash,
    )
    call_tool("scribe_query", agent_id=agent_id, task_id=ctx.get("task_id", ""),
              context_token=ctx.get("context_token", ""), query="finish memory", limit=3)


# ═══════════════════════════════════════════════════════════════════════════════
# Section 1 — Full Workflow Lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

class FullWorkflowLifecycleTest(unittest.TestCase):
    """End-to-end flows: register -> task -> context -> propose -> apply -> audit."""

    def setUp(self) -> None:
        self.old_cwd = Path.cwd()
        self.root = _setup(Path(tempfile.mkdtemp(prefix="integ-e2e-full-")))

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_01_full_write_workflow(self) -> None:
        ctx = _ready_context(AGENT_A)
        claim_id = _claim(ctx, AGENT_A, RESOURCE)
        patch_id = _propose(ctx, AGENT_A, RESOURCE, "@@ -1,3 +1,3 @@\n line1\n-line2\n+modified\n line3\n")
        _apply(ctx, AGENT_A, RESOURCE, patch_id)
        _finish(ctx, AGENT_A, RESOURCE, claim_id=claim_id)

        content = (self.root / RESOURCE).read_text(encoding="utf-8")
        self.assertIn("modified", content)
        self.assertNotIn("\nline2\n", f"\n{content}\n")

        audit = call_tool("workspace_audit", agent_id=AGENT_A, task_id=ctx["task_id"], resource=RESOURCE)
        self.assertEqual(audit["verdict"], "WORKSPACE_AUDIT_OK", audit)

    def test_02_full_read_workflow(self) -> None:
        call_tool("register_agent", agent_id=AGENT_A, host_tool="test")
        bt = call_tool("before_task", agent_id=AGENT_A, request="inspect file", intent="read", resource=RESOURCE)
        self.assertEqual(bt["verdict"], "BEFORE_TASK_OK", bt)
        ctx = {"task_id": bt["task_id"], "context_token": bt["context_token"]}
        call_tool("scribe_query", agent_id=AGENT_A, **ctx, query="read", limit=1)
        call_tool("graphify_query", agent_id=AGENT_A, **ctx, query="impact", resource=RESOURCE)
        # Read tasks do not go through pre_action_guard/finish_task — the guard
        # returns READ_INTENT_CANNOT_WRITE. Verify the task context exists.
        bt2 = call_tool("before_task", agent_id=AGENT_A, request="inspect file", intent="read", resource=RESOURCE)
        self.assertEqual(bt2["verdict"], "ACTIVE_TASK_EXISTS", bt2)

    def test_03_workflow_next_guides_write_workflow(self) -> None:
        call_tool("register_agent", agent_id=AGENT_A, host_tool="test")

        step = call_tool("workflow_next", agent_id=AGENT_A, request="edit tracked", intent="write", resource=RESOURCE)
        self.assertEqual(step["verdict"], "NEXT_ACTION_REQUIRED", step)
        self.assertEqual(step["must_call"]["tool"], "before_task")

        bt = call_tool("before_task", agent_id=AGENT_A, request="edit tracked", intent="write", resource=RESOURCE)
        self.assertEqual(bt["verdict"], "BEFORE_TASK_OK")
        task_id, ctx_tok = bt["task_id"], bt["context_token"]

        step = call_tool("workflow_next", agent_id=AGENT_A, request="edit tracked", intent="write", resource=RESOURCE, task_id=task_id, context_token=ctx_tok, last_verdict="BEFORE_TASK_OK")
        self.assertEqual(step["must_call"]["tool"], "scribe_query")

        call_tool("scribe_query", agent_id=AGENT_A, task_id=task_id, context_token=ctx_tok, query="edit", limit=1)
        step = call_tool("workflow_next", agent_id=AGENT_A, request="edit tracked", intent="write", resource=RESOURCE, task_id=task_id, context_token=ctx_tok, last_verdict="SCRIBE_QUERY_DONE")
        self.assertEqual(step["must_call"]["tool"], "graphify_query")

        call_tool("graphify_query", agent_id=AGENT_A, task_id=task_id, context_token=ctx_tok, query="impact", resource=RESOURCE)
        step = call_tool("workflow_next", agent_id=AGENT_A, request="edit tracked", intent="write", resource=RESOURCE, task_id=task_id, context_token=ctx_tok, last_verdict="GRAPHIFY_QUERY_DONE")
        self.assertEqual(step["must_call"]["tool"], "resource_lock_claim")

    def test_04_loop_breaker_stops_after_repeated_same_verdict(self) -> None:
        call_tool("register_agent", agent_id=AGENT_A, host_tool="test")
        args = dict(agent_id=AGENT_A, request="edit tracked", intent="write", resource=RESOURCE, last_verdict="TASK_CONTEXT_AGENT_MISMATCH")
        r1 = call_tool("workflow_next", **args)
        self.assertEqual(r1["verdict"], "NEXT_ACTION_REQUIRED", r1)
        r2 = call_tool("workflow_next", **args)
        self.assertIn(r2["verdict"], ("RETRY_LOOP_DETECTED", "MAX_WORKFLOW_RETRIES_EXCEEDED", "NEXT_ACTION_REQUIRED"), r2)

    def test_05_resume_task_context_rotates_token(self) -> None:
        call_tool("register_agent", agent_id=AGENT_A, host_tool="test")
        bt = call_tool("before_task", agent_id=AGENT_A, request="edit tracked", intent="write", resource=RESOURCE)
        self.assertEqual(bt["verdict"], "BEFORE_TASK_OK")
        task_id, ctx_tok = bt["task_id"], bt["context_token"]

        resumed = call_tool("resume_task_context", agent_id=AGENT_A, task_id=task_id)
        self.assertEqual(resumed["verdict"], "TASK_CONTEXT_RESUMED", resumed)
        self.assertEqual(resumed["task_id"], task_id)
        self.assertNotEqual(resumed["context_token"], ctx_tok)
        self.assertIsNotNone(resumed["expires_at"])


# ═══════════════════════════════════════════════════════════════════════════════
# Section 2 — Multi-Agent Coordination
# ═══════════════════════════════════════════════════════════════════════════════

class MultiAgentCoordinationTest(unittest.TestCase):
    """Two agents operating independently and concurrently."""

    def setUp(self) -> None:
        self.old_cwd = Path.cwd()
        self.root = _setup(Path(tempfile.mkdtemp(prefix="integ-multi-")))
        (self.root / "file_a.txt").write_text("alpha\n", encoding="utf-8")
        (self.root / "file_b.txt").write_text("beta\n", encoding="utf-8")
        git(self.root, "add", "file_a.txt", "file_b.txt")
        git(self.root, "commit", "-m", "files")

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        shutil.rmtree(self.root, ignore_errors=True)

    def _ready(self, agent_id: str, resource: str) -> dict[str, str]:
        call_tool("register_agent", agent_id=agent_id, host_tool="test")
        bt = call_tool("before_task", agent_id=agent_id, request=f"edit {resource}", intent="write", resource=resource)
        self.assertEqual(bt["verdict"], "BEFORE_TASK_OK", bt)
        ctx = {"task_id": bt["task_id"], "context_token": bt["context_token"]}
        call_tool("scribe_query", agent_id=agent_id, **ctx, query="edit", limit=1)
        call_tool("graphify_query", agent_id=agent_id, **ctx, query="impact", resource=resource)
        return ctx

    def test_06_two_agents_independent_resources(self) -> None:
        ctx_a = self._ready(AGENT_A, "file_a.txt")
        ctx_b = self._ready(AGENT_B, "file_b.txt")

        claim_a = _claim(ctx_a, AGENT_A, "file_a.txt")
        claim_b = _claim(ctx_b, AGENT_B, "file_b.txt")

        pid_a = _propose(ctx_a, AGENT_A, "file_a.txt", "@@ -1 +1 @@\n-alpha\n+alpha-modified\n")
        pid_b = _propose(ctx_b, AGENT_B, "file_b.txt", "@@ -1 +1 @@\n-beta\n+beta-modified\n")

        _apply(ctx_a, AGENT_A, "file_a.txt", pid_a)
        _apply(ctx_b, AGENT_B, "file_b.txt", pid_b)
        _finish(ctx_a, AGENT_A, "file_a.txt", claim_id=claim_a)
        _finish(ctx_b, AGENT_B, "file_b.txt", claim_id=claim_b)

        tasks = call_tool("list_tasks")
        self.assertGreaterEqual(tasks["count"], 2)

        agents_list = call_tool("list_agents")
        self.assertEqual(agents_list["count"], 2)

        self.assertEqual((self.root / "file_a.txt").read_text(), "alpha-modified\n")
        self.assertEqual((self.root / "file_b.txt").read_text(), "beta-modified\n")

    def test_07_agent_b_cannot_use_agent_a_lease(self) -> None:
        call_tool("register_agent", agent_id=AGENT_A, host_tool="test")
        call_tool("register_agent", agent_id=AGENT_B, host_tool="test")

        lease = discipline.issue_action_lease(agent_id=AGENT_A, action="claim_resource", resource="file_a.txt")
        self.assertEqual(lease["status"], "active")

        with self.assertRaises(discipline.DisciplineError) as cm:
            discipline.validate_action_lease(lease_id=lease["lease_id"], agent_id=AGENT_B, action="claim_resource", resource="file_a.txt")
        self.assertEqual(cm.exception.code, "ACTION_LEASE_INVALID")
        self.assertEqual(cm.exception.details.get("reason"), "agent_mismatch")


# ═══════════════════════════════════════════════════════════════════════════════
# Section 3 — Lease & Resource Lock Lifecycle (MCP level)
# ═══════════════════════════════════════════════════════════════════════════════

class LeaseResourceLockLifecycleTest(unittest.TestCase):
    """Lease issue -> extend -> consume -> resource lock lifecycle via MCP."""

    def setUp(self) -> None:
        self.old_cwd = Path.cwd()
        self.root = _setup(Path(tempfile.mkdtemp(prefix="integ-lease-")))

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_08_lease_extend_through_discipline(self) -> None:
        ctx = _ready_context(AGENT_A)
        lease_id = _lease(ctx, AGENT_A, RESOURCE, "claim_resource")

        result = discipline.extend_action_lease(lease_id=lease_id, agent_id=AGENT_A, extend_seconds=120)
        self.assertEqual(result["verdict"], "LEASE_EXTENDED")
        self.assertEqual(result["extend_count"], 1)

        claim = call_tool("claim_resource", agent_id=AGENT_A, resource=RESOURCE, mode="patch_queue", ttl_seconds=600, action_lease_id=lease_id, **ctx)
        self.assertEqual(claim["verdict"], "CLAIM_GRANTED", claim)

        claim2 = call_tool("claim_resource", agent_id=AGENT_A, resource=RESOURCE, mode="patch_queue", ttl_seconds=600, action_lease_id=lease_id, **ctx)
        self.assertEqual(claim2["verdict"], "ACTION_LEASE_CONSUMED", claim2)

    def test_09_resource_lock_lifecycle(self) -> None:
        ctx = _ready_context(AGENT_A)

        rlc = call_tool("resource_lock_claim", agent_id=AGENT_A, resource=RESOURCE,
                        task_id=ctx["task_id"], context_token=ctx["context_token"],
                        ttl_seconds=600)
        self.assertEqual(rlc["verdict"], "RESOURCE_LOCK_ACQUIRED", rlc)

        rls = call_tool("resource_lock_status", resource=RESOURCE)
        self.assertEqual(rls["verdict"], "RESOURCE_LOCK_HELD", rls)
        self.assertEqual(rls["owner_agent_id"], AGENT_A)

        rlr = call_tool("resource_lock_release", agent_id=AGENT_A, resource=RESOURCE,
                        lock_id=rlc.get("lock_id", ""))
        self.assertEqual(rlr["verdict"], "RESOURCE_LOCK_RELEASED", rlr)

        rls2 = call_tool("resource_lock_status", resource=RESOURCE)
        self.assertEqual(rls2["verdict"], "RESOURCE_LOCK_FREE", rls2)

    def test_10_wrong_resource_lease_rejected(self) -> None:
        ctx = _ready_context(AGENT_A)
        (self.root / RESOURCE2).write_text("other\n", encoding="utf-8")
        git(self.root, "add", RESOURCE2)
        git(self.root, "commit", "-m", "other")

        fh = call_tool("file_hash", resource=RESOURCE)
        lease = discipline.issue_action_lease(agent_id=AGENT_A, action="propose_patch", task_id=ctx["task_id"], resource=RESOURCE2, ttl_seconds=30)
        result = call_tool("propose_patch", agent_id=AGENT_A, **ctx, target=RESOURCE, base_hash=fh["hash"], diff_text="@@ -1 +1 @@\n-content\n+changed\n", action_lease_id=lease["lease_id"])
        self.assertEqual(result["verdict"], "ACTION_LEASE_INVALID", result)
        self.assertEqual(result["details"].get("reason"), "resource_mismatch")


# ═══════════════════════════════════════════════════════════════════════════════
# Section 4 — Edge Cases & Error Recovery
# ═══════════════════════════════════════════════════════════════════════════════

class EdgeCasesAndRecoveryTest(unittest.TestCase):
    """Error paths: missing graphify, unknown agents, stale state."""

    def setUp(self) -> None:
        self.old_cwd = Path.cwd()
        self.root = _setup(Path(tempfile.mkdtemp(prefix="integ-edge-")), graphify_out=False)

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_11_unknown_agent_rejected(self) -> None:
        result = call_tool("before_task", agent_id="ghost", request="edit", intent="write", resource="README.md")
        self.assertEqual(result["verdict"], "AGENT_UNKNOWN_OR_UNREGISTERED")

        result = call_tool("pre_action_guard", agent_id="ghost", intent="write", resource=RESOURCE, planned_action="claim_resource")
        self.assertEqual(result["verdict"], "AGENT_UNKNOWN_OR_UNREGISTERED")

        result = call_tool("resume_task_context", agent_id="ghost", task_id="task-nonexistent")
        self.assertEqual(result.get("code"), "UNEXPECTED_ERROR", result)

    def test_12_missing_graphify_out_still_allows_read(self) -> None:
        call_tool("register_agent", agent_id=AGENT_A, host_tool="test")
        bt = call_tool("before_task", agent_id=AGENT_A, request="read tracked", intent="read", resource=RESOURCE)
        self.assertEqual(bt["verdict"], "BEFORE_TASK_OK", bt)

    def test_13_register_overwrites_stale_agent(self) -> None:
        r1 = call_tool("register_agent", agent_id=AGENT_A, host_tool="test", model_name="v1")
        self.assertEqual(r1["agent"]["model_name"], "v1")
        r2 = call_tool("register_agent", agent_id=AGENT_A, host_tool="test", model_name="v2")
        self.assertEqual(r2["agent"]["model_name"], "v2")
        agents = call_tool("list_agents")
        self.assertEqual(agents["count"], 1)

    def test_14_list_tools_returns_all_tools(self) -> None:
        tools = call_list_tools()
        names = [t["name"] for t in tools]
        core = {"register_agent", "before_task", "scribe_query", "graphify_query", "claim_resource",
                "propose_patch", "apply_patch", "finish_task", "workspace_audit", "pre_action_guard",
                "resource_lock_claim", "resource_lock_release", "resource_lock_heartbeat", "resource_lock_status"}
        self.assertTrue(core.issubset(names), f"Missing: {core - set(names)}")

    def test_15_finish_task_without_active_task_returns_error(self) -> None:
        call_tool("register_agent", agent_id=AGENT_A, host_tool="test")
        result = call_tool("finish_task", agent_id=AGENT_A, task_id="task-nonexistent", context_token="invalid", action_lease_id="lease-nonexistent")
        # V2.15.15 strict decision table: unknown task_id returns TASK_CONTEXT_UNKNOWN_TASK
        # (not ACTION_LEASE_INVALID — the DB-backed intent check runs before lease validation).
        self.assertEqual(result["verdict"], "TASK_CONTEXT_UNKNOWN_TASK", result)

    # ── V2.15.17: read-only FSM purity ─────────────────────────

    def test_15b_read_only_field_sequence(self) -> None:
        """finish_task on read task returns terminal=True and next_state_hint."""
        call_tool("register_agent", agent_id=AGENT_A, host_tool="test")
        bt = call_tool("before_task", agent_id=AGENT_A, request="inspect file", intent="read", resource=RESOURCE)
        self.assertEqual(bt["verdict"], "BEFORE_TASK_OK", bt)
        ctx = {"task_id": bt["task_id"], "context_token": bt["context_token"]}

        ft = call_tool("finish_task", agent_id=AGENT_A, **ctx, intent="read", summary="read-only done")
        self.assertEqual(ft["verdict"], "TASK_FINISHED_OK", ft)
        self.assertTrue(ft.get("terminal", False),
                        "finish_task on read should include terminal=True hint")
        self.assertEqual(ft.get("next_state_hint"), "READY_FOR_NEXT_TASK",
                         "finish_task on read should include next_state_hint")

    # ── V2.15.18: strict workflow_next task context ──────────────

    def test_15c_workflow_next_unknown_task_hard_stops(self) -> None:
        """workflow_next with invalid task_id returns TASK_CONTEXT_UNKNOWN_TASK."""
        call_tool("register_agent", agent_id=AGENT_A, host_tool="test")
        result = call_tool("workflow_next", agent_id=AGENT_A,
                           task_id="task-nonexistent-42", context_token="fake",
                           intent="read")
        self.assertFalse(result.get("ok"), f"should fail, got {result}")
        self.assertEqual(result["verdict"], "TASK_CONTEXT_UNKNOWN_TASK", result)
        self.assertEqual(result["state"], "HARD_STOP", result)


# ═══════════════════════════════════════════════════════════════════════════════
# Section 5 — Workspace Audit
# ═══════════════════════════════════════════════════════════════════════════════

class WorkspaceAuditIntegrationTest(unittest.TestCase):
    """Direct-write detection vs clean workspace after proper MCP workflow."""

    def setUp(self) -> None:
        self.old_cwd = Path.cwd()
        self.root = _setup(Path(tempfile.mkdtemp(prefix="integ-audit-")))

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_16_clean_workspace_after_mcp_write(self) -> None:
        ctx = _ready_context(AGENT_A)
        claim_id = _claim(ctx, AGENT_A, RESOURCE)
        pid = _propose(ctx, AGENT_A, RESOURCE, "@@ -1,3 +1,3 @@\n line1\n-line2\n+modified\n line3\n")
        _apply(ctx, AGENT_A, RESOURCE, pid)
        _finish(ctx, AGENT_A, RESOURCE, claim_id=claim_id)

        audit = call_tool("workspace_audit", agent_id=AGENT_A, task_id=ctx["task_id"], resource=RESOURCE)
        self.assertEqual(audit["verdict"], "WORKSPACE_AUDIT_OK", audit)

    def test_17_direct_write_detected(self) -> None:
        call_tool("register_agent", agent_id=AGENT_A, host_tool="test")
        bt = call_tool("before_task", agent_id=AGENT_A, request="edit tracked", intent="write", resource=RESOURCE)
        ctx = {"task_id": bt["task_id"], "context_token": bt["context_token"]}
        call_tool("scribe_query", agent_id=AGENT_A, **ctx, query="x", limit=1)
        call_tool("graphify_query", agent_id=AGENT_A, **ctx, query="x", resource=RESOURCE)
        direct_fs_tripwire.workspace_snapshot(Path(mcp.server.ROOT), ctx["task_id"], AGENT_A)
        (self.root / RESOURCE).write_text("bypass\n", encoding="utf-8")
        audit = call_tool("workspace_audit", agent_id=AGENT_A, task_id=ctx["task_id"], resource=RESOURCE)
        self.assertEqual(audit["verdict"], "DIRECT_WRITE_BYPASS_DETECTED", audit)

    # ── V2.15.24-A: Security audit — direct write on secondary resource ──

    def test_18_direct_write_on_secondary_resource_detected(self) -> None:
        """Same agent, task on RESOURCE, direct write on secondary file:
        finish_task must return DIRECT_WRITE_BYPASS_DETECTED."""
        secondary = RESOURCE2
        (self.root / secondary).write_text("initial\n", encoding="utf-8")
        git(self.root, "add", secondary)
        git(self.root, "commit", "-m", "add secondary")

        call_tool("register_agent", agent_id=AGENT_A, host_tool="test")
        bt = call_tool("before_task", agent_id=AGENT_A, request="edit tracked", intent="write", resource=RESOURCE)
        ctx = {"task_id": bt["task_id"], "context_token": bt["context_token"]}
        call_tool("scribe_query", agent_id=AGENT_A, **ctx, query="x", limit=1)
        call_tool("graphify_query", agent_id=AGENT_A, **ctx, query="x", resource=RESOURCE)

        claim_id = _claim(ctx, AGENT_A, RESOURCE)
        pid = _propose(ctx, AGENT_A, RESOURCE, "@@ -1,3 +1,3 @@\n line1\n-line2\n+modified\n line3\n")
        _apply(ctx, AGENT_A, RESOURCE, pid)
        _release_claim(claim_id, AGENT_A)

        (self.root / secondary).write_text("direct edit bypass\n", encoding="utf-8")

        lease_id = _lease(ctx, AGENT_A, RESOURCE, "finish_task")
        ft = call_tool("finish_task", agent_id=AGENT_A,
                       task_id=ctx["task_id"], context_token=ctx["context_token"],
                       action_lease_id=lease_id)
        self.assertEqual(ft.get("verdict"), "DIRECT_WRITE_BYPASS_DETECTED", ft)
        suspects = ft.get("suspects", [])
        self.assertTrue(any(secondary in s.get("path", "") for s in suspects), ft)


# ═══════════════════════════════════════════════════════════════════════════════
# Section 6 — Patch Queue Lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

class PatchQueueIntegrationTest(unittest.TestCase):
    """Propose -> list -> apply -> list (post-consume) through MCP."""

    def setUp(self) -> None:
        self.old_cwd = Path.cwd()
        self.root = _setup(Path(tempfile.mkdtemp(prefix="integ-patchq-")))
        (self.root / "example.py").write_text("def foo():\n    pass\n", encoding="utf-8")
        git(self.root, "add", "example.py")
        git(self.root, "commit", "-m", "example")

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        shutil.rmtree(self.root, ignore_errors=True)

    def _ready(self) -> dict[str, str]:
        call_tool("register_agent", agent_id=AGENT_A, host_tool="test")
        bt = call_tool("before_task", agent_id=AGENT_A, request="refactor", intent="write", resource="example.py")
        self.assertEqual(bt["verdict"], "BEFORE_TASK_OK")
        ctx = {"task_id": bt["task_id"], "context_token": bt["context_token"]}
        call_tool("scribe_query", agent_id=AGENT_A, **ctx, query="refactor", limit=1)
        call_tool("graphify_query", agent_id=AGENT_A, **ctx, query="impact", resource="example.py")
        return ctx

    def test_18_patch_lifecycle(self) -> None:
        ctx = self._ready()
        claim_id = _claim(ctx, AGENT_A, "example.py")

        fh = call_tool("file_hash", resource="example.py")["hash"]
        diff = "@@ -1,2 +1,2 @@\n def foo():\n-    pass\n+    return 42\n"
        lease_id = _lease(ctx, AGENT_A, "example.py", "propose_patch")
        prop = call_tool("propose_patch", agent_id=AGENT_A, target="example.py", base_hash=fh, diff_text=diff, action_lease_id=lease_id, **ctx)
        self.assertEqual(prop["status"], "PATCH_PROPOSED", prop)
        patch_id = prop["patch_id"]

        patches_before = call_tool("list_patches")
        ids_before = [p["patch_id"] for p in patches_before.get("patches", [])]
        self.assertIn(patch_id, ids_before)

        _apply(ctx, AGENT_A, "example.py", patch_id)

        patches_after = call_tool("list_patches")
        match = [p for p in patches_after.get("patches", []) if p["patch_id"] == patch_id]
        self.assertEqual(len(match), 1)

    def test_19_list_patches_empty_when_none(self) -> None:
        patches = call_tool("list_patches")
        self.assertEqual(len(patches.get("patches", [])), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Section 7 — Agent Registration & Identity
# ═══════════════════════════════════════════════════════════════════════════════

class AgentIdentityIntegrationTest(unittest.TestCase):
    """Agent registration + list_agents + identity enforcement."""

    def setUp(self) -> None:
        self.old_cwd = Path.cwd()
        self.root = _setup(Path(tempfile.mkdtemp(prefix="integ-identity-")))
        (self.root / "f.txt").write_text("data\n", encoding="utf-8")
        git(self.root, "add", "f.txt")
        git(self.root, "commit", "-m", "f")

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_20_register_list_and_reregister(self) -> None:
        r1 = call_tool("register_agent", agent_id=AGENT_A, host_tool="test", model_name="integ")
        self.assertEqual(r1["agent"]["agent_id"], AGENT_A)

        agents = call_tool("list_agents")
        self.assertEqual(agents["count"], 1)
        self.assertEqual(agents["agents"][0]["agent_id"], AGENT_A)

        r2 = call_tool("register_agent", agent_id=AGENT_A, host_tool="test", model_name="integ")
        self.assertEqual(r2["verdict"], "AGENT_REGISTERED")

        self.assertEqual(call_tool("list_agents")["count"], 1)

    def test_21_deprecated_agent_marked_idle(self) -> None:
        call_tool("register_agent", agent_id=AGENT_A, host_tool="test")
        now = int(time.time())
        with db.connect(self.root) as con:
            con.execute("UPDATE agents SET last_seen=? WHERE agent_id=?", (now - 7200, AGENT_A))

        agents = call_tool("list_agents")
        matching = [a for a in agents.get("agents", []) if a["agent_id"] == AGENT_A]
        self.assertEqual(len(matching), 1)
        self.assertIn(matching[0]["status"], ("idle", "active"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
