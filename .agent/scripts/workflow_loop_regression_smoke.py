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


def fail(message: str) -> None:
    raise SystemExit(f"WORKFLOW_LOOP_REGRESSION_SMOKE_FAIL: {message}")


def call_tool(entry: Path, cwd: Path, name: str, args: dict[str, Any]) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(entry), "--call", name, "--args", json.dumps(args)],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=30,
    )
    raw_text = proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout)
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        fail(f"{name} returned non-json output rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}")
        raise exc
    if "content" in raw:
        return json.loads(raw["content"][0]["text"])
    return raw


def assert_forbids_direct(payload: dict[str, Any], label: str) -> None:
    if "direct_file_edit" not in payload.get("forbidden", []):
        fail(f"{label} must forbid direct_file_edit: {payload}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "project"
        shutil.copytree(ROOT / ".agent" / "mcp", project / ".agent" / "mcp")
        (project / "README.md").write_text("test project\n", encoding="utf-8")
        entry = project / ".agent" / "mcp" / "server_entry.py"

        unknown = call_tool(entry, project, "before_task", {
            "agent_id": "host-gemini",
            "request": "edit README",
            "intent": "write",
            "resource": "README.md",
        })
        if unknown.get("verdict") != "AGENT_UNKNOWN_OR_UNREGISTERED":
            fail(f"unknown before_task expected AGENT_UNKNOWN_OR_UNREGISTERED: {unknown}")
        assert_forbids_direct(unknown, "unknown before_task")
        tasks = call_tool(entry, project, "list_tasks", {"agent_id": "host-gemini"})
        if tasks.get("count") != 0:
            fail(f"unknown before_task created task_context: {tasks}")

        registered = call_tool(entry, project, "register_agent", {
            "agent_id": "host-gemini",
            "host_tool": "workflow-loop-smoke",
            "model_name": "test",
        })
        if registered.get("verdict") != "AGENT_REGISTERED":
            fail(f"register_agent failed: {registered}")

        before = call_tool(entry, project, "before_task", {
            "agent_id": "host-gemini",
            "request": "claim resource README for write",
            "intent": "write",
            "resource": "README.md",
        })
        if before.get("verdict") != "BEFORE_TASK_OK":
            fail(f"before_task after register failed: {before}")
        ctx = {"task_id": before["task_id"], "context_token": before["context_token"]}

        scribe = call_tool(entry, project, "scribe_query", {"agent_id": "host-gemini", **ctx, "query": "edit README", "limit": 1})
        if scribe.get("verdict") not in {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}:
            fail(f"scribe_query failed: {scribe}")
        graphify = call_tool(entry, project, "graphify_query", {"agent_id": "host-gemini", **ctx, "query": "impact", "resource": "README.md"})
        if graphify.get("verdict") not in {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}:
            fail(f"graphify_query failed: {graphify}")
        lease = call_tool(entry, project, "pre_action_guard", {"agent_id": "host-gemini", "task_id": ctx["task_id"], "context_token": ctx["context_token"], "resource": "README.md", "planned_action": "claim_resource"})
        if lease.get("verdict") != "PRE_ACTION_GUARD_OK":
            fail(f"pre_action_guard failed: {lease}")
        lease_id = lease["action_lease"]["lease_id"]
        claim = call_tool(entry, project, "claim_resource", {"agent_id": "host-gemini", "resource": "README.md", "mode": "write", "ttl_seconds": 600, "action_lease_id": lease_id, **ctx})
        if claim.get("verdict") != "CLAIM_GRANTED":
            fail(f"claim_resource with ready context failed: {claim}")
        next_after_claim = call_tool(entry, project, "workflow_next", {"agent_id": "host-gemini", "request": "edit README", "intent": "write", "resource": "README.md", "last_verdict": "CLAIM_GRANTED", **ctx})
        if (next_after_claim.get("must_call") or {}).get("tool") == "before_task":
            fail(f"workflow_next returned before_task after active context: {next_after_claim}")

        duplicate = call_tool(entry, project, "before_task", {
            "agent_id": "host-gemini",
            "request": "convert README and write patch",
            "intent": "write",
            "resource": "README.md",
        })
        if duplicate.get("verdict") != "ACTIVE_TASK_EXISTS":
            fail(f"duplicate before_task expected ACTIVE_TASK_EXISTS: {duplicate}")
        if (duplicate.get("must_call") or {}).get("tool") != "resume_task_context":
            fail(f"duplicate before_task must call resume_task_context: {duplicate}")
        assert_forbids_direct(duplicate, "duplicate before_task")
        resumed = call_tool(entry, project, "resume_task_context", duplicate["must_call"]["args"])
        if resumed.get("verdict") != "TASK_CONTEXT_RESUMED" or resumed.get("context_token") == before.get("context_token"):
            fail(f"resume_task_context did not rotate token: {resumed}")

        loop_args = {"agent_id": "host-gemini", "request": "edit README", "intent": "write", "resource": "README.md", "last_verdict": "CLAIM_CONTEXT_NOT_READY"}
        call_tool(entry, project, "workflow_next", loop_args)
        call_tool(entry, project, "workflow_next", loop_args)
        stopped = call_tool(entry, project, "workflow_next", loop_args)
        if stopped.get("verdict") != "STOP_WORKFLOW_LOOP_DIRECT_WRITE_FORBIDDEN":
            fail(f"loop breaker did not hard-stop: {stopped}")
        assert_forbids_direct(stopped, "loop breaker")

    print("workflow_loop_regression_smoke OK")
    print("before_task unknown agent creates no task_context: OK")
    print("duplicate before_task returns ACTIVE_TASK_EXISTS + resume_task_context: OK")
    print("STOP_WORKFLOW_LOOP_DIRECT_WRITE_FORBIDDEN forbids direct_file_edit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
