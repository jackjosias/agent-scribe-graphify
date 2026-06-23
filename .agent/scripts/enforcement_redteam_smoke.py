#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / ".agent" / "mcp" / "server_entry.py"
REDTEAM_DIR = ROOT / ".agent" / "state" / "redteam"


def fail(message: str) -> None:
    raise SystemExit(f"ENFORCEMENT_REDTEAM_FAIL: {message}")


def clean_runtime(root: Path = ROOT) -> None:
    runtime = root / ".agent" / "state" / "runtime"
    for suffix in ("", "-wal", "-shm"):
        path = runtime / f"coordination.sqlite{suffix}"
        if path.exists():
            path.unlink()


def clean_redteam() -> None:
    shutil.rmtree(REDTEAM_DIR, ignore_errors=True)


def prepare_redteam() -> None:
    REDTEAM_DIR.mkdir(parents=True, exist_ok=True)


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(ENTRY), "--call", name, "--args", json.dumps(args)],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        timeout=30,
    )
    raw_text = proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout)
    try:
        outer = json.loads(raw_text)
    except json.JSONDecodeError:
        fail(f"{name} returned non-json output rc={proc.returncode}\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}")
    if "content" in outer:
        return json.loads(outer["content"][0]["text"])
    return outer


def error_text(result: dict[str, Any]) -> str:
    return " ".join(str(result.get(key, "")) for key in ("code", "error", "status", "verdict", "reason"))


def assert_refused(result: dict[str, Any], expected: str, label: str) -> None:
    if result.get("ok") is not False and result.get("verdict") not in {"CLAIM_CONTEXT_NOT_READY", "DELETE_CONFIRMATION_REQUIRED"}:
        fail(f"{label} expected refusal, got {result}")
    if expected not in error_text(result):
        fail(f"{label} expected refusal containing {expected!r}, got {result}")
    if result.get("forbidden") and "direct_file_edit" not in result.get("forbidden", []):
        fail(f"{label} must forbid direct_file_edit: {result}")


def bootstrap(label: str) -> str:
    boot = call_tool("bootstrap", {"host_tool": label, "model_name": "redteam", "run_legacy_bootstrap": False})
    if boot.get("verdict") != "BOOT_OK_MCP":
        fail(f"bootstrap failed for {label}: {boot}")
    return boot["agent"]["agent_id"]


def register(label: str) -> str:
    registered = call_tool("register_agent", {"host_tool": label, "model_name": "redteam"})
    if registered.get("verdict") != "AGENT_REGISTERED":
        fail(f"register_agent failed for {label}: {registered}")
    return registered["agent"]["agent_id"]


def write_target(name: str, text: str = "redteam-original\n") -> str:
    prepare_redteam()
    target = REDTEAM_DIR / name
    target.write_text(text, encoding="utf-8")
    return rel(target)


def file_hash(target: str) -> str:
    result = call_tool("file_hash", {"resource": target})
    if result.get("verdict") != "FILE_HASH":
        fail(f"file_hash failed: {result}")
    return result["hash"]


def start_context(agent_id: str, target: str, request: str = "redteam write", intent: str = "write") -> dict[str, str]:
    before = call_tool("before_task", {"agent_id": agent_id, "request": request, "intent": intent, "resource": target})
    if before.get("verdict") != "BEFORE_TASK_OK":
        fail(f"before_task failed: {before}")
    return {"task_id": before["task_id"], "context_token": before["context_token"]}


def mark_scribe(agent_id: str, ctx: dict[str, str], query: str = "redteam write") -> None:
    result = call_tool("scribe_query", {"agent_id": agent_id, **ctx, "query": query, "limit": 5})
    if result.get("verdict") not in {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}:
        fail(f"scribe_query failed: {result}")


def mark_graphify(agent_id: str, target: str, ctx: dict[str, str], query: str = "redteam write") -> None:
    result = call_tool("graphify_query", {"agent_id": agent_id, **ctx, "query": query, "resource": target})
    if result.get("verdict") not in {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}:
        fail(f"graphify_query failed: {result}")


def ready_context(agent_id: str, target: str, request: str = "redteam write", intent: str = "write") -> dict[str, str]:
    ctx = start_context(agent_id, target, request, intent=intent)
    mark_scribe(agent_id, ctx, request)
    if intent != "read":
        mark_graphify(agent_id, target, ctx, request)
    return ctx


def acquire_lease(agent_id: str, action: str, ctx: dict[str, str] | None = None, resource: str = "") -> str:
    args: dict[str, Any] = {"agent_id": agent_id, "planned_action": action, "intent": "write"}
    if ctx:
        args["task_id"] = ctx.get("task_id", "")
        args["context_token"] = ctx.get("context_token", "")
    if resource:
        args["resource"] = resource
    result = call_tool("pre_action_guard", args)
    if result.get("verdict") == "PRE_ACTION_GUARD_OK" and "action_lease" in result:
        return result["action_lease"]["lease_id"]
    return ""


