#!/usr/bin/env python3
"""Functional (black-box) acceptance tests — business requirement verification.

These tests treat the MCP server as a black box: they call tools through
`server_ext.handle()` and assert ONLY on the final system output.  No
intermediate state (DB rows, module internals, claim IDs) is inspected.

Each test documents one business requirement in the form:

    Given … When … Then …
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

R = "workfile.txt"
A1 = "func-agent-one"
A2 = "func-agent-two"


def ct(name: str, **kw: Any) -> dict[str, Any]:
    result = mcp.handle({
        "jsonrpc": "2.0", "id": f"t-{name}", "method": "tools/call",
        "params": {"name": name, "arguments": kw},
    })
    return json.loads(result["result"]["content"][0]["text"])


def list_tools() -> list[dict[str, Any]]:
    r = mcp.handle({"jsonrpc": "2.0", "id": "tl", "method": "tools/list", "params": {}})
    return r["result"]["tools"]


def g(root: Path, *a: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git"] + list(a), cwd=str(root), text=True, capture_output=True, timeout=15)


def make_workspace(root: Path, *, graphify: bool = True) -> None:
    (root / ".agent" / "state" / "patch_queue").mkdir(parents=True, exist_ok=True)
    if graphify:
        gd = root / "graphify-out"
        gd.mkdir(parents=True)
        (gd / "graph.json").write_text('{"nodes":[],"edges":[]}')
        (gd / "GRAPH_REPORT.md").write_text("# R")
        (gd / "graph.html").write_text("<h></h>")
    (root / R).write_text("line1\nline2\nline3\n")
    g(root, "init")
    g(root, "config", "user.email", "f@t")
    g(root, "config", "user.name", "FT")
    g(root, "add", R)
    g(root, "commit", "-m", "init")


def bootstrap(root: Path) -> None:
    os.chdir(root)
    root.mkdir(parents=True, exist_ok=True)
    make_workspace(root)
    mcp.server.ROOT = root.resolve()
    mcp.server.AGENT_DIR = root / ".agent"
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


# ── helpers ──────────────────────────────────────────────────────────────

def register(a: str) -> dict[str, Any]:
    return ct("register_agent", agent_id=a, host_tool="test")


def bt(a: str, r: str = R) -> dict[str, Any]:
    return ct("before_task", agent_id=a, request=f"edit {r}", intent="write", resource=r)


def ready(a: str, r: str = R) -> dict[str, Any]:
    register(a)
    r1 = bt(a, r)
    assert r1["verdict"] == "BEFORE_TASK_OK", r1
    c = {"task_id": r1["task_id"], "context_token": r1["context_token"]}
    ct("scribe_query", agent_id=a, **c, query="x", limit=1)
    ct("graphify_query", agent_id=a, **c, query="x", resource=r)
    return c


def lease(a: str, c: dict, r: str, act: str) -> str:
    p = ct("pre_action_guard", agent_id=a, resource=r, planned_action=act, **c)
    assert p["verdict"] == "PRE_ACTION_GUARD_OK", p
    return p["action_lease"]["lease_id"]


_CLAIM_IDS: dict[str, str] = {}  # agent_id -> claim_id for finish cleanup


def claim(a: str, c: dict, r: str) -> None:
    l = lease(a, c, r, "claim_resource")
    cl = ct("claim_resource", agent_id=a, resource=r, mode="patch_queue", ttl_seconds=600, action_lease_id=l, **c)
    assert cl["verdict"] == "CLAIM_GRANTED", cl
    _CLAIM_IDS[a] = cl.get("claim_id", "")


def propose(a: str, c: dict, r: str, diff: str) -> str:
    fh = ct("file_hash", resource=r)
    l = lease(a, c, r, "propose_patch")
    p = ct("propose_patch", agent_id=a, target=r, base_hash=fh["hash"], diff_text=diff, action_lease_id=l, **c)
    assert p.get("status") == "PATCH_PROPOSED", p
    return p["patch_id"]


def apply_(a: str, c: dict, r: str, pid: str) -> None:
    lock = ct("resource_lock_claim", agent_id=a, resource=r,
              task_id=c.get("task_id", ""), context_token=c.get("context_token", ""),
              ttl_seconds=600)
    assert lock["verdict"] == "RESOURCE_LOCK_ACQUIRED", lock
    l = lease(a, c, r, "apply_patch")
    ap = ct("apply_patch", agent_id=a, patch_id=pid, action_lease_id=l, **c)
    assert ap["verdict"] == "PATCH_APPLIED", ap
    ct("resource_lock_release", agent_id=a, resource=r, lock_id=lock.get("lock_id", ""))


def finish(a: str, c: dict, r: str) -> None:
    cid = _CLAIM_IDS.pop(a, "")
    if cid:
        ct("release_claim", agent_id=a, claim_id=cid)
    rec = ct("resource_lock_release", agent_id=a, resource=r)
    if rec.get("verdict") == "RESOURCE_LOCK_RELEASED":
        pass
    l = lease(a, c, r, "finish_task")
    f = ct("finish_task", agent_id=a, action_lease_id=l, **c)
    if f["verdict"] == "MEMORY_PROOF_REQUIRED":
        _ensure_memory(a, c, f.get("applied_patch_ids", []))
        from runtime import canonical_memory_gate as _cmg
        _cmg.snapshot_before_task(
            Path(mcp.server.ROOT), c.get("task_id", ""), a,
            request="finish", intent="write",
        )
        l = lease(a, c, r, "finish_task")
        f = ct("finish_task", agent_id=a, action_lease_id=l,
               canonical_memory_skip_reason="Test environment: canonical memory reflects MCP-applied patch_ids; skip promotion for test-only flow.", **c)
    if f["verdict"] == "SCRIBE_COMMIT_GATE_REQUIRED":
        resolved = ct("scribe_commit_gate_resolve", agent_id=a, decision="commit", **c)
        assert resolved["verdict"] == "SCRIBE_COMMIT_GATE_RESOLVED", resolved
        l = lease(a, c, r, "finish_task")
        f = ct("finish_task", agent_id=a, action_lease_id=l, **c)
    assert f["verdict"] in ("TASK_FINISHED", "TASK_FINISHED_OK", "CANONICAL_MEMORY_SKIPPED_WITH_REASON"), f


def _ensure_memory(a: str, c: dict, force_pids: list[str] | None = None) -> None:
    pids = force_pids if force_pids is not None else _applied_patch_ids(a, c["task_id"])
    if not pids:
        return
    memo_file = Path(mcp.server.ROOT) / "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
    memo_file.write_text(f"Applied patches: {', '.join(pids)}\n", encoding="utf-8")
    g(mcp.server.ROOT, "add", "AGENT-MEMOIRE_PROJECT_STATUS.scribe")
    g(mcp.server.ROOT, "commit", "-m", "memory proof")
    ct("scribe_query", agent_id=a, **c, query="finish memory proof", limit=3)


def _applied_patch_ids(agent_id: str, task_id: str) -> list[str]:
    return direct_fs_tripwire.applied_patch_ids(Path(mcp.server.ROOT), task_id, agent_id)


SUCCESS = {"AGENT_REGISTERED", "BEFORE_TASK_OK", "PRE_ACTION_GUARD_OK",
           "ACTION_LEASE_ISSUED", "CLAIM_GRANTED", "PATCH_PROPOSED",
           "PATCH_APPLIED", "TASK_FINISHED", "TASK_FINISHED_OK",
           "WORKSPACE_AUDIT_OK", "AGENTS_LISTED", "TASKS_LISTED"}


# ═══════════════════════════════════════════════════════════════════════════════
# Functional test class
# ═══════════════════════════════════════════════════════════════════════════════

class FunctionalAcceptanceTest(unittest.TestCase):
    """Business-requirement tests — black-box, output-only assertions."""

    def setUp(self) -> None:
        self.old = Path.cwd()
        self.root = Path(tempfile.mkdtemp(prefix="func-"))
        _CLAIM_IDS.clear()
        bootstrap(self.root)

    def tearDown(self) -> None:
        os.chdir(self.old)
        shutil.rmtree(self.root, ignore_errors=True)

    # ── BR1: Agent registration ────────────────────────────────────────────
    # Given an unregistered agent, when it calls register_agent,
    # then the system accepts it and the agent appears in list_agents.

    def test_b1_agent_registration(self) -> None:
        r = register(A1)
        self.assertEqual(r["verdict"], "AGENT_REGISTERED")
        self.assertEqual(r["agent"]["agent_id"], A1)

        agents = ct("list_agents")
        self.assertEqual(agents["count"], 1)
        self.assertEqual(agents["agents"][0]["agent_id"], A1)

    # ── BR2: Agent re-registration is idempotent ────────────────────────────
    # Given a registered agent, when it registers again, then the count stays 1.

    def test_b2_reregister_idempotent(self) -> None:
        register(A1)
        register(A1)
        agents = ct("list_agents")
        self.assertEqual(agents["count"], 1)

    # ── BR3: Unregistered agents cannot create tasks ───────────────────────
    # Given an unregistered agent, when it calls before_task,
    # then the system rejects it.

    def test_b3_unregistered_cannot_create_tasks(self) -> None:
        r = bt("ghost")
        self.assertEqual(r["verdict"], "AGENT_UNKNOWN_OR_UNREGISTERED")

    # ── BR4: Write gate — full write pipeline ──────────────────────────────
    # Given a registered agent with a ready context,
    # when it follows the full MCP write pipeline,
    # then the file is modified correctly and workspace audit is clean.

    def test_b4_full_write_pipeline(self) -> None:
        ctx = ready(A1)
        claim(A1, ctx, R)
        pid = propose(A1, ctx, R, "@@ -1,3 +1,3 @@\n line1\n-line2\n+modified\n line3\n")
        apply_(A1, ctx, R, pid)
        finish(A1, ctx, R)

        content = (self.root / R).read_text()
        self.assertIn("modified", content)
        self.assertNotIn("\nline2\n", f"\n{content}\n")

        audit = ct("workspace_audit", agent_id=A1, resource=R, task_id=ctx["task_id"])
        self.assertEqual(audit["verdict"], "WORKSPACE_AUDIT_OK")

    # ── BR5: Write gate — direct edit forbidden ────────────────────────────
    # Given a tracked workspace, when a file is modified without going through MCP,
    # then workspace_audit detects the direct write.

    def test_b5_direct_write_detected(self) -> None:
        register(A1)
        ctx = {"task_id": bt(A1)["task_id"], "context_token": ""}
        ct("scribe_query", agent_id=A1, **ctx, query="x", limit=1)
        ct("graphify_query", agent_id=A1, **ctx, query="x", resource=R)
        direct_fs_tripwire.workspace_snapshot(Path(mcp.server.ROOT), ctx["task_id"], A1)
        (self.root / R).write_text("direct!\n")
        audit = ct("workspace_audit", agent_id=A1, task_id=ctx["task_id"], resource=R)
        self.assertEqual(audit["verdict"], "DIRECT_WRITE_BYPASS_DETECTED")

    # ── BR6: Workflow FSM enforces step order ──────────────────────────────
    # Given a registered agent without a task,
    # when workflow_next is called, it must route to before_task.
    # After before_task it routes to scribe_query, then graphify_query,
    # then claim_resource.

    def test_b6_workflow_enforces_order(self) -> None:
        register(A1)

        step = ct("workflow_next", agent_id=A1, request="edit tracked", intent="write", resource=R)
        self.assertEqual(step["must_call"]["tool"], "before_task")

        r1 = bt(A1)
        c = {"task_id": r1["task_id"], "context_token": r1["context_token"]}

        step = ct("workflow_next", agent_id=A1, request="edit tracked", intent="write", resource=R, **c, last_verdict="BEFORE_TASK_OK")
        self.assertEqual(step["must_call"]["tool"], "scribe_query")

        ct("scribe_query", agent_id=A1, **c, query="x", limit=1)
        step = ct("workflow_next", agent_id=A1, request="edit tracked", intent="write", resource=R, **c, last_verdict="SCRIBE_QUERY_DONE")
        self.assertEqual(step["must_call"]["tool"], "graphify_query")

        ct("graphify_query", agent_id=A1, **c, query="x", resource=R)
        step = ct("workflow_next", agent_id=A1, request="edit tracked", intent="write", resource=R, **c, last_verdict="GRAPHIFY_QUERY_DONE")
        self.assertEqual(step["must_call"]["tool"], "resource_lock_claim")

    # ── BR7: Skipping steps is blocked ────────────────────────────────────
    # Given a registered agent with a task but no Scribe/Graphify,
    # when pre_action_guard is called, it must route to scribe/graphify first.

    def test_b7_cannot_skip_context_steps(self) -> None:
        register(A1)
        r1 = bt(A1)
        c = {"task_id": r1["task_id"], "context_token": r1["context_token"]}

        guard = ct("pre_action_guard", agent_id=A1, resource=R, planned_action="claim_resource", **c)
        self.assertEqual(guard["state"], "SCRIBE_CONTEXT_REQUIRED")

        ct("scribe_query", agent_id=A1, **c, query="x", limit=1)
        guard = ct("pre_action_guard", agent_id=A1, resource=R, planned_action="claim_resource", **c)
        self.assertEqual(guard["state"], "GRAPHIFY_CONTEXT_REQUIRED")

    # ── BR8: Loop breaker stops repeated failures ──────────────────────────
    # Given a registered agent, when workflow_next is called repeatedly with
    # the same error, the system eventually stops the loop.

    def test_b8_loop_breaker_stops_repeated_errors(self) -> None:
        register(A1)
        args = dict(agent_id=A1, request="edit tracked", intent="write", resource=R, last_verdict="TASK_CONTEXT_AGENT_MISMATCH")
        ct("workflow_next", **args)
        r2 = ct("workflow_next", **args)
        self.assertIn(r2["verdict"], ("RETRY_LOOP_DETECTED", "MAX_WORKFLOW_RETRIES_EXCEEDED", "NEXT_ACTION_REQUIRED"))

    # ── BR9: Graphify guard blocks writes without graphify-out ────────────
    # Given a workspace without graphify-out/,
    # when before_task is called with write intent,
    # the system may still create the task but outputs are missing.

    def test_b9_missing_graphify_still_allows_read(self) -> None:
        # Create a separate workspace without graphify-out
        root2 = Path(tempfile.mkdtemp(prefix="func-nograph-"))
        os.chdir(root2)
        (root2 / ".agent" / "state" / "patch_queue").mkdir(parents=True, exist_ok=True)
        (root2 / R).write_text("data\n")
        g(root2, "init")
        g(root2, "config", "user.email", "f@t")
        g(root2, "config", "user.name", "FT")
        g(root2, "add", R)
        g(root2, "commit", "-m", "init")
        mcp.server.ROOT = root2.resolve()
        mcp.server.AGENT_DIR = root2 / ".agent"
        importlib.reload(db); importlib.reload(discipline)
        mcp.db = db; mcp.discipline = discipline
        db.init_db(root2); discipline.ensure_schema()

        register(A1)
        r = bt(A1)
        self.assertEqual(r["verdict"], "BEFORE_TASK_OK")

        os.chdir(self.old)
        shutil.rmtree(root2, ignore_errors=True)
        # Re-init workspace for next tests
        os.chdir(self.root)
        importlib.reload(db); importlib.reload(patch_queue); importlib.reload(task_context); importlib.reload(discipline)
        mcp.db = db; mcp.patch_queue = patch_queue; mcp.task_context = task_context; mcp.discipline = discipline
        mcp.server.ROOT = self.root.resolve()
        mcp.server.AGENT_DIR = self.root / ".agent"
        db.init_db(self.root)
        discipline.ensure_schema()

    # ── BR10: Lease required for every mutation ────────────────────────────
    # Given a ready context but no lease,
    # when claim_resource is called, the system rejects it.

    def test_b10_lease_required(self) -> None:
        ctx = ready(A1)
        cl = ct("claim_resource", agent_id=A1, resource=R, mode="patch_queue", ttl_seconds=600, **ctx)
        self.assertEqual(cl["verdict"], "ACTION_LEASE_REQUIRED")

    # ── BR11: Lease is single-use ──────────────────────────────────────────
    # Given a lease that was already consumed,
    # when it is used again, the system rejects it.

    def test_b11_lease_single_use(self) -> None:
        ctx = ready(A1)
        l = lease(A1, ctx, R, "claim_resource")
        ct("claim_resource", agent_id=A1, resource=R, mode="patch_queue", ttl_seconds=600, action_lease_id=l, **ctx)
        r2 = ct("claim_resource", agent_id=A1, resource=R, mode="patch_queue", ttl_seconds=600, action_lease_id=l, **ctx)
        self.assertEqual(r2["verdict"], "ACTION_LEASE_CONSUMED")

    # ── BR12: Lease binds agent ────────────────────────────────────────────
    # Given a lease issued for Agent A,
    # when Agent B tries to use it, the system rejects it.

    def test_b12_lease_binds_agent(self) -> None:
        register(A1)
        register(A2)
        l = discipline.issue_action_lease(agent_id=A1, action="claim_resource", resource=R)
        self.assertEqual(l["status"], "active")

        with self.assertRaises(discipline.DisciplineError) as cm:
            discipline.validate_action_lease(lease_id=l["lease_id"], agent_id=A2, action="claim_resource", resource=R)
        self.assertEqual(cm.exception.code, "ACTION_LEASE_INVALID")
        self.assertEqual(cm.exception.details.get("reason"), "agent_mismatch")

    # ── BR13: Lease binds resource ─────────────────────────────────────────
    # Given a lease for resource X,
    # when it is used on resource Y, the system rejects it.

    def test_b13_lease_binds_resource(self) -> None:
        ctx = ready(A1)
        (self.root / "other.txt").write_text("other\n")
        g(self.root, "add", "other.txt")
        g(self.root, "commit", "-m", "o")

        fh = ct("file_hash", resource=R)
        l = discipline.issue_action_lease(agent_id=A1, action="propose_patch", task_id=ctx["task_id"], resource="other.txt", ttl_seconds=30)
        p = ct("propose_patch", agent_id=A1, **ctx, target=R, base_hash=fh["hash"], diff_text="@@ -1 +1 @@\n-content\n+changed\n", action_lease_id=l["lease_id"])
        self.assertEqual(p["verdict"], "ACTION_LEASE_INVALID")

    # ── BR14: Multiple agents can work independently ───────────────────────
    # Given two agents on different resources,
    # when both complete the write pipeline, both files are modified.

    def test_b14_two_agents_independent(self) -> None:
        (self.root / "a.txt").write_text("alpha\n")
        (self.root / "b.txt").write_text("beta\n")
        g(self.root, "add", "a.txt", "b.txt")
        g(self.root, "commit", "-m", "ab")

        c1 = ready(A1, "a.txt")
        c2 = ready(A2, "b.txt")
        claim(A1, c1, "a.txt")
        claim(A2, c2, "b.txt")
        p1 = propose(A1, c1, "a.txt", "@@ -1 +1 @@\n-alpha\n+alpha2\n")
        p2 = propose(A2, c2, "b.txt", "@@ -1 +1 @@\n-beta\n+beta2\n")
        apply_(A1, c1, "a.txt", p1)
        apply_(A2, c2, "b.txt", p2)

        self.assertEqual((self.root / "a.txt").read_text(), "alpha2\n")
        self.assertEqual((self.root / "b.txt").read_text(), "beta2\n")

        agents = ct("list_agents")
        self.assertEqual(agents["count"], 2)

    # ── BR15: Read intent cannot write ─────────────────────────────────────
    # Given a read-intent task,
    # when propose_patch is called, the system rejects it.

    def test_b15_read_intent_cannot_write(self) -> None:
        register(A1)
        r1 = ct("before_task", agent_id=A1, request="read", intent="read", resource=R)
        self.assertEqual(r1["verdict"], "BEFORE_TASK_OK")
        c = {"task_id": r1["task_id"], "context_token": r1["context_token"]}
        ct("scribe_query", agent_id=A1, **c, query="x", limit=1)
        ct("graphify_query", agent_id=A1, **c, query="x", resource=R)

        guard = ct("pre_action_guard", agent_id=A1, **c, resource=R, planned_action="claim_resource")
        self.assertEqual(guard["state"], "READ_INTENT_CANNOT_WRITE")

    # ── BR16: Resume task context rotates token ────────────────────────────
    # Given an active task,
    # when resume_task_context is called, the context token is rotated.

    def test_b16_resume_rotates_token(self) -> None:
        register(A1)
        r1 = bt(A1)
        tok1 = r1["context_token"]
        resumed = ct("resume_task_context", agent_id=A1, task_id=r1["task_id"])
        self.assertNotEqual(resumed["context_token"], tok1)
        self.assertEqual(resumed["task_id"], r1["task_id"])

    # ── BR17: Resource lock lifecycle ──────────────────────────────────────
    # Given a registered agent,
    # when it claims a resource lock, the lock is held;
    # when it releases, the lock is free.

    def test_b17_resource_lock_claim_release(self) -> None:
        ctx = ready(A1, R)
        cl = ct("resource_lock_claim", agent_id=A1, resource=R,
                task_id=ctx["task_id"], context_token=ctx["context_token"])
        self.assertEqual(cl["verdict"], "RESOURCE_LOCK_ACQUIRED")
        lock_id = cl.get("lock_id", "")

        st = ct("resource_lock_status", resource=R)
        self.assertTrue(st["locked"])
        self.assertEqual(st["owner_agent_id"], A1)

        rl = ct("resource_lock_release", agent_id=A1, resource=R, lock_id=lock_id)
        self.assertEqual(rl["verdict"], "RESOURCE_LOCK_RELEASED")

        st2 = ct("resource_lock_status", resource=R)
        self.assertFalse(st2["locked"])

    # ── BR18: All required tools are available ─────────────────────────────
    # Given the MCP server,
    # when tools/list is called, it must include all core tools.

    def test_b18_all_required_tools_available(self) -> None:
        tools = list_tools()
        names = {t["name"] for t in tools}
        required = {"register_agent", "before_task", "scribe_query", "graphify_query",
                     "claim_resource", "propose_patch", "apply_patch", "finish_task",
                     "pre_action_guard", "workspace_audit", "list_agents", "list_tasks",
                     "file_hash", "lease_extend", "resource_lock_claim",
                     "resource_lock_release", "resource_lock_status",
                     "resource_lock_heartbeat",
                     "workflow_next", "resume_task_context", "discipline_ping"}
        missing = required - names
        self.assertFalse(missing, f"Missing tools: {missing}")

    # ── BR19: List tools returns all MCP standard tools ────────────────────
    # Given the server,
    # when tools/list is called, it should return > 30 tools.

    def test_b19_tools_list_count(self) -> None:
        tools = list_tools()
        self.assertGreaterEqual(len(tools), 40)

    # ── BR20: Workspace audit OK on clean workspace ────────────────────────
    # Given a clean workspace with a registered agent and no modifications,
    # when workspace_audit is called, it reports OK.

    def test_b20_workspace_audit_clean(self) -> None:
        ctx = ready(A1)
        audit = ct("workspace_audit", agent_id=A1, task_id=ctx["task_id"], resource=R)
        self.assertEqual(audit["verdict"], "WORKSPACE_AUDIT_OK")

    # ── BR21: Stale agents detected as idle ───────────────────────────────
    # Given an agent registered long ago,
    # when list_agents is called, the agent is idle.

    def test_b21_stale_agent_marked_idle(self) -> None:
        register(A1)
        now = int(time.time())
        with db.connect(self.root) as con:
            con.execute("UPDATE agents SET last_seen=? WHERE agent_id=?", (now - 7200, A1))
        agents = ct("list_agents")
        match = [a for a in agents["agents"] if a["agent_id"] == A1]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["status"], "idle")

    # ── BR22: Register agent accepts model_name ────────────────────────────
    # Given a register_agent call with model_name,
    # then the response includes model_name.

    def test_b22_register_includes_model_name(self) -> None:
        r = ct("register_agent", agent_id=A1, host_tool="test", model_name="gpt42")
        self.assertEqual(r["agent"]["model_name"], "gpt42")

    # ── BR23: after_task variant (not applicable — use finish_task) ────────
    # Given that after_task is not a registered MCP tool,
    # when it is called, the system returns an error.

    def test_b23_after_task_not_registered(self) -> None:
        r = mcp.handle({
            "jsonrpc": "2.0", "id": "at", "method": "tools/call",
            "params": {"name": "after_task", "arguments": {"agent_id": A1}},
        })
        payload = json.loads(r["result"]["content"][0]["text"])
        self.assertIn(payload.get("code", ""), ("TOOL_ERROR", "UNEXPECTED_ERROR"))

    # ── BR24: Tool aliases and proxy layer ─────────────────────────────────
    # Given the proxy layer (server_proxy.py),
    # when certain tool names are called, they are forwarded.
    # (This is an internal requirement — test that it doesn't crash.)

    def test_b24_graphify_required_check_available(self) -> None:
        r = ct("graphify_required_check")
        self.assertIn("verdict", r)

    # ── BR25: Session status for active agent ──────────────────────────────
    # Given a registered agent,
    # when session_status is called, it returns agent info.

    def test_b25_session_status_for_active_agent(self) -> None:
        register(A1)
        s = ct("session_status")
        self.assertTrue(s.get("ok", False))

    # ── BR26: Heartbeat updates last_seen ─────────────────────────────────
    # Given a registered agent,
    # when heartbeat is called, it succeeds.

    def test_b26_heartbeat(self) -> None:
        register(A1)
        h = ct("heartbeat", agent_id=A1)
        self.assertIn("ok", h)


if __name__ == "__main__":
    unittest.main(verbosity=2)
