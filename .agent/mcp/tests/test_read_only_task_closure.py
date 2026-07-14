#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
ROOT = MCP_DIR.parents[1]
ENTRY = MCP_DIR / "server_entry.py"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import graphify_readiness, installation_state
from runtime.validation_lock import reset_validation_runtime_database


def fail(message: str) -> None:
    raise SystemExit(f"READ_TASK_CLOSURE_FAIL: {message}")


def clean_runtime() -> None:
    reset_validation_runtime_database(ROOT)


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    env = dict(os.environ)
    env[graphify_readiness.FIXTURE_ENV] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            str(ENTRY),
            "--call",
            name,
            "--args",
            json.dumps(args),
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    raw = (
        proc.stdout.strip()
        if proc.returncode == 0
        else (proc.stderr.strip() or proc.stdout.strip())
    )
    try:
        outer = json.loads(raw)
        return (
            json.loads(outer["content"][0]["text"])
            if "content" in outer
            else outer
        )
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        fail(
            f"{name} returned invalid JSON rc={proc.returncode}: "
            f"{raw!r}; {exc}"
        )


def prepare_runtime() -> None:
    prepared = installation_state.ensure_fresh_installation_state(ROOT)
    if not prepared.get("ok"):
        fail(f"installation prepare failed: {prepared}")
    finalized = installation_state.finalize_installation_state(ROOT)
    if not finalized.get("ok"):
        fail(f"installation finalize failed: {finalized}")
    fixture = graphify_readiness.write_smoke_fixture(ROOT)
    if not fixture.get("ok"):
        fail(f"Graphify fixture failed: {fixture}")


def main() -> int:
    clean_runtime()
    prepare_runtime()
    try:
        boot = call_tool(
            "bootstrap",
            {
                "host_tool": "read-close-smoke",
                "model_name": "test",
                "run_legacy_bootstrap": False,
            },
        )
        if boot.get("verdict") != "BOOT_OK_MCP":
            fail(f"bootstrap failed: {boot}")
        agent_id = boot["agent"]["agent_id"]

        before = call_tool(
            "before_task",
            {
                "agent_id": agent_id,
                "request": "inspect README and report status",
                "intent": "read_or_research",
                "resource": "README.md",
            },
        )
        if before.get("verdict") != "BEFORE_TASK_OK":
            fail(f"before_task failed: {before}")
        task_id = before["task_id"]
        token = before["context_token"]

        scribe = call_tool(
            "scribe_query",
            {
                "agent_id": agent_id,
                "task_id": task_id,
                "context_token": token,
                "query": "inspect README project status resource:README.md",
                "limit": 5,
            },
        )
        if scribe.get("verdict") not in {
            "SCRIBE_QUERY_DONE",
            "SCRIBE_UNAVAILABLE",
        }:
            fail(f"scribe_query failed: {scribe}")

        if before.get("requires_graphify"):
            graph = call_tool(
                "graphify_query",
                {
                    "agent_id": agent_id,
                    "task_id": task_id,
                    "context_token": token,
                    "query": "inspect README structural context",
                    "resource": "README.md",
                },
            )
            if graph.get("verdict") not in {
                "GRAPHIFY_QUERY_DONE",
                "GRAPHIFY_UNAVAILABLE",
            }:
                fail(f"graphify_query failed: {graph}")

        resumed = call_tool(
            "resume_task_context",
            {"agent_id": agent_id, "task_id": task_id},
        )
        if (
            resumed.get("verdict") != "TASK_CONTEXT_RESUMED"
            or resumed.get("intent") != "read"
        ):
            fail(f"resume did not expose canonical read intent: {resumed}")
        token = resumed["context_token"]

        forbidden_claim = call_tool(
            "claim_resource",
            {
                "agent_id": agent_id,
                "resource": "README.md",
                "mode": "write",
                "task_id": task_id,
                "context_token": token,
            },
        )
        if forbidden_claim.get("verdict") != "READ_ONLY_CLAIM_FORBIDDEN":
            fail(
                "read task acquired or approached a write claim: "
                f"{forbidden_claim}"
            )

        guard = call_tool(
            "pre_action_guard",
            {
                "agent_id": agent_id,
                "task_id": task_id,
                "context_token": token,
                "intent": "read_or_research",
                "planned_action": "finish_task",
                "request": "close completed read task",
            },
        )
        if (
            guard.get("verdict") != "PRE_ACTION_GUARD_OK"
            or guard.get("state") != "READ_ONLY_NO_LEASE"
        ):
            fail(f"read finish guard failed: {guard}")
        if guard.get("action_lease"):
            fail(f"read finish unexpectedly received a write lease: {guard}")

        finished = call_tool(
            "finish_task",
            {
                "agent_id": agent_id,
                "task_id": task_id,
                "context_token": token,
                "intent": "read_or_research",
                "summary": "read-only inspection completed",
            },
        )
        if finished.get("verdict") != "TASK_FINISHED_OK":
            fail(f"read finish failed: {finished}")

        next_step = call_tool(
            "workflow_next",
            {
                "agent_id": agent_id,
                "task_id": task_id,
                "context_token": token,
                "intent": "read_or_research",
                "last_verdict": "TASK_FINISHED_OK",
                "request": "inspect README and report status",
                "resource": "README.md",
            },
        )
        if next_step.get("verdict") != "READY_FOR_NEXT_TASK":
            fail(
                "workflow did not reach READY_FOR_NEXT_TASK: "
                f"{next_step}"
            )

        status = call_tool("task_status", {"task_id": task_id})
        task = status.get("task") or {}
        if task.get("status") != "finished" or task.get("intent") != "read":
            fail(f"task status not canonically finished: {status}")

        second_boot = call_tool(
            "bootstrap",
            {
                "host_tool": "write-spoof-smoke",
                "model_name": "test",
                "run_legacy_bootstrap": False,
            },
        )
        if second_boot.get("verdict") != "BOOT_OK_MCP":
            fail(f"second bootstrap failed: {second_boot}")
        write_agent_id = second_boot["agent"]["agent_id"]

        write_before = call_tool(
            "before_task",
            {
                "agent_id": write_agent_id,
                "request": "modify README",
                "intent": "write",
                "resource": "README.md",
            },
        )
        if write_before.get("verdict") != "BEFORE_TASK_OK":
            fail(f"write before_task failed: {write_before}")
        write_task = write_before["task_id"]
        write_token = write_before["context_token"]

        call_tool(
            "scribe_query",
            {
                "agent_id": write_agent_id,
                "task_id": write_task,
                "context_token": write_token,
                "query": "modify README resource:README.md",
                "limit": 5,
            },
        )
        call_tool(
            "graphify_query",
            {
                "agent_id": write_agent_id,
                "task_id": write_task,
                "context_token": write_token,
                "query": "modify README impact",
                "resource": "README.md",
            },
        )
        spoofed = call_tool(
            "finish_task",
            {
                "agent_id": write_agent_id,
                "task_id": write_task,
                "context_token": write_token,
                "intent": "read_or_research",
                "summary": "attempt to spoof a write task as read",
            },
        )
        if spoofed.get("verdict") != "ACTION_LEASE_REQUIRED":
            fail(
                "write task bypassed finish lease through read alias: "
                f"{spoofed}"
            )

        print("READ_ONLY_TASK_CLOSURE_OK")
        return 0
    finally:
        clean_runtime()


if __name__ == "__main__":
    raise SystemExit(main())
