from __future__ import annotations

import json
from typing import Any, Dict

from runtime import patch_queue, task_context, task_discovery

_FORBIDDEN = [
    "resource_lock_claim", "claim_resource", "propose_patch", "apply_patch",
    "delete_resource", "direct_file_edit",
]
_COARSE = {
    "", ".", "(whole repo)", "whole repo", "whole-repo",
    "repository", "repo", "project", "project-wide",
}


def _payload(result: Dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(result["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {}


def _coarse(resource: str) -> bool:
    return task_context.is_scope_container(resource)


def _task(agent_id: str, task_id: str) -> dict[str, Any] | None:
    if not task_id:
        return None
    try:
        task = task_context.get_task_context(agent_id, task_id)
    except task_context.TaskContextError:
        return None
    if task.get("status") != "active":
        return None
    if task_context.normalize_intent(str(task.get("intent") or "")) not in {"write", "delete"}:
        return None
    return task


def _scope_payload(agent_id: str, task_id: str, token: str, requested: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "verdict": "TASK_EXACT_RESOURCE_REQUIRED",
        "state": "RESOURCE_SCOPING_REQUIRED",
        "reason": "Repository-wide discovery may inspect broadly, but write authority requires one exact project-relative file.",
        "required_inputs": ["resource"],
        "must_call": {
            "tool": "scope_task_resource",
            "args": {"agent_id": agent_id, "task_id": task_id, "context_token": token, "resource": requested},
        },
        "forbidden": _FORBIDDEN,
    }


def _required_payload(
    *, agent_id: str, task_id: str, context_token: str,
    resource: str, reason: str = "", base_hash: str = "",
) -> dict[str, Any]:
    if _coarse(resource):
        return _scope_payload(agent_id, task_id, context_token, resource)
    if not base_hash:
        return {
            "ok": False,
            "verdict": "TASK_DISCOVERY_BASE_HASH_REQUIRED",
            "state": "DISCOVERY_BASE_HASH_REQUIRED",
            "reason": reason or "Discovery must bind to the exact current file hash before ownership is granted.",
            "must_call": {"tool": "file_hash", "args": {"resource": resource}},
            "forbidden": _FORBIDDEN,
        }
    return {
        "ok": False,
        "verdict": "TASK_DISCOVERY_REQUIRED",
        "state": "FIRST_WRITE_DISCOVERY_REQUIRED",
        "reason": reason or "Bind task-local discovery to this task, exact resource and fresh base hash.",
        "must_call": {
            "tool": "record_task_discovery",
            "args": {
                "agent_id": agent_id,
                "task_id": task_id,
                "context_token": context_token,
                "resource": resource,
                "base_hash": base_hash,
            },
        },
        "missing_inputs": ["summary", "evidence"],
        "forbidden": _FORBIDDEN,
    }


def install(server: Any, namespace: dict[str, Any]) -> None:
    if getattr(server, "_FIRST_WRITE_HASH_POLICY_INSTALLED", False):
        return
    current_scribe = server.TOOLS["scribe_query"]
    current_workflow = server.TOOLS["workflow_next"]
    base_schema = server.tool_schema
    server._BASE_SCRIBE_QUERY_POLICY = current_scribe

    def gate(agent_id: str, task_id: str, context_token: str, resource: str = "", base_hash: str = "") -> dict[str, Any] | None:
        task = _task(agent_id, task_id)
        if task is None:
            return None
        history_absent = bool(task.get("scribe_history_absent")) or task_discovery.scribe_miss_exists(task_id)
        if not history_absent:
            return None
        target = resource or str(task.get("resource") or "")
        if _coarse(target):
            return _scope_payload(agent_id, task_id, context_token, target)
        if bool(task.get("requires_graphify")) and not bool(task.get("graphify_done")):
            return {
                "ok": False,
                "verdict": "TASK_DISCOVERY_GRAPHIFY_REQUIRED",
                "state": "GRAPHIFY_CONTEXT_REQUIRED",
                "must_call": {
                    "tool": "graphify_query",
                    "args": {
                        "agent_id": agent_id, "task_id": task_id,
                        "context_token": context_token,
                        "query": f"impact dependencies blast radius for {target}",
                        "resource": target,
                    },
                },
                "forbidden": _FORBIDDEN,
            }
        try:
            task_discovery.require_discovery_ready(agent_id, task_id, resource=target)
        except task_discovery.TaskDiscoveryError as exc:
            return _required_payload(
                agent_id=agent_id, task_id=task_id, context_token=context_token,
                resource=target, base_hash=base_hash, reason=exc.code,
            )
        return None

    # Existing pre-action/claim/patch wrappers resolve this global at call time.
    namespace["_first_write_discovery_gate"] = gate
    namespace["_first_write_required_payload"] = _required_payload

    def scribe_query(
        query: str, limit: int = 5, agent_id: str = "", task_id: str = "", context_token: str = "",
    ) -> Dict[str, Any]:
        active = getattr(server, "_BASE_SCRIBE_QUERY_POLICY", current_scribe)
        result = active(query=query, limit=limit, agent_id=agent_id, task_id=task_id, context_token=context_token)
        task = _task(agent_id, task_id)
        if task is None or not context_token:
            return result
        resource = str(task.get("resource") or "")
        data = _payload(result)
        verdict = str(data.get("verdict") or "")
        command = data.get("result") if isinstance(data.get("result"), dict) else {}
        stdout = str(command.get("stdout") or "")
        if verdict == "SCRIBE_QUERY_DONE" and task_discovery.result_is_relevant(resource, stdout):
            task_discovery.clear_scribe_miss(agent_id, task_id, context_token)
            return result
        if verdict not in {
            "SCRIBE_QUERY_DONE", "SCRIBE_CONTEXT_EMPTY",
            "SCRIBE_CONTEXT_IRRELEVANT_FOR_WRITE",
            "SCRIBE_HISTORY_ABSENT_FIRST_WRITE_DISCOVERY_REQUIRED",
        }:
            return result
        if _coarse(resource):
            response = _scope_payload(agent_id, task_id, context_token)
            response.update({"ok": True, "verdict": "SCRIBE_HISTORY_ABSENT_RESOURCE_SCOPE_REQUIRED"})
            return server.ok(response)
        try:
            task_context.mark_scribe_done(agent_id, task_id, context_token, result_count=0, result_resources=resource)
            miss = task_discovery.mark_scribe_miss(
                agent_id, task_id, context_token, resource,
                query=query, stdout=stdout,
                reason="SCRIBE returned no resource-relevant historical result",
            )
        except (task_context.TaskContextError, task_discovery.TaskDiscoveryError) as exc:
            code = getattr(exc, "code", str(exc))
            return server.ok({"ok": False, "verdict": code, "state": "HARD_STOP", "reason": code, "details": getattr(exc, "details", {}), "forbidden": _FORBIDDEN})
        return server.ok({
            "ok": True,
            "verdict": "SCRIBE_CONTEXT_MISS_FOR_WRITE",
            "state": "FIRST_WRITE_DISCOVERY_REQUIRED",
            "historical_scribe_context_found": False,
            "task_id": task_id,
            "resource": resource,
            "scribe_miss": miss,
            "must_call": (
                {"tool": "graphify_query", "args": {"agent_id": agent_id, "task_id": task_id, "context_token": context_token, "query": f"impact dependencies blast radius for {resource}", "resource": resource}}
                if bool(task.get("requires_graphify")) and not bool(task.get("graphify_done"))
                else {"tool": "file_hash", "args": {"resource": resource}}
            ),
            "forbidden": _FORBIDDEN,
        })

    def scope_task_resource(agent_id: str = "", task_id: str = "", context_token: str = "", resource: str = "") -> Dict[str, Any]:
        try:
            scoped = task_context.scope_task_resource(agent_id, task_id, context_token, resource)
        except task_context.TaskContextError as exc:
            return server.ok({"ok": False, "verdict": exc.code, "state": "HARD_STOP", "reason": exc.code, "details": exc.details, "forbidden": _FORBIDDEN})
        return server.ok({
            "ok": True, "verdict": "TASK_RESOURCE_SCOPED", "state": "SCRIBE_CONTEXT_REQUIRED", **scoped,
            "must_call": {"tool": "scribe_query", "args": {"agent_id": agent_id, "task_id": task_id, "context_token": context_token, "query": f"resource:{resource} intent:write first intervention", "limit": 5}},
            "forbidden": _FORBIDDEN,
        })

    def record_task_discovery(
        agent_id: str = "", task_id: str = "", context_token: str = "", resource: str = "",
        base_hash: str = "", summary: str = "", evidence: str = "",
    ) -> Dict[str, Any]:
        try:
            return server.ok(task_discovery.record_discovery(agent_id, task_id, context_token, resource, base_hash, summary, evidence))
        except task_discovery.TaskDiscoveryError as exc:
            return server.ok({"ok": False, "verdict": exc.code, "state": "HARD_STOP", "reason": exc.code, "details": exc.details, "forbidden": _FORBIDDEN})

    def workflow_next(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        agent_id = str(kwargs.get("agent_id") or "")
        task_id = str(kwargs.get("task_id") or "")
        token = str(kwargs.get("context_token") or "")
        task = _task(agent_id, task_id)
        if task is not None:
            target = str(kwargs.get("resource") or task.get("resource") or "")
            if _coarse(str(task.get("resource") or "")):
                return server.ok(_scope_payload(agent_id, task_id, token, target))
            blocked = gate(agent_id, task_id, token, target, str(kwargs.get("base_hash") or ""))
            if blocked is not None:
                return server.ok(blocked)
        return current_workflow(*args, **kwargs)

    def tool_schema(name: str) -> Dict[str, Any]:
        if name == "scope_task_resource":
            keys = ("agent_id", "task_id", "context_token", "resource")
            return {"type": "object", "properties": {key: {"type": "string"} for key in keys}, "required": list(keys), "additionalProperties": False}
        if name == "record_task_discovery":
            keys = ("agent_id", "task_id", "context_token", "resource", "base_hash", "summary", "evidence")
            return {"type": "object", "properties": {key: {"type": "string"} for key in keys}, "required": list(keys), "additionalProperties": False}
        return base_schema(name)

    server.scribe_query = scribe_query
    server.workflow_next = workflow_next
    server.scope_task_resource = scope_task_resource
    server.record_task_discovery = record_task_discovery
    server.tool_schema = tool_schema
    server.TOOLS["scribe_query"] = scribe_query
    server.TOOLS["workflow_next"] = workflow_next
    server.TOOLS["scope_task_resource"] = scope_task_resource
    server.TOOLS["record_task_discovery"] = record_task_discovery
    server._FIRST_WRITE_HASH_POLICY_INSTALLED = True
