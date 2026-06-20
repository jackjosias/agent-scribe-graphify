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

        if "delete_resource" not in str(call_tool("workflow_next", {"agent_id": agent_id, "intent": "delete", "resource": "tmp-delete-resource-smoke/delete-me.txt"})):
            fail("workflow_next did not route delete intent")

        claim = call_tool("claim_resource", {"agent_id": agent_id, "resource": "tmp-delete-resource-smoke/delete-me.txt", "mode": "patch_queue", "ttl_seconds": 600})
        if claim.get("verdict") != "CLAIM_GRANTED":
            fail(f"claim failed: {claim}")

        h = call_tool("file_hash", {"resource": "tmp-delete-resource-smoke/delete-me.txt"})
        if h.get("verdict") != "FILE_HASH" or not h.get("exists"):
            fail(f"hash failed: {h}")

        refused = call_tool("delete_resource", {"agent_id": agent_id, "resource": "tmp-delete-resource-smoke/delete-me.txt", "base_hash": h["hash"]})
        if refused.get("verdict") != "DELETE_CONFIRMATION_REQUIRED" or not target.exists():
            fail(f"delete without permission should be refused: {refused}")

        confirm = refused["required_confirmation"]
        deleted = call_tool("delete_resource", {"agent_id": agent_id, "resource": "tmp-delete-resource-smoke/delete-me.txt", "base_hash": h["hash"], "confirm_phrase": confirm, "reason": "smoke confirmed deletion"})
        if deleted.get("verdict") != "RESOURCE_DELETED" or target.exists():
            fail(f"confirmed deletion failed: {deleted}")

        call_tool("release_claim", {"agent_id": agent_id, "claim_id": claim["claim_id"], "summary": "delete smoke cleanup"})
        finished = call_tool("finish_task", {"agent_id": agent_id, "summary": "delete resource smoke ok"})
        if finished.get("verdict") != "TASK_FINISHED_OK":
            fail(f"finish failed: {finished}")
        print("DELETE_RESOURCE_SMOKE_OK")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
