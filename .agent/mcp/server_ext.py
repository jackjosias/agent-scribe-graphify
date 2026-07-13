#!/usr/bin/env python3
from __future__ import annotations

"""V2.16 MCP composition facade.

The established tool implementation is loaded from ``_server_ext_impl.py``.
This facade applies the canonical read-only finish policy after registration,
so a read task can close without receiving any write lease while a write task
can never spoof a read intent.
"""

import runpy
from pathlib import Path
from typing import Any, Dict

import server  # type: ignore
from runtime import db, task_context  # type: ignore

_IMPL_PATH = Path(__file__).with_name("_server_ext_impl.py")
_IMPL_NAMESPACE = runpy.run_path(
    str(_IMPL_PATH),
    run_name="agent_scribe_graphify._server_ext_impl",
)
for _name, _value in _IMPL_NAMESPACE.items():
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = _value

_BASE_PRE_ACTION_GUARD = server.TOOLS["pre_action_guard"]
_READ_ONLY_FINISH_FORBIDDEN = [
    "claim_resource",
    "resource_lock_claim",
    "propose_patch",
    "apply_patch",
    "delete_resource",
    "direct_file_edit",
]


def _context_followup(
    exc: task_context.TaskContextError,
    agent_id: str,
    task_id: str,
    context_token: str,
) -> Dict[str, Any]:
    code = getattr(exc, "code", str(exc))
    payload: dict[str, Any] = {
        "ok": False,
        "verdict": "NEXT_ACTION_REQUIRED",
        "state": code,
        "reason": code,
        "forbidden": _READ_ONLY_FINISH_FORBIDDEN,
    }
    if code in {
        "TASK_CONTEXT_TOKEN_MISMATCH",
        "TASK_CONTEXT_REQUIRED: task_id and context_token are required",
    }:
        payload["must_call"] = {
            "tool": "resume_task_context",
            "args": {"agent_id": agent_id, "task_id": task_id},
        }
    elif "scribe_query is required" in code:
        payload["must_call"] = {
            "tool": "scribe_query",
            "args": {
                "agent_id": agent_id,
                "task_id": task_id,
                "context_token": context_token,
                "query": "resume read task context",
                "limit": 5,
            },
        }
    elif "graphify_query is required" in code:
        payload["must_call"] = {
            "tool": "graphify_query",
            "args": {
                "agent_id": agent_id,
                "task_id": task_id,
                "context_token": context_token,
                "query": "resume read task structural context",
            },
        }
    return server.ok(payload)


def pre_action_guard(
    agent_id: str = "",
    request: str = "",
    intent: str = "",
    resource: str = "",
    task_id: str = "",
    context_token: str = "",
    planned_action: str = "",
) -> Dict[str, Any]:
    action = {
        "finish": "finish_task",
        "edit": "propose_patch",
        "write": "propose_patch",
        "delete": "delete_resource",
    }.get(
        (planned_action or "").strip().lower(),
        (planned_action or "").strip().lower(),
    )

    if action == "finish_task" and task_id:
        try:
            status = task_context.task_status(task_id)
        except task_context.TaskContextError as exc:
            return _context_followup(exc, agent_id, task_id, context_token)

        if status.get("agent_id") != agent_id:
            return server.ok({
                "ok": False,
                "verdict": "TASK_AGENT_MISMATCH",
                "state": "HARD_STOP",
                "reason": "The active task belongs to another agent.",
                "forbidden": ["finish_task", *_READ_ONLY_FINISH_FORBIDDEN],
            })

        if status.get("intent") == "read":
            if status.get("status") != "active":
                return server.ok({
                    "ok": False,
                    "verdict": "TASK_CONTEXT_NOT_ACTIVE",
                    "state": "HARD_STOP",
                    "reason": "Only an active read task can request a finish guard.",
                    "forbidden": ["finish_task", *_READ_ONLY_FINISH_FORBIDDEN],
                })
            try:
                db.require_agent_active(agent_id)
                task_context.require_context_ready(
                    agent_id,
                    task_id,
                    context_token,
                    resource="",
                    strict_resource=False,
                    allowed_intents={"read"},
                )
            except db.CoordinationError as exc:
                return server.ok({
                    "ok": False,
                    "verdict": str(exc),
                    "state": str(exc),
                    "forbidden": ["finish_task", *_READ_ONLY_FINISH_FORBIDDEN],
                })
            except task_context.TaskContextError as exc:
                return _context_followup(exc, agent_id, task_id, context_token)

            return server.ok({
                "ok": True,
                "verdict": "PRE_ACTION_GUARD_OK",
                "state": "READ_ONLY_NO_LEASE",
                "reason": "Read-only task is context-ready; finish_task requires no write lease.",
                "task_id": task_id,
                "canonical_intent": "read",
                "forbidden": _READ_ONLY_FINISH_FORBIDDEN,
            })

    return _BASE_PRE_ACTION_GUARD(
        agent_id=agent_id,
        request=request,
        intent=intent,
        resource=resource,
        task_id=task_id,
        context_token=context_token,
        planned_action=planned_action,
    )


server.pre_action_guard = pre_action_guard
server.TOOLS["pre_action_guard"] = pre_action_guard
handle = server.handle
list_tools = server.list_tools
main = server.main


if __name__ == "__main__":
    raise SystemExit(server.main())
