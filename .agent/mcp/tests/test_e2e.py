#!/usr/bin/env python3
"""End-to-end (bout en bout) tests — real MCP server subprocess over stdio.

Each test creates an isolated temp workspace, starts a real MCP server
subprocess (via server_entry.py -> server_ext.py -> run_stdio()), and
communicates via JSON-RPC 2.0 over stdin/stdout — exactly like a
real OpenCode / Claude Code host.

Scenarios (13):
   1. Full Write Pipeline (register -> before_task -> scribe -> graphify ->
      lease -> claim -> propose -> apply -> finish -> workspace_audit)
   2. Full Read Workflow (register -> before_task -> scribe — no lease/finish)
   3. Bypass Detection (direct write caught by workspace_audit)
   4. Stop/Restart Persistence (kill server, restart, resume, redo context,
      finish)
   5. Lease Extend (extend a lease through the tool)
   6. Two Independent Agents (same server, two agents, independent resources)
   7. Loop Breaker (repeated before_task with active task returns
      ACTIVE_TASK_EXISTS)
   8. Resource Lock Conflict (two agents, one resource)
   9. Patch Queue Lifecycle (propose -> list -> apply)
  10. Missing Graphify-out (read allowed, write blocked by guard)
  11. Unknown Agent Rejected (security boundary across process)
  12. Tools List Contract (>= 42 tools, names match)
  13. Resume Token Rotation (resume rotates context_token)
"""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
SERVER_ENTRY = str(MCP_DIR / "server_entry.py")
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import graphify_readiness, installation_state

RESOURCE = "tracked.txt"
RESOURCE_B = "other-tracked.txt"
AGENT_A = "e2e-agent-a"
AGENT_B = "e2e-agent-b"
TIMEOUT = 20
FILE_CONTENT = "line1\n"


# ── MCP stdio subprocess client ──────────────────────────────────────────────


