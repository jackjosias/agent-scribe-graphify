#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Dict

import server  # type: ignore
from runtime import delete_ops, patch_queue  # type: ignore

server.SERVER_VERSION = "0.2.4"
_BASE_WORKFLOW_NEXT = server.workflow_next
_BASE_TOOL_SCHEMA = server.tool_schema
_DELETE_INTENTS = {"delete", "remove"}


def delete_resource(agent_id: str, resource: str, base_hash: str, confirm_phrase: str = "", reason: str = "") -> Dict[str, Any]:
    return server.ok(delete_ops.delete_resource(agent_id=agent_id, resource=resource, base_hash=base_hash, confirm_phrase=confirm_phrase, reason=reason))


def _claims_for(agent_id: str, resource: str) -> Dict[str, Any]:
    return server._active_claims_for(agent_id, resource)


def workflow_next(
    agent_id: str = "",
    request: str = "",
    intent: str = "",
    resource: str = "",
    mode: str = "patch_queue",
    base_hash: str = "",
    patch_id: str = "",
    claim_id: str = "",
    last_verdict: str = "",
    host_tool: str = "unknown",
    model_name: str = "",
) -> Dict[str, Any]:
    normalized = (intent or "").strip().lower()
    if normalized not in _DELETE_INTENTS:
        return _BASE_WORKFLOW_NEXT(
            agent_id=agent_id,
            request=request,
            intent=intent,
            resource=resource,
            mode=mode,
            base_hash=base_hash,
            patch_id=patch_id,
            claim_id=claim_id,
            last_verdict=last_verdict,
            host_tool=host_tool,
            model_name=model_name,
        )

    if not agent_id or agent_id not in server._active_agent_ids():
        return server._next_payload(
            state="NO_ACTIVE_AGENT",
            tool="bootstrap",
            args={"host_tool": host_tool or "unknown", "model_name": model_name or "", "run_legacy_bootstrap": False},
            reason="No active registered agent_id is available. Bootstrap is mandatory before deletion planning.",
            forbidden=["claim_resource", "delete_resource", "finish_task", "direct_file_edit"],
        )

    if request and (last_verdict or "").strip() not in {"BEFORE_TASK_OK", "CLAIM_GRANTED", "FILE_HASH", "RESOURCE_DELETED", "DELETE_CONFIRMATION_REQUIRED"}:
        return server._next_payload(
            state="TASK_NOT_ACKED",
            tool="before_task",
            args={"request": request, "agent_id": agent_id},
            reason="The task must be acknowledged mechanically before deletion planning.",
            forbidden=["claim_resource", "delete_resource", "finish_task", "direct_file_edit"],
        )

    if not resource:
        return server.ok({
            "verdict": "INPUT_REQUIRED",
            "state": "RESOURCE_REQUIRED",
            "reason": "A delete intent requires an explicit project-relative resource.",
            "required_inputs": ["resource"],
            "forbidden": ["delete_resource", "finish_task", "direct_file_edit"],
        })

    safe = patch_queue.safe_resource(resource)
    claims = _claims_for(agent_id, safe)
    owned_write = [row for row in claims["owned"] if row.get("mode") in server.WRITE_MODES]
    if not owned_write:
        return server._next_payload(
            state="CLAIM_REQUIRED_FOR_DELETE",
            tool="claim_resource",
            args={"agent_id": agent_id, "resource": safe, "mode": "patch_queue", "ttl_seconds": 600},
            reason="A compatible claim is mandatory before deletion.",
            forbidden=["delete_resource", "direct_file_edit", "finish_task"],
            context={"foreign_claims": claims["foreign"]},
        )

    if not base_hash:
        return server._next_payload(
            state="BASE_HASH_REQUIRED_FOR_DELETE",
            tool="file_hash",
            args={"resource": safe},
            reason="Deletion requires a fresh base_hash before explicit user confirmation.",
            forbidden=["delete_resource", "direct_file_edit", "finish_task"],
            context={"claim": owned_write[0]},
        )

    confirmation = delete_ops.required_confirmation(safe)
    return server._next_payload(
        state="DELETE_PERMISSION_REQUIRED",
        tool="delete_resource",
        args={"agent_id": agent_id, "resource": safe, "base_hash": base_hash},
        reason=f"Ask the user for explicit permission before deletion. Required confirmation phrase: {confirmation}",
        forbidden=["direct_file_edit", "finish_task"],
        missing_inputs=["confirm_phrase", "reason"],
        context={"required_confirmation": confirmation, "claim": owned_write[0]},
    )


def tool_schema(name: str) -> Dict[str, Any]:
    if name == "delete_resource":
        return {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "resource": {"type": "string"},
                "base_hash": {"type": "string"},
                "confirm_phrase": {"type": "string"},
                "reason": {"type": "string"},
            },
            "additionalProperties": False,
        }
    return _BASE_TOOL_SCHEMA(name)


server.workflow_next = workflow_next
server.delete_resource = delete_resource
server.tool_schema = tool_schema
server.TOOLS["workflow_next"] = workflow_next
server.TOOLS["delete_resource"] = delete_resource

handle = server.handle
list_tools = server.list_tools
main = server.main

if __name__ == "__main__":
    raise SystemExit(server.main())
