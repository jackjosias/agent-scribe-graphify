#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

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


def call_tool(name: str, args: dict[str, Any], entry: Path = ENTRY, cwd: Path | str = ROOT) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(entry), "--call", name, "--args", json.dumps(args)],
        cwd=str(cwd),
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


def smoke_nominal_workflow() -> None:
    clean_runtime()
    work = ROOT / "tmp-smoke-workflow"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    (work / "file.txt").write_text("line1\n", encoding="utf-8")

    boot = call_tool("bootstrap", {"host_tool": "mcp-smoke", "model_name": "test", "run_legacy_bootstrap": False})
    if boot.get("verdict") != "BOOT_OK_MCP":
        fail(f"bootstrap failed: {boot}")
    agent_id = boot["agent"]["agent_id"]

    claim = call_tool("claim_resource", {"agent_id": agent_id, "resource": "tmp-smoke-workflow/file.txt", "mode": "patch_queue", "ttl_seconds": 600})
    if claim.get("verdict") != "CLAIM_GRANTED":
        fail(f"claim failed: {claim}")
    claim_id = claim["claim_id"]

    file_hash = call_tool("file_hash", {"resource": "tmp-smoke-workflow/file.txt"})
    if file_hash.get("verdict") != "FILE_HASH" or not file_hash.get("exists"):
        fail(f"file_hash failed: {file_hash}")

    patch = call_tool("propose_patch", {
        "agent_id": agent_id,
        "target": "tmp-smoke-workflow/file.txt",
        "base_hash": file_hash["hash"],
        "diff_text": "@@ -1,1 +1,1 @@\n-line1\n+line2\n",
    })
    if patch.get("status") != "PATCH_PROPOSED":
        fail(f"patch failed: {patch}")
    patch_id = patch["patch_id"]

    listed = call_tool("list_patches", {"target": "tmp-smoke-workflow/file.txt", "status": "proposed"})
    if listed.get("status") != "PATCHES_LISTED" or listed.get("count") != 1:
        fail(f"list failed: {listed}")

    finish_pending = call_tool("finish_task", {"agent_id": agent_id, "summary": "should be refused"})
    if finish_pending.get("verdict") != "FINISH_REFUSED_PENDING_PATCHES":
        fail(f"finish should be refused: {finish_pending}")

    rejected = call_tool("reject_patch", {"agent_id": agent_id, "patch_id": patch_id, "reason": "smoke cleanup"})
    if rejected.get("verdict") != "PATCH_REJECTED":
        fail(f"reject failed: {rejected}")

    released = call_tool("release_claim", {"agent_id": agent_id, "claim_id": claim_id, "summary": "smoke cleanup"})
    if released.get("verdict") != "CLAIM_RELEASED":
        fail(f"release failed: {released}")

    finished = call_tool("finish_task", {"agent_id": agent_id, "summary": "smoke finished"})
    if finished.get("verdict") != "TASK_FINISHED_OK":
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
        expect_error("propose_patch", {
            "agent_id": "bad-agent",
            "target": "tmp-smoke-symlink/outside-dir/new-file.txt",
            "base_hash": "__new_file__",
            "diff_text": "@@ -0,0 +1 @@\n+bad\n",
        }, "parent escapes project root")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def smoke_unregistered_patch() -> None:
    clean_runtime()
    work = ROOT / "tmp-smoke-auth"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    (work / "file.txt").write_text("line1\n", encoding="utf-8")
    file_hash = call_tool("file_hash", {"resource": "tmp-smoke-auth/file.txt"})
    expect_error("propose_patch", {
        "agent_id": "unregistered-agent",
        "target": "tmp-smoke-auth/file.txt",
        "base_hash": file_hash["hash"],
        "diff_text": "@@ -1,1 +1,1 @@\n-line1\n+line2\n",
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


def main() -> int:
    if not ENTRY.is_file():
        fail(f"missing entrypoint: {ENTRY}")
    smoke_nominal_workflow()
    smoke_bad_paths()
    smoke_unregistered_patch()
    smoke_portable_copy()
    print("MCP_SMOKE_ALL_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
