#!/usr/bin/env python3
"""CLI smoke for V2.12 host discipline guard.

This smoke uses the real server entrypoint with --call and an isolated temporary
workspace selected via AGENT_SCRIBE_GRAPHIFY_ROOT. It must not touch the real
project runtime database.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ENTRY = REPO_ROOT / ".agent" / "mcp" / "server_entry.py"
RESOURCE = "tracked.txt"
AGENT_ID = "host-discipline-smoke-agent"


class SmokeFailure(RuntimeError):
    pass


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), env=env, text=True, capture_output=True, timeout=30)


def git(root: Path, *args: str) -> None:
    proc = run(["git", *args], cwd=root)
    if proc.returncode != 0:
        raise SmokeFailure(f"git {' '.join(args)} failed: {proc.stderr or proc.stdout}")


def unwrap_tool_output(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    if "content" not in data:
        return data
    return json.loads(data["content"][0]["text"])


def call(root: Path, tool: str, **args: Any) -> dict[str, Any]:
    env = os.environ.copy()
    env["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(root)
    proc = run([sys.executable, str(SERVER_ENTRY), "--call", tool, "--args", json.dumps(args)], cwd=REPO_ROOT, env=env)
    if proc.returncode != 0:
        raise SmokeFailure(f"{tool} failed rc={proc.returncode}: {proc.stderr or proc.stdout}")
    payload = unwrap_tool_output(proc.stdout)
    if not payload.get("ok", False):
        raise SmokeFailure(f"{tool} returned error payload: {payload}")
    return payload


def expect(payload: dict[str, Any], key: str, expected: Any) -> None:
    actual = payload.get(key)
    if actual != expected:
        raise SmokeFailure(f"expected {key}={expected!r}, got {actual!r}: {payload}")


def expect_forbidden(payload: dict[str, Any], item: str = "direct_file_edit") -> None:
    forbidden = payload.get("forbidden", [])
    if item not in forbidden:
        raise SmokeFailure(f"expected forbidden to contain {item!r}: {payload}")


def setup_workspace() -> Path:
    root = Path(tempfile.mkdtemp(prefix="host-discipline-smoke-"))
    (root / ".agent" / "state").mkdir(parents=True, exist_ok=True)
    (root / RESOURCE).write_text("line1\n", encoding="utf-8")
    git(root, "init")
    git(root, "config", "user.email", "smoke@example.invalid")
    git(root, "config", "user.name", "Host Discipline Smoke")
    git(root, "add", RESOURCE)
    git(root, "commit", "-m", "initial")
    return root


def main() -> int:
    root = setup_workspace()
    try:
        print(f"Host discipline smoke root: {root}")

        payload = call(root, "register_agent", host_tool="smoke", agent_id=AGENT_ID)
        expect(payload, "verdict", "AGENT_REGISTERED")

        payload = call(root, "pre_action_guard", agent_id=AGENT_ID, request="fix tracked file", intent="write", resource=RESOURCE, planned_action="propose_patch")
        expect(payload, "verdict", "NEXT_ACTION_REQUIRED")
        expect(payload, "state", "BEFORE_TASK_REQUIRED")
        expect(payload["must_call"], "tool", "before_task")
        expect_forbidden(payload)

        before = call(root, "before_task", agent_id=AGENT_ID, request="fix production code path", intent="fix", resource=RESOURCE)
        expect(before, "verdict", "BEFORE_TASK_OK")
        ctx = {"task_id": before["task_id"], "context_token": before["context_token"]}

        payload = call(root, "pre_action_guard", agent_id=AGENT_ID, request="fix production code path", intent="write", resource=RESOURCE, planned_action="claim_resource", **ctx)
        expect(payload, "verdict", "NEXT_ACTION_REQUIRED")
        expect(payload, "state", "SCRIBE_CONTEXT_REQUIRED")
        expect(payload["must_call"], "tool", "scribe_query")

        payload = call(root, "scribe_query", agent_id=AGENT_ID, query="host discipline smoke", limit=3, **ctx)
        if payload.get("verdict") not in {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}:
            raise SmokeFailure(f"unexpected scribe verdict: {payload}")

        payload = call(root, "pre_action_guard", agent_id=AGENT_ID, request="fix production code path", intent="write", resource=RESOURCE, planned_action="claim_resource", **ctx)
        expect(payload, "verdict", "NEXT_ACTION_REQUIRED")
        expect(payload, "state", "GRAPHIFY_CONTEXT_REQUIRED")
        expect(payload["must_call"], "tool", "graphify_query")

        payload = call(root, "graphify_query", agent_id=AGENT_ID, query="host discipline smoke", resource=RESOURCE, **ctx)
        if payload.get("verdict") not in {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}:
            raise SmokeFailure(f"unexpected graphify verdict: {payload}")

        payload = call(root, "pre_action_guard", agent_id=AGENT_ID, intent="write", resource=RESOURCE, planned_action="claim_resource", **ctx)
        expect(payload, "verdict", "PRE_ACTION_GUARD_OK")
        expect(payload, "state", "ACTION_LEASE_ISSUED")
        claim_lease = payload["action_lease"]["lease_id"]

        payload = call(root, "claim_resource", agent_id=AGENT_ID, resource=RESOURCE, mode="patch_queue", ttl_seconds=600, **ctx)
        expect(payload, "verdict", "ACTION_LEASE_REQUIRED")
        expect_forbidden(payload)

        payload = call(root, "claim_resource", agent_id=AGENT_ID, resource=RESOURCE, mode="patch_queue", ttl_seconds=600, action_lease_id=claim_lease, **ctx)
        expect(payload, "verdict", "CLAIM_GRANTED")

        base = call(root, "file_hash", resource=RESOURCE)["hash"]
        payload = call(root, "pre_action_guard", agent_id=AGENT_ID, intent="write", resource=RESOURCE, planned_action="propose_patch", **ctx)
        expect(payload, "verdict", "PRE_ACTION_GUARD_OK")
        patch_lease = payload["action_lease"]["lease_id"]

        payload = call(root, "propose_patch", agent_id=AGENT_ID, target=RESOURCE, base_hash=base, diff_text="@@ -1,1 +1,1 @@\n-line1\n+line2\n", **ctx)
        expect(payload, "verdict", "ACTION_LEASE_REQUIRED")

        payload = call(root, "propose_patch", agent_id=AGENT_ID, target=RESOURCE, base_hash=base, diff_text="@@ -1,1 +1,1 @@\n-line1\n+line2\n", action_lease_id=patch_lease, **ctx)
        expect(payload, "status", "PATCH_PROPOSED")

        payload = call(root, "propose_patch", agent_id=AGENT_ID, target=RESOURCE, base_hash=base, diff_text="@@ -1,1 +1,1 @@\n-line1\n+line3\n", action_lease_id=patch_lease, **ctx)
        expect(payload, "verdict", "ACTION_LEASE_CONSUMED")

        (root / RESOURCE).write_text("direct bypass\n", encoding="utf-8")
        payload = call(root, "workspace_audit", agent_id=AGENT_ID, resource=RESOURCE, **{"task_id": ctx["task_id"]})
        expect(payload, "verdict", "DIRECT_WRITE_BYPASS_DETECTED")
        expect(payload, "state", "DIRECT_WRITE_BYPASS_DETECTED")
        if RESOURCE not in payload.get("modified_files", []):
            raise SmokeFailure(f"audit did not include {RESOURCE}: {payload}")

        print("HOST_DISCIPLINE_SMOKE_OK")
        return 0
    except Exception as exc:
        print(f"HOST_DISCIPLINE_SMOKE_FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
