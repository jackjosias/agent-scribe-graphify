#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MCP_DIR_FOR_LOCK = Path(__file__).resolve().parents[1] / "mcp"
if str(MCP_DIR_FOR_LOCK) not in sys.path:
    sys.path.insert(0, str(MCP_DIR_FOR_LOCK))
from runtime.validation_lock import ValidationRuntimeBusy, validation_runtime_busy_message, validation_runtime_lock
from runtime.state_paths import prepare_state_dirs

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / ".agent" / "mcp" / "server_entry.py"


def fail(message: str) -> None:
    raise SystemExit(f"SMOKE_FAIL: {message}")


def clean_runtime(root: Path = ROOT) -> None:
    runtime = root / ".agent" / "state" / "runtime"
    for suffix in ("", "-wal", "-shm"):
        path = runtime / f"coordination.sqlite{suffix}"
        if path.exists():
            path.unlink()


def _smoke_env() -> dict[str, str]:
    """Return env without AGENT_SCRIBE_GRAPHIFY_ROOT so subprocess DB path
    is determined by the entry file location (SOURCE_ROOT) regardless of
    parent process leakage."""
    return {k: v for k, v in os.environ.items() if k != "AGENT_SCRIBE_GRAPHIFY_ROOT"}


def call_tool(name: str, args: dict[str, Any], entry: Path = ENTRY, cwd: Path | str = ROOT) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(entry), "--call", name, "--args", json.dumps(args)],
        cwd=str(cwd),
        env=_smoke_env(),
        text=True,
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0:
        try:
            return json.loads(proc.stderr or proc.stdout)
        except json.JSONDecodeError:
            fail(f"{name} exited {proc.returncode}\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}")
    try:
        outer = json.loads(proc.stdout)
    except json.JSONDecodeError:
        fail(f"{name} returned non-json stdout: {proc.stdout!r}")
    if "content" in outer:
        return json.loads(outer["content"][0]["text"])
    return outer


def expect_error(name: str, args: dict[str, Any], expected: str) -> None:
    result = call_tool(name, args)
    if result.get("ok") is not False or expected not in result.get("error", ""):
        fail(f"{name} expected error containing {expected!r}, got {result}")


def expect_next_tool(args: dict[str, Any], expected_tool: str) -> dict[str, Any]:
    result = call_tool("workflow_next", args)
    actual = (result.get("must_call") or {}).get("tool")
    if result.get("verdict") != "NEXT_ACTION_REQUIRED" or actual != expected_tool:
        fail(f"workflow_next expected {expected_tool!r}, got {result}")
    return result


def task_context_args(before: dict[str, Any]) -> dict[str, str]:
    return {"task_id": before["task_id"], "context_token": before["context_token"]}


def acquire_lease(agent_id: str, action: str, ctx: dict[str, str] | None = None, resource: str = "", task_id: str = "", context_token: str = "") -> str:
    args: dict[str, Any] = {"agent_id": agent_id, "planned_action": action, "intent": "write"}
    if ctx:
        args["task_id"] = ctx.get("task_id", "")
        args["context_token"] = ctx.get("context_token", "")
    if task_id:
        args["task_id"] = task_id
    if context_token:
        args["context_token"] = context_token
    if resource:
        args["resource"] = resource
    result = call_tool("pre_action_guard", args)
    if result.get("verdict") == "PRE_ACTION_GUARD_OK" and "action_lease" in result:
        return result["action_lease"]["lease_id"]
    fail(f"pre_action_guard failed for {action}: {result}")


def _scoped_query(request: str, resource: str) -> str:
    return f"{request} resource:{resource}" if resource else request

def establish_context(agent_id: str, request: str, intent: str, resource: str) -> dict[str, str]:
    before = call_tool("before_task", {"agent_id": agent_id, "request": request, "intent": intent, "resource": resource})
    if before.get("verdict") != "BEFORE_TASK_OK":
        fail(f"before_task failed: {before}")
    ctx = task_context_args(before)
    scribe = call_tool("scribe_query", {"agent_id": agent_id, **ctx, "query": _scoped_query(request, resource), "limit": 5})
    if scribe.get("verdict") not in {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}:
        fail(f"scribe_query failed: {scribe}")
    graphify = call_tool("graphify_query", {"agent_id": agent_id, **ctx, "query": request, "resource": resource})
    if graphify.get("verdict") not in {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}:
        fail(f"graphify_query failed: {graphify}")
    return ctx