def claim(agent_id: str, target: str, ctx: dict[str, str]) -> str:
    lease_id = acquire_lease(agent_id, "claim_resource", ctx, resource=target)
    result = call_tool("claim_resource", {
        "agent_id": agent_id, "resource": target, "mode": "patch_queue",
        "ttl_seconds": 600, **ctx, "action_lease_id": lease_id,
    })
    if result.get("verdict") != "CLAIM_GRANTED":
        fail(f"claim_resource failed: {result}")
    return result["claim_id"]


def propose(agent_id: str, target: str, base_hash: str, ctx: dict[str, str], replacement: str = "redteam-updated\n") -> dict[str, Any]:
    lease_id = acquire_lease(agent_id, "propose_patch", ctx, resource=target)
    return call_tool("propose_patch", {
        "agent_id": agent_id,
        "target": target,
        "base_hash": base_hash,
        "diff_text": f"@@ -1,1 +1,1 @@\n-redteam-original\n+{replacement.rstrip()}\n",
        **ctx, "action_lease_id": lease_id,
    })


def expect_patch(agent_id: str, target: str, base_hash: str, ctx: dict[str, str], replacement: str = "redteam-updated\n") -> str:
    result = propose(agent_id, target, base_hash, ctx, replacement)
    if result.get("status") != "PATCH_PROPOSED":
        fail(f"propose_patch failed: {result}")
    return result["patch_id"]


def test_positive_context_path() -> None:
    clean_runtime(); clean_redteam()
    target = write_target("positive-context.txt")
    agent = bootstrap("redteam-positive")
    ctx = ready_context(agent, target, "redteam positive context path")
    claim(agent, target, ctx)
    patch_id = expect_patch(agent, target, file_hash(target), ctx, replacement="positive-context-applied\n")
    lease_id = acquire_lease(agent, "apply_patch", ctx)
    applied = call_tool("apply_patch", {"agent_id": agent, "patch_id": patch_id, **ctx, "action_lease_id": lease_id})
    if applied.get("verdict") != "PATCH_APPLIED":
        fail(f"positive context apply failed: {applied}")


def test_claim_requires_scribe_and_graphify() -> None:
    clean_runtime(); clean_redteam()
    target = write_target("missing-context.txt")
    agent = bootstrap("redteam-missing-context")
    ctx = start_context(agent, target, "redteam missing scribe")
    result = call_tool("claim_resource", {"agent_id": agent, "resource": target, "mode": "patch_queue", "ttl_seconds": 600, **ctx})
    assert_refused(result, "scribe_query", "claim_resource without scribe_query")
    mark_scribe(agent, ctx, "redteam missing graphify")
    result = call_tool("claim_resource", {"agent_id": agent, "resource": target, "mode": "patch_queue", "ttl_seconds": 600, **ctx})
    assert_refused(result, "graphify_query", "claim_resource without graphify_query")


def test_fake_token_and_read_intent_refused_at_claim() -> None:
    clean_runtime(); clean_redteam()
    target = write_target("fake-token.txt")
    agent = bootstrap("redteam-fake-token")
    ctx = ready_context(agent, target, "redteam fake token")
    bad_ctx = {**ctx, "context_token": "fake-token"}
    result = call_tool("claim_resource", {"agent_id": agent, "resource": target, "mode": "patch_queue", "ttl_seconds": 600, **bad_ctx})
    assert_refused(result, "TASK_CONTEXT_TOKEN_MISMATCH", "claim_resource with fake token")

    clean_runtime(); clean_redteam()
    target = write_target("read-intent.txt")
    agent = bootstrap("redteam-read-intent")
    read_ctx = ready_context(agent, target, "redteam read intent", intent="read")
    result = call_tool("claim_resource", {"agent_id": agent, "resource": target, "mode": "patch_queue", "ttl_seconds": 600, **read_ctx})
    assert_refused(result, "READ_INTENT_CANNOT_WRITE", "claim_resource with read intent")