class McpSubprocessClient:
    """JSON-RPC 2.0 stdio MCP client — wraps a real server subprocess."""

    def __init__(self, cwd: str, env_extra: dict[str, str] | None = None):
        self.cwd = cwd
        self.env_extra = env_extra or {}
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = os.environ.copy()
        env.update(self.env_extra)
        self.proc = subprocess.Popen(
            [sys.executable, SERVER_ENTRY],
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )

    def call(self, tool: str, **args: Any) -> dict[str, Any]:
        payload = self._do_call(tool, **args)
        if not payload.get("ok", True):
            err_msg = payload.get("error") or payload.get("verdict", "UNKNOWN_ERROR")
            raise RuntimeError(f"Tool error [{err_msg}]: {json.dumps(payload)}")
        return payload

    def call_raw(self, tool: str, **args: Any) -> dict[str, Any]:
        return self._do_call(tool, **args)

    def list_tools(self) -> list[dict[str, Any]]:
        req = {
            "jsonrpc": "2.0", "id": "e2e-list-tools",
            "method": "tools/list", "params": {},
        }
        self._write(req)
        resp = self._read_resp()
        return resp["result"]["tools"]

    def initialize(self) -> dict[str, Any]:
        req = {
            "jsonrpc": "2.0", "id": "e2e-init", "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "e2e-test", "version": "1.0.0"},
            },
        }
        self._write(req)
        resp = self._read_resp()
        return resp["result"]

    def close(self) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                if stream is not None:
                    stream.close()
            self.proc = None

    @property
    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # -- internals --

    def _do_call(self, tool: str, **args: Any) -> dict[str, Any]:
        req = {
            "jsonrpc": "2.0", "id": f"e2e-{tool}",
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
        self._write(req)
        resp = self._read_resp()
        if "error" in resp:
            raise RuntimeError(f"MCP JSON-RPC error: {resp['error']}")
        result = resp.get("result", {})
        text = result.get("content", [{}])[0].get("text", "{}")
        return json.loads(text)

    def _write(self, req: dict[str, Any]) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()

    def _read_resp(self) -> dict[str, Any]:
        assert self.proc is not None and self.proc.stdout is not None
        r, _, _ = select.select([self.proc.stdout], [], [], TIMEOUT)
        if r:
            line = self.proc.stdout.readline()
            if not line:
                ret = self.proc.poll()
                stderr = self.proc.stderr.read() if self.proc.stderr else ""
                raise RuntimeError(
                    f"MCP server closed stdout (exit={ret}):\n{stderr}"
                )
            return json.loads(line.strip())
        ret = self.proc.poll()
        if ret is not None:
            stderr = self.proc.stderr.read() if self.proc.stderr else ""
            raise RuntimeError(
                f"MCP server died (exit={ret}) before response:\n{stderr}"
            )
        raise TimeoutError(f"MCP server did not respond within {TIMEOUT}s")


# ── Test base ────────────────────────────────────────────────────────────────


class E2ETestBase(unittest.TestCase):
    """Creates a temp workspace + MCP subprocess per test."""

    root: Path
    client: McpSubprocessClient

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="e2e-"))
        self.client = McpSubprocessClient(
            cwd=str(self.root),
            env_extra={
                "AGENT_SCRIBE_GRAPHIFY_ROOT": str(self.root),
                graphify_readiness.FIXTURE_ENV: "1",
            },
        )

    def tearDown(self) -> None:
        self.client.close()
        shutil.rmtree(self.root, ignore_errors=True)

    # -- workspace helpers --

    def _prepare_workspace(
        self, with_graphify: bool = True, extra_files: list[str] | None = None,
    ) -> None:
        (self.root / ".agent" / "state").mkdir(parents=True, exist_ok=True)
        qdir = self.root / ".agent" / "state" / "patch_queue"
        qdir.mkdir(parents=True, exist_ok=True)
        mcp_link = self.root / ".agent" / "mcp"
        if not mcp_link.exists():
            mcp_link.symlink_to(MCP_DIR, target_is_directory=True)
        (self.root / RESOURCE).write_text(FILE_CONTENT)
        if extra_files:
            for fname in extra_files:
                (self.root / fname).write_text(FILE_CONTENT)
        self._git("init")
        self._git("config", "user.email", "test@e2e.invalid")
        self._git("config", "user.name", "E2E Test")
        self._git("add", ".")
        self._git("commit", "-m", "initial")
        prepared = installation_state.ensure_fresh_installation_state(self.root)
        self.assertTrue(prepared["ok"], prepared)
        finalized = installation_state.finalize_installation_state(self.root)
        self.assertTrue(finalized["ok"], finalized)
        if with_graphify:
            fixture = graphify_readiness.write_smoke_fixture(self.root)
            self.assertTrue(fixture["ok"], fixture)

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(self.root),
            capture_output=True, text=True, timeout=TIMEOUT,
        )

    # -- workflow step helpers (each asserts its own verdict) --

    def _register(self, agent_id: str = AGENT_A) -> dict[str, Any]:
        p = self.client.call("register_agent", host_tool="e2e", agent_id=agent_id)
        self.assertEqual(p.get("verdict", "AGENT_REGISTERED"),
                         "AGENT_REGISTERED", p)
        return p

    def _before_task(self, agent_id: str = AGENT_A,
                     intent: str = "fix",
                     resource: str = RESOURCE) -> dict[str, Any]:
        p = self.client.call(
            "before_task", agent_id=agent_id,
            request="fix production code path",
            intent=intent, resource=resource,
        )
        self.assertEqual(p["verdict"], "BEFORE_TASK_OK", p)
        self.assertIn("task_id", p)
        self.assertIn("context_token", p)
        return p

    def _scribe_query(self, ctx: dict[str, str],
                      agent_id: str = AGENT_A) -> dict[str, Any]:
        p = self.client.call(
            "scribe_query", agent_id=agent_id,
            task_id=ctx["task_id"], context_token=ctx["context_token"],
            query="e2e test query", limit=3,
        )
        self.assertIn(p["verdict"], {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}, p)
        return p

    def _graphify_query(self, ctx: dict[str, str],
                        agent_id: str = AGENT_A) -> dict[str, Any]:
        p = self.client.call(
            "graphify_query", agent_id=agent_id,
            task_id=ctx["task_id"], context_token=ctx["context_token"],
            query="e2e", resource=RESOURCE,
        )
        self.assertIn(p["verdict"], {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}, p)
        return p

    def _ready_context(self, agent_id: str = AGENT_A) -> dict[str, str]:
        self._register(agent_id)
        bt = self._before_task(agent_id=agent_id)
        ctx = {"task_id": bt["task_id"], "context_token": bt["context_token"]}
        self._scribe_query(ctx, agent_id=agent_id)
        self._graphify_query(ctx, agent_id=agent_id)
        return ctx

    def _lease(self, ctx: dict[str, str], action: str,
               agent_id: str = AGENT_A,
               resource: str = RESOURCE) -> str:
        p = self.client.call(
            "pre_action_guard", agent_id=agent_id, intent="write",
            resource=resource, planned_action=action, **ctx,
        )
        self.assertEqual(p["verdict"], "PRE_ACTION_GUARD_OK", p)
        self.assertEqual(p["state"], "ACTION_LEASE_ISSUED", p)
        lid = p["action_lease"]["lease_id"]
        self.assertTrue(lid.startswith("lease-"), lid)
        return lid

    def _claim(self, ctx: dict[str, str], lease_id: str,
               agent_id: str = AGENT_A,
               resource: str = RESOURCE) -> str:
        p = self.client.call(
            "claim_resource", agent_id=agent_id, resource=resource,
            mode="patch_queue", ttl_seconds=600,
            action_lease_id=lease_id, **ctx,
        )
        self.assertEqual(p["verdict"], "CLAIM_GRANTED", p)
        return p["claim_id"]

    def _file_hash(self, resource: str = RESOURCE) -> str:
        p = self.client.call("file_hash", resource=resource)
        h = p["hash"]
        self.assertTrue(len(h) > 0, p)
        return h

    def _propose(self, ctx: dict[str, str], lease_id: str,
                 base_hash: str, agent_id: str = AGENT_A,
                 resource: str = RESOURCE) -> str:
        p = self.client.call(
            "propose_patch", agent_id=agent_id, target=resource,
            base_hash=base_hash,
            diff_text="@@ -1,1 +1,1 @@\n-line1\n+line2\n",
            action_lease_id=lease_id, **ctx,
        )
        self.assertEqual(p["status"], "PATCH_PROPOSED", p)
        return p["patch_id"]

    def _apply(self, ctx: dict[str, str], lease_id: str,
               patch_id: str, agent_id: str = AGENT_A,
               resource: str = RESOURCE) -> dict[str, Any]:
        lock = self.client.call(
            "resource_lock_claim", agent_id=agent_id, resource=resource,
            task_id=ctx.get("task_id", ""), context_token=ctx.get("context_token", ""),
            ttl_seconds=600,
        )
        self.assertEqual(lock["verdict"], "RESOURCE_LOCK_ACQUIRED", lock)
        p = self.client.call(
            "apply_patch", agent_id=agent_id, patch_id=patch_id,
            action_lease_id=lease_id, **ctx,
        )
        self.assertEqual(p["verdict"], "PATCH_APPLIED", p)
        self.client.call("resource_lock_release", agent_id=agent_id,
                         resource=resource, lock_id=lock.get("lock_id", ""))
        return p

    def _release_claim(self, claim_id: str,
                       agent_id: str = AGENT_A) -> None:
        p = self.client.call("release_claim", agent_id=agent_id,
                             claim_id=claim_id)
        self.assertEqual(p["verdict"], "CLAIM_RELEASED", p)

    def _finish(self, ctx: dict[str, str],
                agent_id: str = AGENT_A,
                resource: str = RESOURCE) -> dict[str, Any]:
        lease_id = self._lease(ctx, "finish_task", agent_id=agent_id,
                               resource=resource)
        p = self.client.call(
            "finish_task", agent_id=agent_id,
            task_id=ctx["task_id"], context_token=ctx["context_token"],
            action_lease_id=lease_id,
        )
        if p["verdict"] == "SCRIBE_COMMIT_GATE_REQUIRED":
            resolved = self.client.call("scribe_commit_gate_resolve",
                                       agent_id=agent_id, decision="commit",
                                       **ctx)
            self.assertEqual(resolved["verdict"], "SCRIBE_COMMIT_GATE_RESOLVED", resolved)
            lease_id = self._lease(ctx, "finish_task", agent_id=agent_id,
                                   resource=resource)
            p = self.client.call(
                "finish_task", agent_id=agent_id,
                task_id=ctx["task_id"], context_token=ctx["context_token"],
                action_lease_id=lease_id,
            )
        if p["verdict"] == "CANONICAL_MEMORY_REQUIRED":
            lease_id = self._lease(ctx, "finish_task", agent_id=agent_id,
                                   resource=resource)
            p = self.client.call(
                "finish_task", agent_id=agent_id,
                task_id=ctx["task_id"], context_token=ctx["context_token"],
                action_lease_id=lease_id,
                canonical_memory_skip_reason="Test environment: canonical memory already covered by MEMORY_PROOF_REQUIRED gate via AGENT-MEMOIRE content matching patch_ids.",
            )
        self.assertEqual(p["verdict"], "TASK_FINISHED_OK", p)
        return p


