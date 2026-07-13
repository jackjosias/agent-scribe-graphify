from __future__ import annotations

import json
from typing import Any, Dict

from runtime import task_context, task_discovery

FORBIDDEN = [
    "claim_resource", "resource_lock_claim", "propose_patch", "apply_patch",
    "delete_resource", "finish_task", "direct_file_edit",
]


def _payload(result: Dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(result["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {}


def build_scribe_query(server: Any, base_scribe: Any):
    def scribe_query(
        query: str,
        limit: int = 5,
        agent_id: str = "",
        task_id: str = "",
        context_token: str = "",
    ) -> Dict[str, Any]:
        active_base = getattr(server, "_BASE_SCRIBE_QUERY_POLICY", base_scribe)
        result = active_base(
            query=query, limit=limit, agent_id=agent_id,
            task_id=task_id, context_token=context_token,
        )
        if not (agent_id and task_id and context_token):
            return result
        try:
            task = task_context.verify_active_context(agent_id, task_id, context_token)
        except task_context.TaskContextError:
            return result
        canonical = task_context.normalize_intent(str(task.get("intent") or ""))
        if canonical not in {"write", "delete"}:
            return result

        data = _payload(result)
        resource = str(task.get("resource") or "")
        command = data.get("result") if isinstance(data.get("result"), dict) else {}
        stdout = str(command.get("stdout") or "")
        verdict = str(data.get("verdict") or "")
        relevant = False
        if resource and verdict == "SCRIBE_QUERY_DONE":
            try:
                relevant = task_discovery.result_is_relevant(resource, stdout)
            except Exception:
                relevant = False
        miss = verdict in {
            "SCRIBE_CONTEXT_EMPTY",
            "SCRIBE_CONTEXT_IRRELEVANT_FOR_WRITE",
        } or (verdict == "SCRIBE_QUERY_DONE" and not relevant)
        if not miss:
            if verdict == "SCRIBE_QUERY_DONE" and relevant:
                try:
                    task_discovery.clear_scribe_miss(agent_id, task_id, context_token)
                except Exception:
                    pass
            return result

        try:
            context_state = task_context.mark_scribe_done(
                agent_id, task_id, context_token,
                result_count=1 if stdout.strip() else 0,
                result_resources=resource,
            )
            discovery = task_discovery.mark_scribe_miss(
                agent_id, task_id, context_token, resource,
                query=query, stdout=stdout,
                reason=str(data.get("reason") or "no resource-relevant SCRIBE result"),
            )
        except (task_context.TaskContextError, task_discovery.TaskDiscoveryError) as exc:
            code = getattr(exc, "code", str(exc))
            return server.ok({
                "ok": False, "verdict": code, "state": "HARD_STOP",
                "reason": code, "details": getattr(exc, "details", {}),
                "forbidden": FORBIDDEN,
            })

        if bool(task.get("requires_graphify")) and not bool(task.get("graphify_done")):
            must_call = {
                "tool": "graphify_query",
                "args": {
                    "agent_id": agent_id, "task_id": task_id,
                    "context_token": context_token,
                    "query": f"impact dependencies blast radius for {resource}",
                    "resource": resource,
                },
            }
        else:
            must_call = {
                "tool": "workflow_next",
                "args": {
                    "agent_id": agent_id, "task_id": task_id,
                    "context_token": context_token, "intent": canonical,
                    "resource": resource,
                    "last_verdict": "SCRIBE_CONTEXT_MISS_FOR_WRITE",
                },
            }
        return server.ok({
            "ok": True,
            "verdict": "SCRIBE_CONTEXT_MISS_FOR_WRITE",
            "state": "FIRST_WRITE_DISCOVERY_REQUIRED",
            "reason": (
                "SCRIBE was queried honestly but returned no historical result "
                "relevant to the exact write resource. Bind fresh task-local "
                "discovery to its current base hash before any lock or claim."
            ),
            "query": query, "result": command,
            "task_context": {
                "task_id": task_id, "scribe_done": True,
                "memory_hash": context_state.get("memory_hash"),
            },
            "discovery": discovery, "must_call": must_call,
            "forbidden": FORBIDDEN,
        })
    return scribe_query


def build_scope_tool(server: Any):
    def scope_task_resource(
        agent_id: str = "", task_id: str = "",
        context_token: str = "", resource: str = "",
    ) -> Dict[str, Any]:
        try:
            result = task_context.scope_task_resource(
                agent_id, task_id, context_token, resource
            )
        except task_context.TaskContextError as exc:
            return server.ok({
                "ok": False, "verdict": exc.code, "state": "HARD_STOP",
                "reason": exc.code, "details": exc.details,
                "forbidden": FORBIDDEN,
            })
        return server.ok({
            "ok": True, "verdict": "TASK_RESOURCE_SCOPED",
            "state": "SCRIBE_CONTEXT_REQUIRED", **result,
            "must_call": {
                "tool": "scribe_query",
                "args": {
                    "agent_id": agent_id, "task_id": task_id,
                    "context_token": context_token,
                    "query": f"resource:{resource} intent:write first intervention",
                    "limit": 5,
                },
            },
            "forbidden": FORBIDDEN,
        })
    return scope_task_resource


def build_record_tool(server: Any):
    def record_task_discovery(
        agent_id: str = "", task_id: str = "",
        context_token: str = "", resource: str = "",
        base_hash: str = "", summary: str = "", evidence: str = "",
    ) -> Dict[str, Any]:
        try:
            result = task_discovery.record_discovery(
                agent_id, task_id, context_token, resource,
                base_hash, summary, evidence,
            )
        except task_discovery.TaskDiscoveryError as exc:
            return server.ok({
                "ok": False, "verdict": exc.code, "state": "HARD_STOP",
                "reason": exc.code, "details": exc.details,
                "forbidden": FORBIDDEN,
            })
        return server.ok(result)
    return record_task_discovery