def _ensure_graphify_stubs() -> None:
    """Create minimal canonical graphify stubs so the Graphify Mandatory Guard
    does not block write operations during the smoke test.

    Never overwrites existing files — real graphify outputs are preserved."""
    gdir = prepare_state_dirs(ROOT)["graphify_out"]
    gdir.mkdir(parents=True, exist_ok=True)
    for fname, content in [
        ("graph.json", '{"nodes":[],"edges":[]}'),
        ("GRAPH_REPORT.md", "# Smoke stub Graph Report\n"),
        ("graph.html", "<html><body></body></html>\n"),
    ]:
        path = gdir / fname
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def smoke_nominal_workflow() -> None:
    clean_runtime()
    work = ROOT / "tmp-smoke-workflow"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    target = work / "file.txt"
    target.write_text("line1\n", encoding="utf-8")

    no_agent = call_tool("workflow_next", {
        "request": "modify smoke workflow file",
        "intent": "write",
        "resource": "tmp-smoke-workflow/file.txt",
        "host_tool": "mcp-smoke",
        "model_name": "test",
    })
    if no_agent.get("state") != "AGENT_ID_REQUIRED" or "bootstrap" not in no_agent.get("forbidden", []):
        fail(f"workflow_next must not bootstrap implicitly without agent_id: {no_agent}")

    boot = call_tool("bootstrap", {"host_tool": "mcp-smoke", "model_name": "test", "run_legacy_bootstrap": False})
    if boot.get("verdict") != "BOOT_OK_MCP":
        fail(f"bootstrap failed: {boot}")
    _ensure_graphify_stubs()
    agent_id = boot["agent"]["agent_id"]

    expect_next_tool({
        "agent_id": agent_id,
        "request": "modify smoke workflow file",
        "intent": "write",
        "resource": "tmp-smoke-workflow/file.txt",
    }, "before_task")

    before = call_tool("before_task", {"agent_id": agent_id, "request": "modify smoke workflow file", "intent": "write", "resource": "tmp-smoke-workflow/file.txt"})
    if before.get("verdict") != "BEFORE_TASK_OK":
        fail(f"before_task failed: {before}")
    ctx = task_context_args(before)

    expect_next_tool({
        "agent_id": agent_id,
        "request": "modify smoke workflow file",
        "intent": "write",
        "resource": "tmp-smoke-workflow/file.txt",
        "last_verdict": "BEFORE_TASK_OK",
        **ctx,
    }, "scribe_query")

    scribe = call_tool("scribe_query", {"agent_id": agent_id, **ctx, "query": "modify tmp-smoke-workflow/file.txt", "limit": 5})
    if scribe.get("verdict") not in {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}:
        fail(f"scribe_query failed: {scribe}")

    expect_next_tool({
        "agent_id": agent_id,
        "request": "modify smoke workflow file",
        "intent": "write",
        "resource": "tmp-smoke-workflow/file.txt",
        "last_verdict": scribe["verdict"],
        **ctx,
    }, "graphify_query")

    graphify = call_tool("graphify_query", {"agent_id": agent_id, **ctx, "query": "modify smoke workflow file", "resource": "tmp-smoke-workflow/file.txt"})
    if graphify.get("verdict") not in {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}:
        fail(f"graphify_query failed: {graphify}")

    expect_next_tool({
        "agent_id": agent_id,
        "request": "modify smoke workflow file",
        "intent": "write",
        "resource": "tmp-smoke-workflow/file.txt",
        "last_verdict": graphify["verdict"],
        **ctx,
    }, "resource_lock_claim")

    hard_lock = call_tool("resource_lock_claim", {"agent_id": agent_id, "resource": "tmp-smoke-workflow/file.txt", "ttl_seconds": 600, **ctx})
    if hard_lock.get("verdict") != "RESOURCE_LOCK_ACQUIRED":
        fail(f"resource_lock_claim should acquire hard write lock: {hard_lock}")
    lock_id = hard_lock["lock_id"]

    claim = call_tool("claim_resource", {"agent_id": agent_id, "resource": "tmp-smoke-workflow/file.txt", "mode": "write", "ttl_seconds": 600, **ctx, "action_lease_id": acquire_lease(agent_id, "claim_resource", ctx, resource="tmp-smoke-workflow/file.txt")})
    if claim.get("verdict") != "CLAIM_GRANTED" or claim.get("mode") != "patch_queue":
        fail(f"claim should be granted as patch_queue write-gate: {claim}")
    claim_id = claim["claim_id"]

    direct = call_tool("before_edit", {"agent_id": agent_id, "resource": "tmp-smoke-workflow/file.txt"})
    if direct.get("verdict") != "DIRECT_EDIT_REFUSED_MCP_WRITE_GATE":
        fail(f"before_edit should refuse direct host writes: {direct}")

    expect_next_tool({
        "agent_id": agent_id,
        "intent": "write",
        "resource": "tmp-smoke-workflow/file.txt",
        "last_verdict": "CLAIM_GRANTED",
        **ctx,
    }, "file_hash")

    file_hash = call_tool("file_hash", {"resource": "tmp-smoke-workflow/file.txt"})
    if file_hash.get("verdict") != "FILE_HASH" or not file_hash.get("exists"):
        fail(f"file_hash failed: {file_hash}")

    next_patch = expect_next_tool({
        "agent_id": agent_id,
        "intent": "write",
        "resource": "tmp-smoke-workflow/file.txt",
        "base_hash": file_hash["hash"],
        "last_verdict": "FILE_HASH",
        **ctx,
    }, "propose_patch")
    if "diff_text" not in next_patch.get("missing_inputs", []):
        fail(f"workflow_next should require diff_text before propose_patch: {next_patch}")

    patch = call_tool("propose_patch", {
        "agent_id": agent_id,
        "target": "tmp-smoke-workflow/file.txt",
        "base_hash": file_hash["hash"],
        "diff_text": "@@ -1,1 +1,1 @@\n-line1\n+line2\n",
        **ctx,
        "action_lease_id": acquire_lease(agent_id, "propose_patch", ctx, resource="tmp-smoke-workflow/file.txt"),
    })
    if patch.get("status") != "PATCH_PROPOSED":
        fail(f"patch failed: {patch}")
    patch_id = patch["patch_id"]

    next_apply = expect_next_tool({
        "agent_id": agent_id,
        "intent": "write",
        "resource": "tmp-smoke-workflow/file.txt",
        "last_verdict": "PATCH_PROPOSED",
        **ctx,
    }, "apply_patch")
    if "direct_file_edit" not in next_apply.get("forbidden", []):
        fail(f"workflow_next must forbid direct writes before apply_patch: {next_apply}")

    finish_pending = call_tool("finish_task", {"agent_id": agent_id, "summary": "should be refused", **ctx, "action_lease_id": acquire_lease(agent_id, "finish_task", ctx)})
    if finish_pending.get("verdict") != "FINISH_REFUSED_PENDING_PATCHES":
        fail(f"finish should be refused before apply_patch: {finish_pending}")

    applied = call_tool("apply_patch", {"agent_id": agent_id, "patch_id": patch_id, **ctx, "action_lease_id": acquire_lease(agent_id, "apply_patch", ctx)})
    if applied.get("verdict") != "PATCH_APPLIED":
        fail(f"apply_patch failed: {applied}")
    if target.read_text(encoding="utf-8") != "line2\n":
        fail("apply_patch did not modify the target file through MCP")

    released_lock = call_tool("resource_lock_release", {"agent_id": agent_id, "resource": "tmp-smoke-workflow/file.txt", "lock_id": lock_id})
    if released_lock.get("verdict") != "RESOURCE_LOCK_RELEASED":
        fail(f"resource_lock_release failed: {released_lock}")

    applied_list = call_tool("list_patches", {"target": "tmp-smoke-workflow/file.txt", "status": "applied"})
    if applied_list.get("status") != "PATCHES_LISTED" or applied_list.get("count") != 1:
        fail(f"applied patch should be listed: {applied_list}")

    expect_next_tool({
        "agent_id": agent_id,
        "intent": "finish",
        "last_verdict": "PATCH_APPLIED",
    }, "release_claim")

    released = call_tool("release_claim", {"agent_id": agent_id, "claim_id": claim_id, "summary": "smoke cleanup"})
    if released.get("verdict") != "CLAIM_RELEASED":
        fail(f"release failed: {released}")

    expect_next_tool({
        "agent_id": agent_id,
        "intent": "finish",
        "resource": "tmp-smoke-workflow/file.txt",
        "last_verdict": "CLAIM_RELEASED",
    }, "scribe_record")

    record = call_tool("scribe_record", {
        "agent_id": agent_id,
        "request": "modify smoke workflow file",
        "summary": "smoke finished",
        "touched_resources": ["tmp-smoke-workflow/file.txt"],
        "verdict": "CLAIM_RELEASED",
        "tags": ["smoke"],
    })
    if record.get("verdict") != "SCRIBE_RECORD_STAGED_ONLY":
        fail(f"scribe_record failed: {record}")

    expect_next_tool({
        "agent_id": agent_id,
        "intent": "finish",
        "last_verdict": "SCRIBE_RECORD_STAGED_ONLY",
    }, "finish_task")

    canonical_skip_reason = (
        "This smoke only verifies runtime MCP gates and a transient workflow mutation; "
        "it intentionally leaves no durable project memory because canonical memory coverage "
        "is exercised by the dedicated canonical memory gate tests."
    )
    finished = call_tool(
        "finish_task",
        {
            "agent_id": agent_id,
            "summary": "smoke finished",
            "canonical_memory_skip_reason": canonical_skip_reason,
            **ctx,
            "action_lease_id": acquire_lease(agent_id, "finish_task", ctx),
        },
    )
    if finished.get("verdict") == "SCRIBE_COMMIT_GATE_REQUIRED":
        resolved = call_tool("scribe_commit_gate_resolve", {"agent_id": agent_id, **ctx, "decision": "commit"})
        if resolved.get("verdict") != "SCRIBE_COMMIT_GATE_RESOLVED":
            fail(f"scribe commit gate resolve failed: {resolved}")
        finished = call_tool(
            "finish_task",
            {
                "agent_id": agent_id,
                "summary": "smoke finished",
                "canonical_memory_skip_reason": canonical_skip_reason,
                **ctx,
                "action_lease_id": acquire_lease(agent_id, "finish_task", ctx),
            },
        )
    if finished.get("verdict") not in {"TASK_FINISHED_OK", "CANONICAL_MEMORY_SKIPPED_WITH_REASON"}:
        fail(f"finish failed: {finished}")

    shutil.rmtree(work, ignore_errors=True)
    clean_runtime()