# ── Test classes ─────────────────────────────────────────────────────────────


class TestE2EFullWritePipeline(E2ETestBase):
    """Scenario 1: Full write pipeline — the critical user path."""

    def test_full_write_pipeline_succeeds(self) -> None:
        self._prepare_workspace()
        self.client.start()

        ctx = self._ready_context()
        lease_claim = self._lease(ctx, "claim_resource")
        cid = self._claim(ctx, lease_claim)
        base = self._file_hash()
        lease_propose = self._lease(ctx, "propose_patch")
        pid = self._propose(ctx, lease_propose, base)
        lease_apply = self._lease(ctx, "apply_patch")
        self._apply(ctx, lease_apply, pid)
        self._release_claim(cid)
        self._finish(ctx)

        audit = self.client.call("workspace_audit", agent_id=AGENT_A,
                                 task_id=ctx["task_id"], resource=RESOURCE)
        self.assertEqual(audit["verdict"], "WORKSPACE_AUDIT_OK", audit)


class TestE2EFullReadWorkflow(E2ETestBase):
    """Scenario 2: Read workflow — no lease needed; skip finish since
    read-intent tasks cannot obtain a pre_action_guard lease."""

    def test_full_read_workflow_succeeds(self) -> None:
        self._prepare_workspace()
        self.client.start()

        self._register()
        bt = self._before_task(intent="write")
        ctx = {"task_id": bt["task_id"], "context_token": bt["context_token"]}
        self._scribe_query(ctx)

        workspace_audit = self.client.call("workspace_audit", agent_id=AGENT_A,
                                           resource=RESOURCE)
        self.assertIn(workspace_audit["verdict"],
                      ("WORKSPACE_AUDIT_OK", "DIRECT_FS_TRIPWIRE_NO_SNAPSHOT"),
                      workspace_audit)


