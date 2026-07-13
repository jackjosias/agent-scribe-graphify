#!/usr/bin/env python3
from __future__ import annotations

"""V2.16 MCP composition facade.

The established tool implementation is loaded from ``_server_ext_impl.py``.
This facade applies two public policy boundaries:

* canonical read-only task closure without write leases;
* a fail-closed first-write discovery path when SCRIBE has no historical
  context for a genuinely new resource.

The first-write path never treats a local discovery receipt as canonical
memory.  It only permits the normal MCP patch workflow after an honest SCRIBE
query, Graphify context and task/resource-bound discovery evidence.
"""

import json
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
_BASE_SCRIBE_QUERY = server.TOOLS["scribe_query"]
_BASE_CLAIM_RESOURCE = server.TOOLS["claim_resource"]
_BASE_PROPOSE_PATCH = server.TOOLS["propose_patch"]
_BASE_APPLY_PATCH = server.TOOLS["apply_patch"]
_BASE_DELETE_RESOURCE = server.TOOLS["delete_resource"]

_READ_ONLY_FINISH_FORBIDDEN = [
    "claim_resource",
    "resource_lock_claim",
    "propose_patch",
    "apply_patch",
    "delete_resource",
    "direct_file_edit",
]
_FIRST_WRITE_ACTIONS = {
    "claim_resource",
    "resource_lock_claim",
    "propose_patch",
    "apply_patch",
    "delete_resource",
}
_FIRST_WRITE_DISCOVERY_VERDICT = "TASK_LOCAL_DISCOVERY_EVIDENCE"
_FIRST_WRITE_RECORD_TYPE = "task_local_discovery"