def smoke_bad_paths() -> None:
    expect_error("file_hash", {"resource": "../outside.txt"}, "project-relative")
    expect_error("file_hash", {"resource": "/etc/passwd"}, "escapes project root")
    expect_error("file_hash", {"resource": "C:\\Windows\\System32\\drivers\\etc\\hosts"}, "escapes project root")
    expect_error("file_hash", {"resource": "C:/Windows/System32/drivers/etc/hosts"}, "escapes project root")
    expect_error("file_hash", {"resource": "\\\\server\\share\\secret.txt"}, "escapes project root")

    work = ROOT / "tmp-smoke-symlink"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        (work / "passwd-link").symlink_to("/etc/passwd")
        expect_error("file_hash", {"resource": "tmp-smoke-symlink/passwd-link"}, "symlink escapes project root")

        (work / "inside.txt").write_text("inside-ok\n", encoding="utf-8")
        (work / "inside-link").symlink_to("inside.txt")
        inside = call_tool("file_hash", {"resource": "tmp-smoke-symlink/inside-link"})
        if inside.get("verdict") != "FILE_HASH" or not inside.get("exists"):
            fail(f"internal symlink should be accepted: {inside}")

        (work / "outside-dir").symlink_to(tempfile.gettempdir(), target_is_directory=True)
        boot = call_tool("bootstrap", {"host_tool": "bad-path-smoke", "model_name": "test", "run_legacy_bootstrap": False})
        agent_id = boot["agent"]["agent_id"]
        target = "tmp-smoke-symlink/outside-dir/new-file.txt"
        ctx = establish_context(agent_id, "bad path smoke", "write", target)
        expect_error("propose_patch", {
            "agent_id": agent_id,
            "target": target,
            "base_hash": "__new_file__",
            "diff_text": "@@ -0,0 +1 @@\n+bad\n",
            **ctx,
            "action_lease_id": acquire_lease(agent_id, "propose_patch", ctx, resource=target),
        }, "parent escapes project root")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def smoke_unregistered_patch() -> None:
    clean_runtime()
    work = ROOT / "tmp-smoke-auth"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    (work / "file.txt").write_text("line1\n", encoding="utf-8")
    boot = call_tool("bootstrap", {"host_tool": "auth-smoke", "model_name": "test", "run_legacy_bootstrap": False})
    agent_id = boot["agent"]["agent_id"]
    ctx = establish_context(agent_id, "auth smoke", "write", "tmp-smoke-auth/file.txt")
    file_hash = call_tool("file_hash", {"resource": "tmp-smoke-auth/file.txt"})
    expect_error("propose_patch", {
        "agent_id": agent_id,
        "target": "tmp-smoke-auth/file.txt",
        "base_hash": file_hash["hash"],
        "diff_text": "@@ -1,1 +1,1 @@\n-line1\n+line2\n",
        **ctx,
        "action_lease_id": acquire_lease(agent_id, "propose_patch", ctx, resource="tmp-smoke-auth/file.txt"),
    }, "claim required")
    shutil.rmtree(work, ignore_errors=True)
    clean_runtime()