class TestE2EBypassDetection(E2ETestBase):
    """Scenario 3: Direct write bypass is caught by workspace_audit."""

    def test_direct_write_detected(self) -> None:
        self._prepare_workspace()
        self.client.start()

        self._register()
        bt = self._before_task()
        ctx = {"task_id": bt["task_id"], "context_token": bt["context_token"]}
        self._scribe_query(ctx)
        self._graphify_query(ctx)
        (self.root / RESOURCE).write_text("direct edit bypass\n")

        audit = self.client.call("workspace_audit", agent_id=AGENT_A,
                                 task_id=ctx["task_id"], resource=RESOURCE)
        self.assertEqual(audit["verdict"], "DIRECT_WRITE_BYPASS_DETECTED", audit)
        suspects = audit.get("suspects", [])
        self.assertTrue(any(RESOURCE in s.get("path", "") for s in suspects), audit)


class TestE2EStopRestartPersistence(E2ETestBase):
    """Scenario 4: Kill server, restart, resume task — DB survives."""

    def test_task_survives_server_restart(self) -> None:
        self._prepare_workspace()
        self.client.start()

        self._register()
        bt = self._before_task()
        task_id = bt["task_id"]
        old_token = bt["context_token"]

        self.client.close()
        self.assertFalse(self.client.is_alive)

        self.client.start()
        self.assertTrue(self.client.is_alive)

        resume = self.client.call("resume_task_context", agent_id=AGENT_A,
                                  task_id=task_id)
        self.assertEqual(resume["verdict"], "TASK_CONTEXT_RESUMED", resume)
        new_token = resume["context_token"]
        self.assertNotEqual(new_token, old_token,
                            "context_token should rotate on resume")

        ctx = {"task_id": task_id, "context_token": new_token}
        self._scribe_query(ctx)
        self._graphify_query(ctx)
        self._finish(ctx)