def _tool_payload(result: Dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(result["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {}


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


def _first_write_required_payload(
    *,
    agent_id: str,
    task_id: str,
    context_token: str,
    resource: str,
    reason: str,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "verdict": "FIRST_WRITE_DISCOVERY_REQUIRED",
        "state": "FIRST_WRITE_DISCOVERY_REQUIRED",
        "reason": reason,
        "task_id": task_id,
        "resource": resource,
        "historical_scribe_context_found": False,
        "must_call": {
            "tool": "scribe_record",
            "args": {
                "agent_id": agent_id,
                "task_id": task_id,
                "context_token": context_token,
                "record_type": _FIRST_WRITE_RECORD_TYPE,
                "memory_policy": "local_only",
                "request": f"First-write discovery for {resource}",
                "summary": (
                    "Describe the observed implementation, the intended bounded change, "
                    "the existing infrastructure that must be reused, and the regression risks."
                ),
                "evidence": (
                    "Provide concrete file-local and Graphify evidence discovered during this task."
                ),
                "root_cause": (
                    "SCRIBE has no relevant historical entry for this exact resource; this is a "
                    "first intervention, not permission to bypass MCP."
                ),
                "verdict": _FIRST_WRITE_DISCOVERY_VERDICT,
                "resources": [resource],
            },
        },
        "forbidden": [
            "claim_resource",
            "propose_patch",
            "apply_patch",
            "delete_resource",
            "direct_file_edit",
        ],
    }


def _first_write_discovery_gate(
    agent_id: str,
    task_id: str,
    context_token: str,
    resource: str = "",
) -> Dict[str, Any] | None:
    """Return a blocking payload only for an unresolved first-write task.

    A normal task with one or more relevant SCRIBE results follows the original
    path unchanged.  A task whose honest query found no relevant history must
    prove fresh, local-only discovery tied to the exact task and resource.
    """

    if not task_id:
        return None
    try:
        status = task_context.get_task_context(agent_id, task_id)
    except task_context.TaskContextError:
        return None

    if not status.get("scribe_done"):
        return None
    try:
        result_count = int(status.get("scribe_result_count") or 0)
    except (TypeError, ValueError):
        result_count = 0
    if result_count > 0:
        return None

    stored_resource = str(status.get("resource") or "")
    target_resource = resource or stored_resource
    if not target_resource:
        return _first_write_required_payload(
            agent_id=agent_id,
            task_id=task_id,
            context_token=context_token,
            resource="",
            reason="A first-write task requires one exact resource before discovery evidence can be accepted.",
        )
    if stored_resource and target_resource != stored_resource:
        return {
            "ok": False,
            "verdict": "TASK_CONTEXT_RESOURCE_MISMATCH",
            "state": "HARD_STOP",
            "reason": "First-write discovery resource does not match the stored task resource.",
            "task_id": task_id,
            "resource": target_resource,
            "stored_resource": stored_resource,
        }
    if status.get("requires_graphify") and not status.get("graphify_done"):
        return {
            "ok": False,
            "verdict": "GRAPHIFY_CONTEXT_REQUIRED",
            "state": "GRAPHIFY_CONTEXT_REQUIRED",
            "reason": "First-write discovery requires Graphify context before any write lease or patch.",
            "must_call": {
                "tool": "graphify_query",
                "args": {
                    "agent_id": agent_id,
                    "task_id": task_id,
                    "context_token": context_token,
                    "query": f"impact dependencies blast radius for {target_resource}",
                    "resource": target_resource,
                },
            },
        }
    if not status.get("scribe_record_done"):
        return _first_write_required_payload(
            agent_id=agent_id,
            task_id=task_id,
            context_token=context_token,
            resource=target_resource,
            reason="SCRIBE had no relevant history. Record task-local discovery before acquiring write authority.",
        )

    record_path = str(status.get("scribe_record_path") or "")
    candidate = (server.ROOT / record_path).resolve() if record_path else None
    records_root = (server.ROOT / ".agent" / "state" / "outputs" / "scribe-out" / "records").resolve()
    if candidate is None or not candidate.is_file() or not candidate.is_relative_to(records_root):
        return _first_write_required_payload(
            agent_id=agent_id,
            task_id=task_id,
            context_token=context_token,
            resource=target_resource,
            reason="Task-local discovery receipt is missing or outside the managed SCRIBE records directory.",
        )
    try:
        record = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _first_write_required_payload(
            agent_id=agent_id,
            task_id=task_id,
            context_token=context_token,
            resource=target_resource,
            reason="Task-local discovery receipt is unreadable or invalid JSON.",
        )

    evidence_resources = {
        str(item)
        for item in [*(record.get("resources") or []), *(record.get("touched_resources") or [])]
        if str(item)
    }
    evidence = " ".join(str(record.get(key) or "").strip() for key in ("summary", "evidence", "root_cause"))
    valid = all(
        (
            str(record.get("task_id") or "") == task_id,
            str(record.get("agent_id") or "") == agent_id,
            str(record.get("record_type") or "").strip().lower() == _FIRST_WRITE_RECORD_TYPE,
            str(record.get("memory_policy") or "").strip().lower() == "local_only",
            not bool(record.get("canonical_memory_required")),
            str(record.get("verdict") or "") == _FIRST_WRITE_DISCOVERY_VERDICT,
            target_resource in evidence_resources,
            len(evidence) >= 80,
        )
    )
    if not valid:
        return _first_write_required_payload(
            agent_id=agent_id,
            task_id=task_id,
            context_token=context_token,
            resource=target_resource,
            reason=(
                "Discovery evidence must be local_only, task/agent/resource-bound, non-canonical, "
                "and contain concrete summary/evidence/root-cause details."
            ),
        )
    return None


def scribe_query(
    query: str,
    limit: int = 5,
    agent_id: str = "",
    task_id: str = "",
    context_token: str = "",
) -> Dict[str, Any]:
    result = _BASE_SCRIBE_QUERY(
        query=query,
        limit=limit,
        agent_id=agent_id,
        task_id=task_id,
        context_token=context_token,
    )
    payload = _tool_payload(result)
    if payload.get("verdict") not in {"SCRIBE_CONTEXT_EMPTY", "SCRIBE_CONTEXT_IRRELEVANT_FOR_WRITE"}:
        return result
    if not (agent_id and task_id and context_token):
        return result
    try:
        status = task_context.get_task_context(agent_id, task_id)
    except task_context.TaskContextError:
        return result
    if str(status.get("intent") or "") not in {"write", "delete"}:
        return result

    try:
        marked = task_context.mark_scribe_done(
            agent_id,
            task_id,
            context_token,
            result_count=0,
            result_resources=str(status.get("resource") or ""),
        )
    except task_context.TaskContextError as exc:
        raise _context_error(exc) from exc  # type: ignore[name-defined]

    resource = str(status.get("resource") or "")
    response = _first_write_required_payload(
        agent_id=agent_id,
        task_id=task_id,
        context_token=context_token,
        resource=resource,
        reason=(
            "SCRIBE query executed successfully but found no relevant historical context for this exact resource. "
            "Proceed only through bounded task-local discovery; do not seed canonical memory to unlock the gate."
        ),
    )
    response.update({
        "ok": True,
        "verdict": "SCRIBE_HISTORY_ABSENT_FIRST_WRITE_DISCOVERY_REQUIRED",
        "state": "FIRST_WRITE_DISCOVERY_REQUIRED",
        "query": query,
        "original_scribe_verdict": payload.get("verdict"),
        "historical_scribe_context_found": False,
        "task_context": {
            "task_id": task_id,
            "scribe_done": True,
            "scribe_result_count": 0,
            "memory_hash": marked.get("memory_hash"),
        },
    })
    return server.ok(response)


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

    if action in _FIRST_WRITE_ACTIONS and task_id:
        blocked = _first_write_discovery_gate(agent_id, task_id, context_token, resource)
        if blocked is not None:
            return server.ok(blocked)

    return _BASE_PRE_ACTION_GUARD(
        agent_id=agent_id,
        request=request,
        intent=intent,
        resource=resource,
        task_id=task_id,
        context_token=context_token,
        planned_action=planned_action,
    )


def claim_resource(
    agent_id: str,
    resource: str,
    mode: str = "write",
    ttl_seconds: int = 1800,
    task_id: str = "",
    context_token: str = "",
    action_lease_id: str = "",
) -> Dict[str, Any]:
    if mode in server.WRITE_MODES:
        blocked = _first_write_discovery_gate(agent_id, task_id, context_token, resource)
        if blocked is not None:
            return server.ok(blocked)
    return _BASE_CLAIM_RESOURCE(
        agent_id=agent_id,
        resource=resource,
        mode=mode,
        ttl_seconds=ttl_seconds,
        task_id=task_id,
        context_token=context_token,
        action_lease_id=action_lease_id,
    )


def propose_patch(
    agent_id: str,
    target: str,
    base_hash: str,
    diff_text: str,
    task_id: str = "",
    context_token: str = "",
    action_lease_id: str = "",
) -> Dict[str, Any]:
    blocked = _first_write_discovery_gate(agent_id, task_id, context_token, target)
    if blocked is not None:
        return server.ok(blocked)
    return _BASE_PROPOSE_PATCH(
        agent_id=agent_id,
        target=target,
        base_hash=base_hash,
        diff_text=diff_text,
        task_id=task_id,
        context_token=context_token,
        action_lease_id=action_lease_id,
    )


def apply_patch(
    agent_id: str,
    patch_id: str,
    task_id: str = "",
    context_token: str = "",
    action_lease_id: str = "",
) -> Dict[str, Any]:
    blocked = _first_write_discovery_gate(agent_id, task_id, context_token)
    if blocked is not None:
        return server.ok(blocked)
    return _BASE_APPLY_PATCH(
        agent_id=agent_id,
        patch_id=patch_id,
        task_id=task_id,
        context_token=context_token,
        action_lease_id=action_lease_id,
    )


def delete_resource(
    agent_id: str,
    resource: str,
    base_hash: str,
    confirm_phrase: str = "",
    reason: str = "",
    task_id: str = "",
    context_token: str = "",
    action_lease_id: str = "",
) -> Dict[str, Any]:
    blocked = _first_write_discovery_gate(agent_id, task_id, context_token, resource)
    if blocked is not None:
        return server.ok(blocked)
    return _BASE_DELETE_RESOURCE(
        agent_id=agent_id,
        resource=resource,
        base_hash=base_hash,
        confirm_phrase=confirm_phrase,
        reason=reason,
        task_id=task_id,
        context_token=context_token,
        action_lease_id=action_lease_id,
    )


server.pre_action_guard = pre_action_guard
server.scribe_query = scribe_query
server.claim_resource = claim_resource
server.propose_patch = propose_patch
server.apply_patch = apply_patch
server.delete_resource = delete_resource
server.TOOLS["pre_action_guard"] = pre_action_guard
server.TOOLS["scribe_query"] = scribe_query
server.TOOLS["claim_resource"] = claim_resource
server.TOOLS["propose_patch"] = propose_patch
server.TOOLS["apply_patch"] = apply_patch
server.TOOLS["delete_resource"] = delete_resource
handle = server.handle
list_tools = server.list_tools
main = server.main


if __name__ == "__main__":
    raise SystemExit(server.main())
