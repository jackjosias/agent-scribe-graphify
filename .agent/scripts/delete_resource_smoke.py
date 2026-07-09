#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / ".agent" / "mcp" / "server_entry.py"


def fail(message: str) -> None:
    raise SystemExit(f"DELETE_RESOURCE_SMOKE_FAIL: {message}")


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    proc = subprocess.run([sys.executable, str(ENTRY), "--call", name, "--args", json.dumps(args)], cwd=str(ROOT), text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        try:
            return json.loads(proc.stderr or proc.stdout)
        except json.JSONDecodeError:
            fail(f"{name} failed\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}")
    outer = json.loads(proc.stdout)
    if "content" in outer:
        return json.loads(outer["content"][0]["text"])
    return outer


def task_context_args(before: dict[str, Any]) -> dict[str, str]:
    return {"task_id": before["task_id"], "context_token": before["context_token"]}


def acquire_lease(agent_id: str, action: str, ctx: dict[str, str], resource: str = "tmp-delete-resource-smoke/delete-me.txt") -> str:
    lease = call_tool("pre_action_guard", {
        "agent_id": agent_id,
        "intent": "write",
        "resource": resource,
        "planned_action": action,
        **ctx,
    })
    if lease.get("verdict") != "PRE_ACTION_GUARD_OK" or lease.get("state") != "ACTION_LEASE_ISSUED":
        fail(f"pre_action_guard failed for {action}: {lease}")
    return lease["action_lease"]["lease_id"]


def main() -> int:
    work = ROOT / "tmp-delete-resource-smoke"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    target = work / "delete-me.txt"
    target.write_text("temporary\n", encoding="utf-8")
    try:
        boot = call_tool("bootstrap", {"host_tool": "delete-resource-smoke", "model_name": "test", "run_legacy_bootstrap": False})
        if boot.get("verdict") != "BOOT_OK_MCP":
            fail(f"bootstrap failed: {boot}")
        agent_id = boot["agent"]["agent_id"]

        first = call_tool("workflow_next", {
            "agent_id": agent_id,
            "request": "delete smoke workflow file",
            "intent": "delete",
            "resource": "tmp-delete-resource-smoke/delete-me.txt",
        })
        if (first.get("must_call") or {}).get("tool") != "before_task":
            fail(f"workflow_next should require before_task first: {first}")
        before = call_tool("before_task", {"agent_id": agent_id, "request": "delete smoke workflow file", "intent": "delete", "resource": "tmp-delete-resource-smoke/delete-me.txt"})
        ctx = task_context_args(before)
        second = call_tool("workflow_next", {
            "agent_id": agent_id,
            "request": "delete smoke workflow file",
            "intent": "delete",
            "resource": "tmp-delete-resource-smoke/delete-me.txt",
            "last_verdict": before["verdict"],
            **ctx,
        })
        if (second.get("must_call") or {}).get("tool") != "scribe_query":
            fail(f"workflow_next should require scribe_query before delete claim: {second}")
        scribe = call_tool("scribe_query", {"agent_id": agent_id, **ctx, "query": "delete smoke workflow file", "limit": 5})
        third = call_tool("workflow_next", {
            "agent_id": agent_id,
            "request": "delete smoke workflow file",
            "intent": "delete",
            "resource": "tmp-delete-resource-smoke/delete-me.txt",
            "last_verdict": scribe["verdict"],
            **ctx,
        })
        if (third.get("must_call") or {}).get("tool") != "graphify_query":
            fail(f"workflow_next should require graphify_query before delete claim: {third}")
        graphify = call_tool("graphify_query", {"agent_id": agent_id, **ctx, "query": "delete smoke workflow file", "resource": "tmp-delete-resource-smoke/delete-me.txt"})
        fourth = call_tool("workflow_next", {
            "agent_id": agent_id,
            "request": "delete smoke workflow file",
            "intent": "delete",
            "resource": "tmp-delete-resource-smoke/delete-me.txt",
            "last_verdict": graphify["verdict"],
            **ctx,
        })
        if (fourth.get("must_call") or {}).get("tool") != "claim_resource":
            fail(f"workflow_next should route delete to claim after context: {fourth}")

        claim = call_tool("claim_resource", {"agent_id": agent_id, "resource": "tmp-delete-resource-smoke/delete-me.txt", "mode": "patch_queue", "ttl_seconds": 600, "action_lease_id": acquire_lease(agent_id, "claim_resource", ctx), **ctx})
        if claim.get("verdict") != "CLAIM_GRANTED":
            fail(f"claim failed: {claim}")

        h = call_tool("file_hash", {"resource": "tmp-delete-resource-smoke/delete-me.txt"})
        if h.get("verdict") != "FILE_HASH" or not h.get("exists"):
            fail(f"hash failed: {h}")

        refused = call_tool("delete_resource", {"agent_id": agent_id, "resource": "tmp-delete-resource-smoke/delete-me.txt", "base_hash": h["hash"], "action_lease_id": acquire_lease(agent_id, "delete_resource", ctx), **ctx})
        if refused.get("verdict") != "DELETE_CONFIRMATION_REQUIRED" or not target.exists():
            fail(f"delete without permission should be refused: {refused}")

        confirm = refused["required_confirmation"]
        deleted = call_tool("delete_resource", {"agent_id": agent_id, "resource": "tmp-delete-resource-smoke/delete-me.txt", "base_hash": h["hash"], "confirm_phrase": confirm, "reason": "smoke confirmed deletion", "action_lease_id": acquire_lease(agent_id, "delete_resource", ctx), **ctx})
        if deleted.get("verdict") != "RESOURCE_DELETED" or target.exists():
            fail(f"confirmed deletion failed: {deleted}")

        call_tool("release_claim", {"agent_id": agent_id, "claim_id": claim["claim_id"], "summary": "delete smoke cleanup"})
        next_record = call_tool("workflow_next", {"agent_id": agent_id, "intent": "finish", "resource": "tmp-delete-resource-smoke/delete-me.txt", "last_verdict": "CLAIM_RELEASED"})
        if (next_record.get("must_call") or {}).get("tool") != "scribe_record":
            fail(f"workflow_next should require scribe_record before finish after delete: {next_record}")
        record = call_tool("scribe_record", {"agent_id": agent_id, "request": "delete smoke workflow file", "summary": "delete resource smoke ok", "touched_resources": ["tmp-delete-resource-smoke/delete-me.txt"], "verdict": "CLAIM_RELEASED", "tags": ["smoke", "delete"], "memory_policy": "local_only"})
        if record.get("verdict") != "SCRIBE_RECORD_STAGED_ONLY":
            fail(f"scribe_record failed after delete: {record}")
        finished = call_tool("finish_task", {"agent_id": agent_id, "summary": "delete resource smoke ok", "canonical_memory_skip_reason": "This smoke only verifies delete workflow behavior and transient cleanup; it intentionally leaves no durable project memory because canonical memory coverage is exercised elsewhere.", "action_lease_id": acquire_lease(agent_id, "finish_task", ctx, resource=""), **ctx})
        if finished.get("verdict") not in {"TASK_FINISHED_OK", "CANONICAL_MEMORY_SKIPPED_WITH_REASON"}:
            fail(f"finish failed: {finished}")
        print("DELETE_RESOURCE_SMOKE_OK")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