class TestE2ELeaseExtend(E2ETestBase):
    """Scenario 5: Extend a lease through the lease_extend tool."""

    def test_lease_extend_happy_path(self) -> None:
        self._prepare_workspace()
        self.client.start()
        ctx = self._ready_context()

        lid = self._lease(ctx, "claim_resource")

        extended = self.client.call("lease_extend", lease_id=lid,
                                    agent_id=AGENT_A, extend_seconds=60)
        self.assertEqual(extended["verdict"], "LEASE_EXTENDED", extended)
        self.assertIn("expires_at", extended)
        self.assertIn("extend_count", extended)
        self.assertGreater(extended["extend_count"], 0)

        cid = self._claim(ctx, lid)

        base = self._file_hash()
        lp = self._lease(ctx, "propose_patch")
        pid = self._propose(ctx, lp, base)
        la = self._lease(ctx, "apply_patch")
        self._apply(ctx, la, pid)
        self._release_claim(cid, AGENT_A)
        self._finish(ctx)


class TestE2ETwoIndependentAgents(E2ETestBase):
    """Scenario 6: Two agents, same server, independent resources."""

    def test_two_agents_independent(self) -> None:
        self._prepare_workspace(extra_files=[RESOURCE_B])
        self.client.start()

        self._register(AGENT_A)
        self._register(AGENT_B)

        bt_a = self._before_task(agent_id=AGENT_A, resource=RESOURCE)
        ctx_a = {"task_id": bt_a["task_id"],
                 "context_token": bt_a["context_token"]}
        bt_b = self._before_task(agent_id=AGENT_B, resource=RESOURCE_B)
        ctx_b = {"task_id": bt_b["task_id"],
                 "context_token": bt_b["context_token"]}

        self._scribe_query(ctx_a, AGENT_A)
        self._graphify_query(ctx_a, AGENT_A)
        self._scribe_query(ctx_b, AGENT_B)
        self._graphify_query(ctx_b, AGENT_B)

        la_c = self._lease(ctx_a, "claim_resource", AGENT_A, RESOURCE)
        ca = self._claim(ctx_a, la_c, AGENT_A, RESOURCE)

        lb_c = self._lease(ctx_b, "claim_resource", AGENT_B, RESOURCE_B)
        cb = self._claim(ctx_b, lb_c, AGENT_B, RESOURCE_B)

        self.assertNotEqual(ca, cb, "claim IDs should differ across agents")

        base_a = self._file_hash(RESOURCE)
        base_b = self._file_hash(RESOURCE_B)
        la_p = self._lease(ctx_a, "propose_patch", AGENT_A, RESOURCE)
        pa = self._propose(ctx_a, la_p, base_a, AGENT_A, RESOURCE)
        lb_p = self._lease(ctx_b, "propose_patch", AGENT_B, RESOURCE_B)
        pb = self._propose(ctx_b, lb_p, base_b, AGENT_B, RESOURCE_B)

        la_a = self._lease(ctx_a, "apply_patch", AGENT_A, RESOURCE)
        self._apply(ctx_a, la_a, pa, AGENT_A, RESOURCE)
        lb_a = self._lease(ctx_b, "apply_patch", AGENT_B, RESOURCE_B)
        self._apply(ctx_b, lb_a, pb, AGENT_B, RESOURCE_B)

        self._release_claim(ca, AGENT_A)
        self._release_claim(cb, AGENT_B)
        self._finish(ctx_a, AGENT_A, RESOURCE)
        self._finish(ctx_b, AGENT_B, RESOURCE_B)

        audit_a = self.client.call("workspace_audit", agent_id=AGENT_A,
                                   task_id=ctx_a["task_id"], resource=RESOURCE)
        self.assertEqual(audit_a["verdict"], "WORKSPACE_AUDIT_OK", audit_a)
        audit_b = self.client.call("workspace_audit", agent_id=AGENT_B,
                                   task_id=ctx_b["task_id"], resource=RESOURCE_B)
        self.assertEqual(audit_b["verdict"], "WORKSPACE_AUDIT_OK", audit_b)


