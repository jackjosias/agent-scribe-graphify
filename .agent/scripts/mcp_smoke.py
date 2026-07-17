#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

MCP_DIR_FOR_LOCK = Path(__file__).resolve().parents[1] / "mcp"
if str(MCP_DIR_FOR_LOCK) not in sys.path:
    sys.path.insert(0, str(MCP_DIR_FOR_LOCK))
from runtime import graphify_readiness, installation_state
from runtime.validation_lock import (
    ValidationRuntimeBusy,
    reset_validation_runtime_database,
    validation_runtime_busy_message,
    validation_runtime_lock,
)

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / ".agent" / "mcp" / "server_entry.py"
SMOKE_STATE_ROOT = ROOT / ".agent" / "state" / "smoke"
WORKFLOW_RESOURCE = ".agent/state/smoke/workflow/file.txt"
AUTH_RESOURCE = ".agent/state/smoke/auth/file.txt"
SMOKE_WORKSPACES = (
    SMOKE_STATE_ROOT / "workflow",
    SMOKE_STATE_ROOT / "symlink-boundaries",
    SMOKE_STATE_ROOT / "auth",
)


def fail(message: str) -> None:
    raise SystemExit(f"SMOKE_FAIL: {message}")


def prepare_tenor_gate(root: Path) -> None:
    """Prepare a fixture explicitly; server startup is intentionally read-only."""
    prepared = installation_state.ensure_fresh_installation_state(root)
    if not prepared.get("ok"):
        fail(f"TENOR fixture prepare failed for {root}: {prepared}")
    finalized = installation_state.finalize_installation_state(root)
    if not finalized.get("ok"):
        fail(f"TENOR fixture finalize failed for {root}: {finalized}")
    gate = installation_state.inspect_installation_state(root)
    if not gate.get("ready"):
        fail(f"TENOR fixture gate not ready for {root}: {gate}")


def clean_runtime(root: Path = ROOT) -> None:
    reset_validation_runtime_database(root)


def remove_smoke_workspace(path: Path, retries: int = 20) -> None:
    """Remove one owned smoke path without following a symlink or hiding errors."""

    attempts = max(1, min(int(retries), 100))
    for attempt in range(attempts):
        try:
            if path.is_symlink() or (os.path.lexists(path) and not path.is_dir()):
                path.unlink()
            elif os.path.lexists(path):
                shutil.rmtree(path)
        except (OSError, shutil.Error) as exc:
            if attempt + 1 >= attempts:
                fail(f"SMOKE_CLEANUP_FAILED path={path} error={exc}")
        if not os.path.lexists(path):
            return
        time.sleep(0.05)
    fail(f"SMOKE_CLEANUP_FAILED path={path} still_exists=true")


def clean_smoke_workspaces() -> None:
    """Remove only owned workspaces and prove the post-condition."""
    for path in SMOKE_WORKSPACES:
        remove_smoke_workspace(path)


def _smoke_env() -> dict[str, str]:
    """Bind each entry to its project and explicitly authorize smoke fixtures."""
    env = {key: value for key, value in os.environ.items() if key != "AGENT_SCRIBE_GRAPHIFY_ROOT"}
    env[graphify_readiness.FIXTURE_ENV] = "1"
    return env


def call_tool(name: str, args: dict[str, Any], entry: Path = ENTRY, cwd: Path | str = ROOT) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(entry), "--call", name, "--args", json.dumps(args)],
        cwd=str(cwd),
        env=_smoke_env(),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
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


def acquire_lease(
    agent_id: str,
    action: str,
    ctx: dict[str, str] | None = None,
    resource: str = "",
    task_id: str = "",
    context_token: str = "",
) -> str:
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


