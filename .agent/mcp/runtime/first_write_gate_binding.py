from __future__ import annotations

"""Final binding patch for hash-bound first-write discovery guards."""

import json
from typing import Any, Dict

from runtime import first_write_hardening, task_discovery


def _payload(result: Dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(result["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {}


def install(server: Any) -> None:
    if getattr(server, "_FIRST_WRITE_GATE_BINDING_INSTALLED", False):
        return

    def gate(
        agent_id: str,
        task_id: str,
        context_token: str,
        resource: str = "",
        base_hash: str = "",
    ) -> dict[str, Any] | None:
        task = first_write_hardening._task(agent_id, task_id)
        if task is None:
            return None
        history_absent = bool(task.get("scribe_history_absent")) or task_discovery.scribe_miss_exists(task_id)
        if not history_absent:
            return None
        target = resource or str(task.get("resource") or "")
        if first_write_hardening._coarse(target):
            return first_write_hardening._scope_payload(agent_id, task_id, context_token, target)
        if bool(task.get("requires_graphify")) and not bool(task.get("graphify_done")):
            return {
                "ok": False,
                "verdict": "TASK_DISCOVERY_GRAPHIFY_REQUIRED",
                "state": "GRAPHIFY_CONTEXT_REQUIRED",
                "must_call": {
                    "tool": "graphify_query",
                    "args": {
                        "agent_id": agent_id,
                        "task_id": task_id,
                        "context_token": context_token,
                        "query": f"impact dependencies blast radius for {target}",
                        "resource": target,
                    },
                },
                "forbidden": first_write_hardening._FORBIDDEN,
            }
        try:
            task_discovery.require_discovery_ready(agent_id, task_id, resource=target)
        except task_discovery.TaskDiscoveryError as exc:
            return first_write_hardening._required_payload(
                agent_id=agent_id,
                task_id=task_id,
                context_token=context_token,
                resource=target,
                base_hash=base_hash,
                reason=exc.code,
            )
        return None

    # The outer server facade functions retain the globals dict in which they
    # were defined. Rebind the gate there explicitly instead of assuming a
    # runpy return dictionary is the same object on every Python implementation.
    for name in (
        "pre_action_guard",
        "claim_resource",
        "propose_patch",
        "apply_patch",
        "delete_resource",
    ):
        function = server.TOOLS.get(name)
        globals_dict = getattr(function, "__globals__", None)
        if isinstance(globals_dict, dict):
            globals_dict["_first_write_discovery_gate"] = gate
            globals_dict["_first_write_required_payload"] = first_write_hardening._required_payload

    current_scribe = server.TOOLS["scribe_query"]

    def scribe_query(
        query: str,
        limit: int = 5,
        agent_id: str = "",
        task_id: str = "",
        context_token: str = "",
    ) -> Dict[str, Any]:
        result = current_scribe(
            query=query,
            limit=limit,
            agent_id=agent_id,
            task_id=task_id,
            context_token=context_token,
        )
        data = _payload(result)
        if data.get("verdict") == "SCRIBE_CONTEXT_MISS_FOR_WRITE" and task_id:
            data["discovery"] = task_discovery.status(task_id)
            return server.ok(data)
        return result

    server.scribe_query = scribe_query
    server.TOOLS["scribe_query"] = scribe_query
    server._FIRST_WRITE_GATE_BINDING_INSTALLED = True
