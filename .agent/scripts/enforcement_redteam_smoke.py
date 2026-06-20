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


def error_text(result: dict[str, Any]) -> str:
    return " ".join(str(result.get(key, "")) for key in ("code", "error", "status", "verdict", "reason"))


def assert_refused(result: dict[str, Any], expected: str, label: str) -> None:
    if result.get("ok") is not False or expected not in error_text(result):
        fail(f"{label} expected refusal containing {expected!r}, got {result}")


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


def claim(agent_id: str, target: str) -> str:
    result = call_tool("claim_resource", {"agent_id": agent_id, "resource": target, "mode": "patch_queue", "ttl_seconds": 600})
    if result.get("verdict") != "CLAIM_GRANTED":
        fail(f"claim_resource failed: {result}")
    return result["claim_id"]


def file_hash(target: str) -> str:
    result = call_tool("file_hash", {"resource": target})
    if result.get("verdict") != "FILE_HASH":
        fail(f"file_hash failed: {result}")
    return result["hash"]


def propose(agent_id: str, target: str, base_hash: str, replacement: str = "redteam-updated\n") -> str:
    result = call_tool("propose_patch", {
        "agent_id": agent_id,
        "target": target,
        "base_hash": base_hash,
        "diff_text": f"@@ -1,1 +1,1 @@\n-redteam-original\n+{replacement.rstrip()}\n",
    })
    if result.get("status") != "PATCH_PROPOSED":
        fail(f"propose_patch failed: {result}")
    return result["patch_id"]


def write_target(name: str, text: str = "redteam-original\n") -> str:
    prepare_redteam()
    target = REDTEAM_DIR / name
    target.write_text(text, encoding="utf-8")
    return rel(target)


def test_propose_without_claim() -> None:
    clean_runtime()
    clean_redteam()
    target = write_target("without-claim.txt")
    base_hash = file_hash(target)
    result = call_tool("propose_patch", {
        "agent_id": "unregistered-redteam-agent",
        "target": target,
        "base_hash": base_hash,
        "diff_text": "@@ -1,1 +1,1 @@\n-redteam-original\n+redteam-updated\n",
    })
    assert_refused(result, "claim required", "propose_patch without claim")


def test_apply_wrong_agent() -> None:
    clean_runtime()
    clean_redteam()
    target = write_target("wrong-agent.txt")
    agent_a = bootstrap("redteam-owner")
    agent_b = register("redteam-intruder")
    claim(agent_a, target)
    patch_id = propose(agent_a, target, file_hash(target))
    result = call_tool("apply_patch", {"agent_id": agent_b, "patch_id": patch_id})
    assert_refused(result, "only patch owner can apply it", "apply_patch wrong agent")


def test_apply_without_claim() -> None:
    clean_runtime()
    clean_redteam()
    target = write_target("released-claim.txt")
    agent_a = bootstrap("redteam-released")
    claim_id = claim(agent_a, target)
    patch_id = propose(agent_a, target, file_hash(target))
    released = call_tool("release_claim", {"agent_id": agent_a, "claim_id": claim_id, "summary": "redteam release before apply"})
    if released.get("verdict") != "CLAIM_RELEASED":
        fail(f"release_claim failed: {released}")
    result = call_tool("apply_patch", {"agent_id": agent_a, "patch_id": patch_id})
    assert_refused(result, "claim required", "apply_patch without active claim")


def test_delete_confirmation_required() -> None:
    clean_runtime()
    clean_redteam()
    target = write_target("delete-confirmation.txt")
    agent = bootstrap("redteam-delete-confirmation")
    claim(agent, target)
    base_hash = file_hash(target)
    result = call_tool("delete_resource", {"agent_id": agent, "resource": target, "base_hash": base_hash, "confirm_phrase": "DELETE wrong-file"})
    if result.get("verdict") != "DELETE_CONFIRMATION_REQUIRED":
        fail(f"delete_resource should require exact confirmation: {result}")
    if not (ROOT / target).exists():
        fail("delete_resource removed file without exact confirmation")


def test_delete_with_pending_patch() -> None:
    clean_runtime()
    clean_redteam()
    target = write_target("delete-pending-patch.txt")
    agent = bootstrap("redteam-delete-pending")
    claim(agent, target)
    base_hash = file_hash(target)
    propose(agent, target, base_hash)
    result = call_tool("delete_resource", {
        "agent_id": agent,
        "resource": target,
        "base_hash": base_hash,
        "confirm_phrase": f"DELETE {target}",
        "reason": "redteam pending patch delete attempt",
    })
    assert_refused(result, "pending proposed/conflict patches", "delete_resource with pending patch")
    if not (ROOT / target).exists():
        fail("delete_resource removed file with pending proposed/conflict patch")


def test_context_bypass() -> str:
    clean_runtime()
    clean_redteam()
    target = write_target("context-bypass.txt")
    agent = bootstrap("redteam-context-bypass")
    claim(agent, target)
    patch_id = propose(agent, target, file_hash(target), replacement="context-bypass-applied\n")
    result = call_tool("apply_patch", {"agent_id": agent, "patch_id": patch_id})
    if result.get("verdict") == "PATCH_APPLIED":
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
        test_propose_without_claim()
        test_apply_wrong_agent()
        test_apply_without_claim()
        test_delete_confirmation_required()
        test_delete_with_pending_patch()
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