def _ensure_graphify_stubs(root: Path = ROOT) -> None:
    """Create an explicitly marked fixture; never masquerade as terrain readiness."""
    result = graphify_readiness.write_smoke_fixture(root)
    if not result.get("ok"):
        fail(f"cannot create Graphify smoke fixture for {root}: {result}")
    verified = graphify_readiness.inspect_graphify_readiness(root, allow_fixture=True)
    if not verified.ok or verified.verdict != graphify_readiness.GRAPHIFY_TEST_FIXTURE_READY:
        fail(f"Graphify smoke fixture failed readiness verification: {verified.to_dict()}")


def smoke_nominal_workflow() -> None:
    clean_runtime()
    work = SMOKE_STATE_ROOT / "workflow"
    remove_smoke_workspace(work)
    work.mkdir(parents=True)
    target = work / "file.txt"
    target.write_text("line1\n", encoding="utf-8")

    no_agent = call_tool("workflow_next", {
        "request": "modify smoke workflow file",
        "intent": "write",
        "resource": WORKFLOW_RESOURCE,
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
        "resource": WORKFLOW_RESOURCE,
    }, "before_task")

    before = call_tool("before_task", {"agent_id": agent_id, "request": "modify smoke workflow file", "intent": "write", "resource": WORKFLOW_RESOURCE})
    if before.get("verdict") != "BEFORE_TASK_OK":
        fail(f"before_task failed: {before}")
    ctx = task_context_args(before)

    expect_next_tool({
        "agent_id": agent_id,
        "request": "modify smoke workflow file",
        "intent": "write",
        "resource": WORKFLOW_RESOURCE,
        "last_verdict": "BEFORE_TASK_OK",
        **ctx,
    }, "scribe_query")

    scribe = call_tool("scribe_query", {"agent_id": agent_id, **ctx, "query": f"modify {WORKFLOW_RESOURCE}", "limit": 5})
    if scribe.get("verdict") not in {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}:
        fail(f"scribe_query failed: {scribe}")

    expect_next_tool({
        "agent_id": agent_id,
        "request": "modify smoke workflow file",
        "intent": "write",
        "resource": WORKFLOW_RESOURCE,
        "last_verdict": scribe["verdict"],
        **ctx,
    }, "graphify_query")

    graphify = call_tool("graphify_query", {"agent_id": agent_id, **ctx, "query": "modify smoke workflow file", "resource": WORKFLOW_RESOURCE})
    if graphify.get("verdict") not in {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}:
        fail(f"graphify_query failed: {graphify}")

    expect_next_tool({
        "agent_id": agent_id,
        "intent": "write",
        "resource": WORKFLOW_RESOURCE,
        "last_verdict": graphify["verdict"],
        **ctx,
    }, "resource_lock_claim")

    hard_lock = call_tool("resource_lock_claim", {"agent_id": agent_id, "resource": WORKFLOW_RESOURCE, "ttl_seconds": 600, **ctx})
    if hard_lock.get("verdict") != "RESOURCE_LOCK_ACQUIRED":
        fail(f"resource_lock_claim should acquire hard write lock: {hard_lock}")
    lock_id = hard_lock["lock_id"]

    claim = call_tool("claim_resource", {
        "agent_id": agent_id,
        "resource": WORKFLOW_RESOURCE,
        "mode": "write",
        "ttl_seconds": 600,
        **ctx,
        "action_lease_id": acquire_lease(agent_id, "claim_resource", ctx, resource=WORKFLOW_RESOURCE),
    })
    if claim.get("verdict") != "CLAIM_GRANTED" or claim.get("mode") != "patch_queue":
        fail(f"claim should be granted as patch_queue write-gate: {claim}")
    claim_id = claim["claim_id"]

    direct = call_tool("before_edit", {"agent_id": agent_id, "resource": WORKFLOW_RESOURCE})
    if direct.get("verdict") != "DIRECT_EDIT_REFUSED_MCP_WRITE_GATE":
        fail(f"before_edit should refuse direct host writes: {direct}")

    expect_next_tool({
        "agent_id": agent_id,
        "intent": "write",
        "resource": WORKFLOW_RESOURCE,
        "last_verdict": "CLAIM_GRANTED",
        **ctx,
    }, "file_hash")

    file_hash = call_tool("file_hash", {"resource": WORKFLOW_RESOURCE})
    if file_hash.get("verdict") != "FILE_HASH" or not file_hash.get("exists"):
        fail(f"file_hash failed: {file_hash}")

    next_patch = expect_next_tool({
        "agent_id": agent_id,
        "intent": "write",
        "resource": WORKFLOW_RESOURCE,
        "base_hash": file_hash["hash"],
        "last_verdict": "FILE_HASH",
        **ctx,
    }, "propose_patch")
    if "diff_text" not in next_patch.get("missing_inputs", []):
        fail(f"workflow_next should require diff_text before propose_patch: {next_patch}")

    patch = call_tool("propose_patch", {
        "agent_id": agent_id,
        "target": WORKFLOW_RESOURCE,
        "base_hash": file_hash["hash"],
        "diff_text": "@@ -1,1 +1,1 @@\n-line1\n+line2\n",
        **ctx,
        "action_lease_id": acquire_lease(agent_id, "propose_patch", ctx, resource=WORKFLOW_RESOURCE),
    })
    if patch.get("status") != "PATCH_PROPOSED":
        fail(f"patch failed: {patch}")
    patch_id = patch["patch_id"]

    next_apply = expect_next_tool({
        "agent_id": agent_id,
        "intent": "write",
        "resource": WORKFLOW_RESOURCE,
        "last_verdict": "PATCH_PROPOSED",
        **ctx,
    }, "apply_patch")
    if "direct_file_edit" not in next_apply.get("forbidden", []):
        fail(f"workflow_next must forbid direct writes before apply_patch: {next_apply}")

    finish_pending = call_tool("finish_task", {
        "agent_id": agent_id,
        "summary": "should be refused",
        **ctx,
        "action_lease_id": acquire_lease(agent_id, "finish_task", ctx),
    })
    if finish_pending.get("verdict") != "FINISH_REFUSED_PENDING_PATCHES":
        fail(f"finish should be refused before apply_patch: {finish_pending}")

    applied = call_tool("apply_patch", {
        "agent_id": agent_id,
        "patch_id": patch_id,
        **ctx,
        "action_lease_id": acquire_lease(agent_id, "apply_patch", ctx),
    })
    if applied.get("verdict") != "PATCH_APPLIED":
        fail(f"apply_patch failed: {applied}")
    if target.read_text(encoding="utf-8") != "line2\n":
        fail("apply_patch did not modify the target file through MCP")

    released_lock = call_tool("resource_lock_release", {"agent_id": agent_id, "resource": WORKFLOW_RESOURCE, "lock_id": lock_id})
    if released_lock.get("verdict") != "RESOURCE_LOCK_RELEASED":
        fail(f"resource_lock_release failed: {released_lock}")

    applied_list = call_tool("list_patches", {"target": WORKFLOW_RESOURCE, "status": "applied"})
    if applied_list.get("status") != "PATCHES_LISTED" or applied_list.get("count") != 1:
        fail(f"applied patch should be listed: {applied_list}")

    expect_next_tool({"agent_id": agent_id, "intent": "finish", "last_verdict": "PATCH_APPLIED"}, "release_claim")

    released = call_tool("release_claim", {"agent_id": agent_id, "claim_id": claim_id, "summary": "smoke cleanup"})
    if released.get("verdict") != "CLAIM_RELEASED":
        fail(f"release failed: {released}")

    expect_next_tool({
        "agent_id": agent_id,
        "intent": "finish",
        "resource": WORKFLOW_RESOURCE,
        "last_verdict": "CLAIM_RELEASED",
    }, "scribe_record")

    record = call_tool("scribe_record", {
        "agent_id": agent_id,
        "request": "modify smoke workflow file",
        "summary": "smoke finished",
        "touched_resources": [WORKFLOW_RESOURCE],
        "verdict": "CLAIM_RELEASED",
        "tags": ["smoke"],
    })
    if record.get("verdict") != "SCRIBE_RECORD_STAGED_ONLY":
        fail(f"scribe_record failed: {record}")

    expect_next_tool({"agent_id": agent_id, "intent": "finish", "last_verdict": "SCRIBE_RECORD_STAGED_ONLY"}, "finish_task")

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

    remove_smoke_workspace(work)
    clean_runtime()