def test_propose_apply_and_delete_guards() -> None:
    clean_runtime(); clean_redteam()
    target = write_target("guards.txt")
    agent = bootstrap("redteam-guards")
    ctx = ready_context(agent, target, "redteam guards")
    result = propose(agent, target, file_hash(target), ctx)
    assert_refused(result, "claim required", "propose_patch without claim")
    claim_id = claim(agent, target, ctx)
    patch_id = expect_patch(agent, target, file_hash(target), ctx)
    intruder = bootstrap("redteam-intruder")
    intruder_ctx = ready_context(intruder, target, "redteam intruder apply")
    intruder_lease = acquire_lease(intruder, "apply_patch", intruder_ctx)
    result = call_tool("apply_patch", {"agent_id": intruder, "patch_id": patch_id, **intruder_ctx, "action_lease_id": intruder_lease})
    assert_refused(result, "only patch owner can apply it", "apply_patch wrong agent")
    released = call_tool("release_claim", {"agent_id": agent, "claim_id": claim_id, "summary": "release before apply"})
    if released.get("verdict") != "CLAIM_RELEASED":
        fail(f"release_claim failed: {released}")
    apply_lease = acquire_lease(agent, "apply_patch", ctx)
    result = call_tool("apply_patch", {"agent_id": agent, "patch_id": patch_id, **ctx, "action_lease_id": apply_lease})
    assert_refused(result, "claim required", "apply_patch without active claim")

    clean_runtime(); clean_redteam()
    target = write_target("delete-confirmation.txt")
    agent = bootstrap("redteam-delete")
    ctx = ready_context(agent, target, "redteam delete")
    claim(agent, target, ctx)
    lease_id = acquire_lease(agent, "delete_resource", ctx, resource=target)
    result = call_tool("delete_resource", {"agent_id": agent, "resource": target, "base_hash": file_hash(target), "confirm_phrase": "DELETE wrong-file", **ctx, "action_lease_id": lease_id})
    if result.get("verdict") != "DELETE_CONFIRMATION_REQUIRED" or not (ROOT / target).exists():
        fail(f"delete_resource should require exact confirmation: {result}")


def test_resource_mismatch_refused_at_claim() -> None:
    clean_runtime(); clean_redteam()
    file_a = write_target("resource-a.txt")
    file_b = write_target("resource-b.txt")
    agent = bootstrap("redteam-resource-mismatch")
    ctx = ready_context(agent, file_a, "redteam resource mismatch")
    result = call_tool("claim_resource", {"agent_id": agent, "resource": file_b, "mode": "patch_queue", "ttl_seconds": 600, **ctx})
    assert_refused(result, "TASK_CONTEXT_RESOURCE_MISMATCH", "claim_resource with mismatched context resource")


def test_context_bypass() -> str:
    clean_runtime(); clean_redteam()
    target = write_target("context-bypass.txt")
    agent = bootstrap("redteam-context-bypass")
    lease_id = acquire_lease(agent, "claim_resource", resource=target)
    claim_result = call_tool("claim_resource", {"agent_id": agent, "resource": target, "mode": "patch_queue", "ttl_seconds": 600, "action_lease_id": lease_id})
    if claim_result.get("verdict") == "CLAIM_GRANTED":
        print("MCP_CONTEXT_BYPASS_OPEN")
        return "OPEN"
    lease_id = acquire_lease(agent, "propose_patch", resource=target)
    result = call_tool("propose_patch", {
        "agent_id": agent,
        "target": target,
        "base_hash": file_hash(target),
        "diff_text": "@@ -1,1 +1,1 @@\n-redteam-original\n+context-bypass-applied\n",
        "action_lease_id": lease_id,
    })
    if result.get("status") == "PATCH_PROPOSED":
        patch_id = result["patch_id"]
        lease_apply = acquire_lease(agent, "apply_patch")
        applied = call_tool("apply_patch", {"agent_id": agent, "patch_id": patch_id, "action_lease_id": lease_apply})
        if applied.get("verdict") == "PATCH_APPLIED":
            print("MCP_CONTEXT_BYPASS_OPEN")
            return "OPEN"
    print("MCP_CONTEXT_BYPASS_CLOSED")
    return "CLOSED"


def test_direct_fs_write() -> str:
    try:
        prepare_redteam()
        direct = REDTEAM_DIR / "direct-shell.txt"
        direct.write_text("direct fs write outside MCP\n", encoding="utf-8")
        if direct.exists():
            print("DIRECT_FS_WRITE_OUTSIDE_SANDBOX_OPEN")
            return "OPEN"
        return "UNKNOWN"
    except OSError:
        return "BLOCKED"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-context", action="store_true", help="fail if direct MCP write path bypasses before_task/scribe_query/graphify_query")
    args = parser.parse_args()
    if not ENTRY.is_file():
        fail(f"missing entrypoint: {ENTRY}")
    try:
        test_positive_context_path()
        test_claim_requires_scribe_and_graphify()
        test_fake_token_and_read_intent_refused_at_claim()
        test_propose_apply_and_delete_guards()
        test_resource_mismatch_refused_at_claim()
        context_bypass = test_context_bypass()
        direct_fs = test_direct_fs_write()
        print(f"MCP_ENFORCEMENT_REDTEAM_OK context_bypass={context_bypass} direct_fs_outside_sandbox={direct_fs}")
        if args.strict_context and context_bypass == "OPEN":
            return 2
        return 0
    finally:
        clean_redteam()
        clean_runtime()


if __name__ == "__main__":
    raise SystemExit(main())