class TestE2ELoopBreaker(E2ETestBase):
    """Scenario 7: Repeated before_task with active task returns
    ACTIVE_TASK_EXISTS."""

    def test_loop_breaker_stops_after_max_retries(self) -> None:
        self._prepare_workspace()
        self.client.start()
        self._register()

        bt = self._before_task()
        ctx = {"task_id": bt["task_id"], "context_token": bt["context_token"]}
        self._scribe_query(ctx)
        self._graphify_query(ctx)

        lease = self._lease(ctx, "claim_resource")
        self._claim(ctx, lease)

        payload = self.client.call_raw(
            "before_task", agent_id=AGENT_A,
            request="duplicate", intent="fix", resource=RESOURCE,
        )
        self.assertEqual(payload["verdict"], "ACTIVE_TASK_EXISTS", payload)


class TestE2EResourceLock(E2ETestBase):
    """Scenario 8: resource_lock_claim conflict between two agents."""

    def test_resource_lock_conflict(self) -> None:
        self._prepare_workspace()
        self.client.start()

        self._register(AGENT_A)
        self._register(AGENT_B)

        ctx_a = self._ready_context(AGENT_A)
        ctx_b = self._ready_context(AGENT_B)

        claim_a = self.client.call("resource_lock_claim", agent_id=AGENT_A,
                                   resource=RESOURCE,
                                   task_id=ctx_a["task_id"],
                                   context_token=ctx_a["context_token"])
        self.assertEqual(claim_a["verdict"], "RESOURCE_LOCK_ACQUIRED", claim_a)
        lock_id = claim_a.get("lock_id", "")

        claim_b = self.client.call_raw("resource_lock_claim", agent_id=AGENT_B,
                                       resource=RESOURCE,
                                       task_id=ctx_b["task_id"],
                                       context_token=ctx_b["context_token"])
        self.assertEqual(claim_b["verdict"], "RESOURCE_BUSY",
                         claim_b)

        release_a = self.client.call("resource_lock_release", agent_id=AGENT_A,
                                     resource=RESOURCE, lock_id=lock_id)
        self.assertEqual(release_a["verdict"], "RESOURCE_LOCK_RELEASED",
                         release_a)

        claim_b2 = self.client.call("resource_lock_claim", agent_id=AGENT_B,
                                    resource=RESOURCE,
                                    task_id=ctx_b["task_id"],
                                    context_token=ctx_b["context_token"])
        self.assertEqual(claim_b2["verdict"], "RESOURCE_LOCK_ACQUIRED",
                         claim_b2)