def smoke_bad_paths() -> None:
    expect_error("file_hash", {"resource": "../outside.txt"}, "project-relative")
    expect_error("file_hash", {"resource": "/etc/passwd"}, "escapes project root")
    expect_error("file_hash", {"resource": "C:\\Windows\\System32\\drivers\\etc\\hosts"}, "escapes project root")
    expect_error("file_hash", {"resource": "C:/Windows/System32/drivers/etc/hosts"}, "escapes project root")
    expect_error("file_hash", {"resource": "\\\\server\\share\\secret.txt"}, "escapes project root")

    work = ROOT / ".agent" / "state" / "smoke" / "symlink-boundaries"
    remove_smoke_workspace(work)
    work.mkdir(parents=True)
    resource_root = str(work.relative_to(ROOT)).replace("\\", "/")
    try:
        (work / "passwd-link").symlink_to("/etc/passwd")
        observed_target = os.readlink(work / "passwd-link")
        if observed_target != "/etc/passwd":
            print(
                "SMOKE_SYMLINK_SANDBOX_MUNGED "
                f"requested=/etc/passwd observed={observed_target}"
            )
            return
        expect_error("file_hash", {"resource": f"{resource_root}/passwd-link"}, "symlink")

        (work / "inside.txt").write_text("inside-ok\n", encoding="utf-8")
        (work / "inside-link").symlink_to("inside.txt")
        observed_inside = os.readlink(work / "inside-link")
        if observed_inside != "inside.txt":
            print(
                "SMOKE_SYMLINK_SANDBOX_MUNGED "
                f"requested=inside.txt observed={observed_inside}"
            )
            return
        inside = call_tool("file_hash", {"resource": f"{resource_root}/inside-link"})
        if inside.get("verdict") != "FILE_HASH" or not inside.get("exists"):
            if "symlink cannot be resolved" in str(inside.get("error") or ""):
                print("SMOKE_SYMLINK_SANDBOX_BLOCKED internal_symlink_resolution")
                return
            fail(f"internal symlink should be accepted: {inside}")

        (work / "outside-dir").symlink_to(tempfile.gettempdir(), target_is_directory=True)
        observed_outside = os.readlink(work / "outside-dir")
        if observed_outside != tempfile.gettempdir():
            print(
                "SMOKE_SYMLINK_SANDBOX_MUNGED "
                f"requested={tempfile.gettempdir()} observed={observed_outside}"
            )
            return
        boot = call_tool("bootstrap", {"host_tool": "bad-path-smoke", "model_name": "test", "run_legacy_bootstrap": False})
        agent_id = boot["agent"]["agent_id"]
        target = f"{resource_root}/outside-dir/new-file.txt"
        ctx = establish_context(agent_id, "bad path smoke", "write", target)
        outside_result = call_tool("propose_patch", {
            "agent_id": agent_id,
            "target": target,
            "base_hash": "__new_file__",
            "diff_text": "@@ -0,0 +1 @@\n+bad\n",
            **ctx,
            "action_lease_id": acquire_lease(agent_id, "propose_patch", ctx, resource=target),
        })
        if outside_result.get("ok") is not False or "parent" not in str(outside_result.get("error") or ""):
            observed_after_call = os.readlink(work / "outside-dir")
            if observed_after_call != tempfile.gettempdir():
                print(
                    "SMOKE_SYMLINK_SANDBOX_MUNGED "
                    f"requested={tempfile.gettempdir()} observed={observed_after_call}"
                )
                return
            fail(f"propose_patch expected parent escape refusal, got {outside_result}")
    finally:
        remove_smoke_workspace(work)