def smoke_portable_copy() -> None:
    with tempfile.TemporaryDirectory(prefix="Agent Portable Project With Spaces ") as tmp:
        new_root = Path(tmp)
        new_agent = new_root / ".agent"
        shutil.copytree(ROOT / ".agent", new_agent, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

        for generated in ("state", "runtime", "scribe-out", "graphify-out"):
            shutil.rmtree(new_agent / generated, ignore_errors=True)

        if not (new_agent / "mcp" / "runtime" / "db.py").is_file():
            fail("portable copy lost source module .agent/mcp/runtime/db.py")

        entry = new_agent / "mcp" / "server_entry.py"
        boot = call_tool("bootstrap", {"host_tool": "portable-copy-smoke", "model_name": "test", "run_legacy_bootstrap": False}, entry=entry, cwd=tempfile.gettempdir())
        if boot.get("verdict") != "BOOT_OK_MCP":
            fail(f"portable bootstrap failed: {boot}")
        if str(new_root) not in boot["runtime"]["db"]:
            fail(f"portable db path points outside copied project: {boot['runtime']['db']}")
        if not (new_root / ".agent" / "state" / "runtime" / "coordination.sqlite").is_file():
            fail("portable sqlite was not created in copied project")
        for legacy in ("runtime", "scribe-out", "graphify-out"):
            if (new_root / ".agent" / legacy).exists():
                fail(f"legacy directory recreated in portable copy: {legacy}")


def smoke_tool_listing() -> None:
    proc = subprocess.run([sys.executable, str(ENTRY), "--list-tools"], cwd=str(ROOT), env=_smoke_env(), text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        fail(f"list-tools failed\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}")
    for tool in ("workflow_next", "scribe_query", "graphify_query", "scribe_record", "scribe_commit_gate_status", "scribe_commit_gate_resolve", "apply_patch", "delete_resource"):
        if tool not in proc.stdout:
            fail(f"missing tool from server_entry --list-tools: {tool}")


def main() -> int:
    if not ENTRY.is_file():
        fail(f"missing entrypoint: {ENTRY}")
    _ensure_graphify_stubs()
    smoke_nominal_workflow()
    smoke_bad_paths()
    smoke_unregistered_patch()
    smoke_portable_copy()
    smoke_tool_listing()
    print("MCP_SMOKE_ALL_OK")
    return 0


if __name__ == "__main__":
    try:
        with validation_runtime_lock(ROOT, timeout_seconds=0, poll_interval=0.01):
            print("VALIDATION_RUNTIME_LOCK_ACQUIRED")
            exit_code = main()
            print("VALIDATION_RUNTIME_LOCK_RELEASED")
            raise SystemExit(exit_code)
    except ValidationRuntimeBusy as exc:
        raise SystemExit(validation_runtime_busy_message(exc.lock_path))
