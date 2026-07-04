#!/usr/bin/env python3
"""End-to-end tests for V2.12 host discipline guard.

These tests intentionally go through the MCP dispatch surface (`server.handle`).
Assertions on local constants do not count as system proof for this contract.
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

import server_ext as mcp  # type: ignore
from runtime import db, direct_fs_tripwire, discipline, patch_queue, task_context  # type: ignore


RESOURCE = "tracked.txt"
AGENT = "host-discipline-test-agent"


def _json_payload(result: dict[str, Any]) -> dict[str, Any]:
    payload = result["result"]["content"][0]["text"]
    return json.loads(payload)


def call_tool(name: str, **args: Any) -> dict[str, Any]:
    return _json_payload(mcp.handle({
        "jsonrpc": "2.0",
        "id": f"test-{name}",
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }))


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(root), text=True, capture_output=True, timeout=15)


class HostDisciplineGuardE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="host-discipline-e2e-"))
        self.old_cwd = Path.cwd()
        os.chdir(self.root)
        os.environ["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(self.root)
        (self.root / ".agent" / "state").mkdir(parents=True, exist_ok=True)
        (self.root / ".agent" / "state" / "patch_queue").mkdir(parents=True, exist_ok=True)
        graphify_dir = self.root / "graphify-out"
        graphify_dir.mkdir(parents=True, exist_ok=True)
        (graphify_dir / "graph.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        (graphify_dir / "GRAPH_REPORT.md").write_text("# Graphify Report\n\nEmpty.\n", encoding="utf-8")
        (graphify_dir / "graph.html").write_text("<html><body></body></html>\n", encoding="utf-8")
        (self.root / RESOURCE).write_text("line1\n", encoding="utf-8")
        git(self.root, "init")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "config", "user.name", "Host Discipline Test")
        git(self.root, "add", RESOURCE)
        git(self.root, "commit", "-m", "initial")
        mcp.server.ROOT = self.root.resolve()
        mcp.server.AGENT_DIR = mcp.server.ROOT / ".agent"
        importlib.reload(db)
        importlib.reload(patch_queue)
        importlib.reload(task_context)
        importlib.reload(discipline)
        mcp.db = db
        mcp.patch_queue = patch_queue
        mcp.task_context = task_context
        mcp.discipline = discipline
        # Clear the graphify guard global cache so a blocking result from an
        # earlier test in the suite does not poison this test's guard check.
        mcp._GRAPHIFY_GUARD_CACHE.clear()
        db.init_db(self.root)
        discipline.ensure_schema()

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        shutil.rmtree(self.root, ignore_errors=True)

    def register(self, agent_id: str = AGENT) -> dict[str, Any]:
        payload = call_tool("register_agent", host_tool="test", agent_id=agent_id)
        self.assertEqual(payload["verdict"], "AGENT_REGISTERED")
        return payload

    def before_task(self, agent_id: str = AGENT, requires_graphify: bool = True) -> dict[str, str]:
        request = "fix production code path" if requires_graphify else "inspect note"
        intent = "fix" if requires_graphify else "write"
        payload = call_tool("before_task", agent_id=agent_id, request=request, intent=intent, resource=RESOURCE)
        self.assertEqual(payload["verdict"], "BEFORE_TASK_OK", payload)
        return {"task_id": payload["task_id"], "context_token": payload["context_token"]}

    def ready_context(self) -> dict[str, str]:
        self.register()
        ctx = self.before_task(requires_graphify=True)
        payload = call_tool("scribe_query", agent_id=AGENT, task_id=ctx["task_id"], context_token=ctx["context_token"], query="host discipline", limit=3)
        self.assertIn(payload["verdict"], {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}, payload)
        payload = call_tool("graphify_query", agent_id=AGENT, task_id=ctx["task_id"], context_token=ctx["context_token"], query="host discipline", resource=RESOURCE)
        self.assertIn(payload["verdict"], {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}, payload)
        return ctx

    def lease(self, action: str, ctx: dict[str, str], resource: str = RESOURCE) -> str:
        payload = call_tool("pre_action_guard", agent_id=AGENT, intent="write", resource=resource, planned_action=action, **ctx)
        self.assertEqual(payload["verdict"], "PRE_ACTION_GUARD_OK", payload)
        self.assertEqual(payload["state"], "ACTION_LEASE_ISSUED", payload)
        self.assertIn("direct_file_edit", payload["forbidden"])
        return payload["action_lease"]["lease_id"]

    def claim(self, ctx: dict[str, str]) -> None:
        lease_id = self.lease("claim_resource", ctx)
        payload = call_tool("claim_resource", agent_id=AGENT, resource=RESOURCE, mode="patch_queue", ttl_seconds=600, action_lease_id=lease_id, **ctx)
        self.assertEqual(payload["verdict"], "CLAIM_GRANTED", payload)

    def test_pre_action_guard_unknown_agent_returns_AGENT_UNKNOWN_OR_UNREGISTERED(self) -> None:
        payload = call_tool("pre_action_guard", agent_id="missing-agent", intent="write", resource=RESOURCE, planned_action="propose_patch")
        self.assertEqual(payload["verdict"], "AGENT_UNKNOWN_OR_UNREGISTERED", payload)
        self.assertEqual(payload["state"], "AGENT_UNKNOWN_OR_UNREGISTERED", payload)
        self.assertEqual(payload["must_call"]["tool"], "register_agent", payload)
        self.assertIn("direct_file_edit", payload["forbidden"])

    def test_pre_action_guard_active_agent_without_task_routes_to_before_task(self) -> None:
        self.register()
        payload = call_tool("pre_action_guard", agent_id=AGENT, request="fix file", intent="write", resource=RESOURCE, planned_action="propose_patch")
        self.assertEqual(payload["verdict"], "NEXT_ACTION_REQUIRED", payload)
        self.assertEqual(payload["state"], "BEFORE_TASK_REQUIRED", payload)
        self.assertEqual(payload["must_call"]["tool"], "before_task", payload)
        self.assertIn("direct_file_edit", payload["forbidden"])

    def test_pre_action_guard_task_without_scribe_routes_to_scribe_query(self) -> None:
        self.register()
        ctx = self.before_task(requires_graphify=True)
        payload = call_tool("pre_action_guard", agent_id=AGENT, request="fix file", intent="write", resource=RESOURCE, planned_action="claim_resource", **ctx)
        self.assertEqual(payload["verdict"], "NEXT_ACTION_REQUIRED", payload)
        self.assertEqual(payload["state"], "SCRIBE_CONTEXT_REQUIRED", payload)
        self.assertEqual(payload["must_call"]["tool"], "scribe_query", payload)

    def test_pre_action_guard_task_without_graphify_routes_to_graphify_query(self) -> None:
        self.register()
        ctx = self.before_task(requires_graphify=True)
        payload = call_tool("scribe_query", agent_id=AGENT, task_id=ctx["task_id"], context_token=ctx["context_token"], query="host discipline", limit=3)
        self.assertIn(payload["verdict"], {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}, payload)
        payload = call_tool("pre_action_guard", agent_id=AGENT, request="fix file", intent="write", resource=RESOURCE, planned_action="claim_resource", **ctx)
        self.assertEqual(payload["verdict"], "NEXT_ACTION_REQUIRED", payload)
        self.assertEqual(payload["state"], "GRAPHIFY_CONTEXT_REQUIRED", payload)
        self.assertEqual(payload["must_call"]["tool"], "graphify_query", payload)

    def test_pre_action_guard_ready_task_issues_action_lease(self) -> None:
        ctx = self.ready_context()
        lease_id = self.lease("claim_resource", ctx)
        self.assertTrue(lease_id.startswith("lease-"))

    def test_sensitive_tool_without_lease_returns_ACTION_LEASE_REQUIRED(self) -> None:
        ctx = self.ready_context()
        payload = call_tool("claim_resource", agent_id=AGENT, resource=RESOURCE, mode="patch_queue", ttl_seconds=600, **ctx)
        self.assertEqual(payload["verdict"], "ACTION_LEASE_REQUIRED", payload)
        self.assertEqual(payload["state"], "ACTION_LEASE_REQUIRED", payload)
        self.assertEqual(payload["must_call"]["tool"], "pre_action_guard", payload)
        self.assertIn("direct_file_edit", payload["forbidden"])

    def test_sensitive_tool_with_valid_lease_succeeds(self) -> None:
        ctx = self.ready_context()
        self.claim(ctx)
        base = call_tool("file_hash", resource=RESOURCE)["hash"]
        lease_id = self.lease("propose_patch", ctx)
        payload = call_tool("propose_patch", agent_id=AGENT, target=RESOURCE, base_hash=base, diff_text="@@ -1,1 +1,1 @@\n-line1\n+line2\n", action_lease_id=lease_id, **ctx)
        self.assertEqual(payload["status"], "PATCH_PROPOSED", payload)

    def test_same_lease_reuse_returns_ACTION_LEASE_CONSUMED(self) -> None:
        ctx = self.ready_context()
        lease_id = self.lease("claim_resource", ctx)
        first = call_tool("claim_resource", agent_id=AGENT, resource=RESOURCE, mode="patch_queue", ttl_seconds=600, action_lease_id=lease_id, **ctx)
        self.assertEqual(first["verdict"], "CLAIM_GRANTED", first)
        second = call_tool("claim_resource", agent_id=AGENT, resource=RESOURCE, mode="patch_queue", ttl_seconds=600, action_lease_id=lease_id, **ctx)
        self.assertEqual(second["verdict"], "ACTION_LEASE_CONSUMED", second)
        self.assertEqual(second["state"], "ACTION_LEASE_CONSUMED", second)

    def test_wrong_resource_lease_returns_ACTION_LEASE_INVALID(self) -> None:
        ctx = self.ready_context()
        (self.root / "other.txt").write_text("other\n", encoding="utf-8")
        git(self.root, "add", "other.txt")
        git(self.root, "commit", "-m", "other")
        lease = discipline.issue_action_lease(agent_id=AGENT, action="propose_patch", task_id=ctx["task_id"], resource="other.txt", intent="write", ttl_seconds=30)
        base = call_tool("file_hash", resource=RESOURCE)["hash"]
        payload = call_tool("propose_patch", agent_id=AGENT, target=RESOURCE, base_hash=base, diff_text="@@ -1,1 +1,1 @@\n-line1\n+line2\n", action_lease_id=lease["lease_id"], **ctx)
        self.assertEqual(payload["verdict"], "ACTION_LEASE_INVALID", payload)
        self.assertEqual(payload["details"].get("reason"), "resource_mismatch", payload)

    def test_expired_lease_returns_ACTION_LEASE_EXPIRED(self) -> None:
        ctx = self.ready_context()
        lease = discipline.issue_action_lease(agent_id=AGENT, action="claim_resource", task_id=ctx["task_id"], resource=RESOURCE, intent="write", ttl_seconds=1)
        time.sleep(2.2)
        payload = call_tool("claim_resource", agent_id=AGENT, resource=RESOURCE, mode="patch_queue", ttl_seconds=600, action_lease_id=lease["lease_id"], **ctx)
        self.assertEqual(payload["verdict"], "ACTION_LEASE_EXPIRED", payload)
        self.assertEqual(payload["state"], "ACTION_LEASE_EXPIRED", payload)

    def test_workspace_audit_detects_real_direct_write_on_tracked_file(self) -> None:
        self.register()
        ctx = self.before_task()
        call_tool("scribe_query", agent_id=AGENT, **ctx, query="x", limit=3)
        call_tool("graphify_query", agent_id=AGENT, **ctx, query="x", resource=RESOURCE)
        direct_fs_tripwire.workspace_snapshot(Path(mcp.server.ROOT), ctx["task_id"], AGENT)
        (self.root / RESOURCE).write_text("direct edit\n", encoding="utf-8")
        payload = call_tool("workspace_audit", agent_id=AGENT, task_id=ctx["task_id"], resource=RESOURCE)
        self.assertEqual(payload["verdict"], "DIRECT_WRITE_BYPASS_DETECTED", payload)
        self.assertEqual(payload["state"], "WORKSPACE_AUDIT_REQUIRED", payload)
        self.assertTrue(any(s.get("path") == RESOURCE for s in payload.get("suspects", [])), f"{RESOURCE} not in suspects: {payload.get('suspects')}")
        self.assertIn("direct_file_edit", payload["forbidden"])

    def test_workspace_audit_ok_after_no_direct_write(self) -> None:
        self.register()
        ctx = self.before_task()
        call_tool("scribe_query", agent_id=AGENT, **ctx, query="x", limit=3)
        call_tool("graphify_query", agent_id=AGENT, **ctx, query="x", resource=RESOURCE)
        direct_fs_tripwire.workspace_snapshot(Path(mcp.server.ROOT), ctx["task_id"], AGENT)
        payload = call_tool("workspace_audit", agent_id=AGENT, task_id=ctx["task_id"], resource=RESOURCE)
        self.assertEqual(payload["verdict"], "WORKSPACE_AUDIT_OK", payload)
        self.assertEqual(payload["state"], "WORKSPACE_AUDIT_OK", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