def smoke_unregistered_patch() -> None:
    clean_runtime()
    work = SMOKE_STATE_ROOT / "auth"
    remove_smoke_workspace(work)
    work.mkdir(parents=True)
    (work / "file.txt").write_text("line1\n", encoding="utf-8")
    boot = call_tool("bootstrap", {"host_tool": "auth-smoke", "model_name": "test", "run_legacy_bootstrap": False})
    agent_id = boot["agent"]["agent_id"]
    ctx = establish_context(agent_id, "auth smoke", "write", AUTH_RESOURCE)
    file_hash = call_tool("file_hash", {"resource": AUTH_RESOURCE})
    expect_error("propose_patch", {
        "agent_id": agent_id,
        "target": AUTH_RESOURCE,
        "base_hash": file_hash["hash"],
        "diff_text": "@@ -1,1 +1,1 @@\n-line1\n+line2\n",
        **ctx,
        "action_lease_id": acquire_lease(agent_id, "propose_patch", ctx, resource=AUTH_RESOURCE),
    }, "claim required")
    remove_smoke_workspace(work)
    clean_runtime()


def smoke_portable_copy() -> None:
    with tempfile.TemporaryDirectory(prefix="Agent Portable Project With Spaces ") as tmp:
        new_root = Path(tmp)
        new_agent = new_root / ".agent"

        def portable_ignore(directory: str, names: list[str]) -> set[str]:
            ignored = set(shutil.ignore_patterns("__pycache__", "*.pyc")(directory, names))
            if Path(directory).resolve() == (ROOT / ".agent").resolve():
                ignored.add("state")
            return ignored

        shutil.copytree(ROOT / ".agent", new_agent, ignore=portable_ignore)

        for generated in ("state", "runtime", "scribe-out", "graphify-out"):
            shutil.rmtree(new_agent / generated, ignore_errors=True)

        if not (new_agent / "mcp" / "runtime" / "db.py").is_file():
            fail("portable copy lost source module .agent/mcp/runtime/db.py")

        prepare_tenor_gate(new_root)
        _ensure_graphify_stubs(new_root)
        entry = new_agent / "mcp" / "server_entry.py"
        boot = call_tool(
            "bootstrap",
            {"host_tool": "portable-copy-smoke", "model_name": "test", "run_legacy_bootstrap": False},
            entry=entry,
            cwd=new_root,
        )
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
    proc = subprocess.run(
        [sys.executable, str(ENTRY), "--list-tools"],
        cwd=str(ROOT),
        env=_smoke_env(),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        fail(f"list-tools failed\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}")
    for tool in (
        "file_hash",
        "tenor_init_bridge",
        "portability_check",
        "graphify_required_check",
        "graphify_project_build",
        "tenor_task_start",
        "tenor_apply_changeset",
        "tenor_activity",
        "tenor_task_control",
    ):
        if tool not in proc.stdout:
            fail(f"missing tool from server_entry --list-tools: {tool}")


def main() -> int:
    if not ENTRY.is_file():
        fail(f"missing entrypoint: {ENTRY}")
    clean_smoke_workspaces()
    clean_runtime()
    try:
        prepare_tenor_gate(ROOT)
        _ensure_graphify_stubs()
        smoke_nominal_workflow()
        smoke_bad_paths()
        smoke_unregistered_patch()
        smoke_portable_copy()
        smoke_tool_listing()
        print("MCP_SMOKE_ALL_OK")
        return 0
    finally:
        clean_smoke_workspaces()
        clean_runtime()


if __name__ == "__main__":
    try:
        with validation_runtime_lock(ROOT, timeout_seconds=0, poll_interval=0.01):
            print("VALIDATION_RUNTIME_LOCK_ACQUIRED")
            exit_code = main()
            print("VALIDATION_RUNTIME_LOCK_RELEASED")
            raise SystemExit(exit_code)
    except ValidationRuntimeBusy as exc:
        raise SystemExit(validation_runtime_busy_message(exc.lock_path))
