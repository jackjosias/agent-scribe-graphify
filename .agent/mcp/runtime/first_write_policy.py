from __future__ import annotations

from typing import Any, Dict

from runtime.first_write_scribe import (
    build_record_tool,
    build_scope_tool,
    build_scribe_query,
)
from runtime.first_write_workflow import build_workflow_next


def install(server: Any) -> None:
    if getattr(server, "_FIRST_WRITE_POLICY_INSTALLED", False):
        return
    base_scribe = server.TOOLS["scribe_query"]
    server._BASE_SCRIBE_QUERY_POLICY = base_scribe
    base_workflow = server.TOOLS["workflow_next"]
    base_schema = server.tool_schema

    scribe_query = build_scribe_query(server, base_scribe)
    workflow_next = build_workflow_next(server, base_workflow)
    scope_task_resource = build_scope_tool(server)
    record_task_discovery = build_record_tool(server)

    def tool_schema(name: str) -> Dict[str, Any]:
        if name == "scope_task_resource":
            keys = ("agent_id", "task_id", "context_token", "resource")
            return {
                "type": "object",
                "properties": {key: {"type": "string"} for key in keys},
                "required": list(keys),
                "additionalProperties": False,
            }
        if name == "record_task_discovery":
            keys = (
                "agent_id", "task_id", "context_token", "resource",
                "base_hash", "summary", "evidence",
            )
            return {
                "type": "object",
                "properties": {key: {"type": "string"} for key in keys},
                "required": list(keys),
                "additionalProperties": False,
            }
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
    server._FIRST_WRITE_POLICY_INSTALLED = True