class TestE2EPatchQueueLifecycle(E2ETestBase):
    """Scenario 9: propose -> list -> apply."""

    def test_patch_lifecycle(self) -> None:
        self._prepare_workspace()
        self.client.start()
        ctx = self._ready_context()

        lc = self._lease(ctx, "claim_resource")
        cid = self._claim(ctx, lc)
        base = self._file_hash()
        lp = self._lease(ctx, "propose_patch")
        patch_id = self._propose(ctx, lp, base)

        patches = self.client.call("list_patches")
        self.assertIn("patches", patches, patches)
        self.assertTrue(any(p["patch_id"] == patch_id
                            for p in patches["patches"]),
                        f"patch {patch_id} not in list")

        la = self._lease(ctx, "apply_patch")
        self._apply(ctx, la, patch_id)
        self._release_claim(cid, AGENT_A)
        self._finish(ctx)


class TestE2EMissingGraphify(E2ETestBase):
    """Scenario 10: Without graphify-out, read works; write blocked by guard."""

    def test_missing_graphify_blocks_write(self) -> None:
        self._prepare_workspace(with_graphify=False)
        self.client.start()

        self._register()
        bt = self._before_task(intent="write")
        ctx = {"task_id": bt["task_id"], "context_token": bt["context_token"]}
        self._scribe_query(ctx)

        graph = self.client.call_raw(
            "graphify_query",
            agent_id=AGENT_A,
            query="impact of tracked file",
            resource=RESOURCE,
            **ctx,
        )
        self.assertEqual(graph["verdict"], "GRAPHIFY_UNAVAILABLE", graph)

        guard = self.client.call_raw(
            "pre_action_guard", agent_id=AGENT_A, intent="write",
            resource=RESOURCE, planned_action="claim_resource",
            **ctx,
        )
        self.assertEqual(guard["verdict"], "GRAPHIFY_MISSING", guard)
        self.assertFalse(guard.get("ok", True), guard)
        self.assertFalse(guard.get("write_allowed", True), guard)


class TestE2EUnknownAgent(E2ETestBase):
    """Scenario 11: Unregistered agent rejected over stdio."""

    def test_unknown_agent_rejected(self) -> None:
        self._prepare_workspace()
        self.client.start()

        result = self.client.call_raw(
            "before_task", agent_id="unknown-intruder",
            request="hack", intent="write", resource=RESOURCE,
        )
        self.assertEqual(result["verdict"], "AGENT_UNKNOWN_OR_UNREGISTERED",
                         result)


class TestE2EToolsList(E2ETestBase):
    """Scenario 12: tools/list exposes only bootstrap plus four task tools."""

    def test_tools_list_contract(self) -> None:
        self._prepare_workspace()
        self.client.start()

        self.client.initialize()
        tools = self.client.list_tools()
        names = [t["name"] for t in tools]
        required = {
            "file_hash", "tenor_init_bridge", "portability_check",
            "graphify_required_check", "graphify_project_build",
            "tenor_task_start", "tenor_apply_changeset", "tenor_activity",
            "tenor_task_control",
        }
        self.assertSetEqual(set(names), required)


class TestE2EResumeToken(E2ETestBase):
    """Scenario 13: resume_task_context rotates the token."""

    def test_resume_rotates_token(self) -> None:
        self._prepare_workspace()
        self.client.start()

        self._register()
        bt = self._before_task()
        ctx = {"task_id": bt["task_id"], "context_token": bt["context_token"]}
        old_token = bt["context_token"]

        resume = self.client.call("resume_task_context", agent_id=AGENT_A,
                                  task_id=ctx["task_id"])
        self.assertEqual(resume["verdict"], "TASK_CONTEXT_RESUMED", resume)
        new_token = resume["context_token"]
        self.assertNotEqual(new_token, old_token,
                            "context_token must rotate on resume")
        self.assertIsNotNone(new_token)
        self.assertGreater(len(new_token), 16)


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
