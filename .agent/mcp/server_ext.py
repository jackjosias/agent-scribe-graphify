#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from typing import Any, Dict, List

import server  # type: ignore
from runtime import delete_ops, patch_queue  # type: ignore
from runtime.state_paths import prepare_state_dirs  # type: ignore

server.SERVER_VERSION = "0.2.6"
_BASE_WORKFLOW_NEXT = server.workflow_next
_BASE_TOOL_SCHEMA = server.tool_schema
_DELETE_INTENTS = {"delete", "remove"}
_SCRIBE_VERDICTS = {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}
_GRAPHIFY_VERDICTS = {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}
_CONTEXT_VERDICTS = {"BEFORE_TASK_OK", *_SCRIBE_VERDICTS, *_GRAPHIFY_VERDICTS}
_WRITE_DONE_VERDICTS = {"PATCH_APPLIED", "PATCH_APPLIED_CONFIRMED", "RESOURCE_DELETED"}
_RECORD_REQUIRED_VERDICTS = {*_WRITE_DONE_VERDICTS, "CLAIM_RELEASED"}
_RECORD_DONE_VERDICTS = {"SCRIBE_RECORD_WRITTEN"}
_WRITE_OR_DECISION_INTENTS = {"write", "edit", "patch", "modify", "code", "fix", "refactor", "test", "create", "delete", "remove", "decision"}
_GRAPHIFY_KEYWORDS = {"api", "architecture", "backend", "base de données", "bug", "code", "database", "db", "frontend", "migration", "module", "production", "refactor", "sécurité", "security", "test"}
_DEBUG_KEYWORDS = {"bug", "debug", "erreur", "error", "fail", "failure", "fix", "regression", "refactor", "test"}


def _last(last_verdict: str) -> str:
    return (last_verdict or "").strip()


def _request_text(request: str, intent: str, resource: str) -> str:
    return " ".join(part for part in [request, intent, resource] if part).lower()


def _requires_graphify(request: str, intent: str, resource: str) -> bool:
    text = _request_text(request, intent, resource)
    if (intent or "").strip().lower() in _WRITE_OR_DECISION_INTENTS:
        return True
    return any(keyword in text for keyword in _GRAPHIFY_KEYWORDS)


def _requires_scribe_record(intent: str, last_verdict: str) -> bool:
    normalized = (intent or "").strip().lower()
    return _last(last_verdict) in _RECORD_REQUIRED_VERDICTS or normalized in _WRITE_OR_DECISION_INTENTS


def _targeted_scribe_query(request: str, intent: str, resource: str) -> str:
    parts = [request.strip()]
    if resource:
        parts.append(f"resource:{resource}")
    if intent:
        parts.append(f"intent:{intent}")
    text = _request_text(request, intent, resource)
    if any(keyword in text for keyword in _DEBUG_KEYWORDS):
        parts.append("scar regression decision ne_pas_reproposer root_cause")
    return " ".join(part for part in parts if part).strip()


def _targeted_graphify_query(request: str, intent: str, resource: str) -> str:
    target = resource or request or intent or "current task"
    return f"impact dependencies blast radius for {target}"


def _context_gate(agent_id: str, request: str, intent: str, resource: str, last_verdict: str) -> Dict[str, Any] | None:
    last = _last(last_verdict)
    if not request:
        return None
    if last not in _CONTEXT_VERDICTS:
        return server._next_payload(
            state="TASK_NOT_ACKED",
            tool="before_task",
            args={"request": request, "agent_id": agent_id},
            reason="The task must be acknowledged before SCRIBE, Graphify, claims, hashes, patches, deletion or finish.",
            forbidden=["claim_resource", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"],
        )
    if last == "BEFORE_TASK_OK":
        return server._next_payload(
            state="SCRIBE_CONTEXT_REQUIRED",
            tool="scribe_query",
            args={"query": _targeted_scribe_query(request, intent, resource), "limit": 5},
            reason="Targeted SCRIBE RAG query is required, not full memory read.",
            forbidden=["claim_resource", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"],
        )
    if last in _SCRIBE_VERDICTS and _requires_graphify(request, intent, resource):
        return server._next_payload(
            state="GRAPHIFY_CONTEXT_REQUIRED",
            tool="graphify_query",
            args={"query": _targeted_graphify_query(request, intent, resource), "resource": resource or ""},
            reason="Targeted Graphify impact query is required.",
            forbidden=["claim_resource", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"],
        )
    return None


def delete_resource(agent_id: str, resource: str, base_hash: str, confirm_phrase: str = "", reason: str = "") -> Dict[str, Any]:
    return server.ok(delete_ops.delete_resource(agent_id=agent_id, resource=resource, base_hash=base_hash, confirm_phrase=confirm_phrase, reason=reason))


def scribe_record(
    agent_id: str = "",
    request: str = "",
    summary: str = "",
    touched_resources: List[str] | None = None,
    verdict: str = "",
    tags: List[str] | None = None,
    record_type: str = "task_summary",
    severity: str = "medium",
    evidence: str = "",
    root_cause: str = "",
    fix: str = "",
    prevention: str = "",
    related_errors: List[str] | None = None,
    related_tests: List[str] | None = None,
    resources: List[str] | None = None,
) -> Dict[str, Any]:
    if not agent_id:
        raise server.ToolError("agent_id is required")
    now = int(time.time())
    merged_resources = resources if resources is not None else touched_resources
    payload = {
        "timestamp": now,
        "agent_id": agent_id,
        "record_type": record_type or "task_summary",
        "severity": severity or "medium",
        "request": request or "",
        "summary": summary or "",
        "touched_resources": touched_resources or [],
        "resources": merged_resources or [],
        "verdict": verdict or "",
        "evidence": evidence or "",
        "root_cause": root_cause or "",
        "fix": fix or "",
        "prevention": prevention or "",
        "related_errors": related_errors or [],
        "related_tests": related_tests or [],
        "tags": tags or [],
    }
    paths = prepare_state_dirs(server.ROOT)
    records = paths["scribe_out"] / "records"
    records.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    target = records / f"{now}-{agent_id[:12]}-{digest}.json"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(records))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return server.ok({"verdict": "SCRIBE_RECORD_WRITTEN", "record": str(target.relative_to(server.ROOT)), "entry": payload})


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
    last = _last(last_verdict)

    if not agent_id or agent_id not in server._active_agent_ids():
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

    if normalized in server.FINISH_INTENTS and last not in _RECORD_DONE_VERDICTS:
        pending = server._agent_pending_patches(agent_id, resource)
        if pending:
            return _BASE_WORKFLOW_NEXT(agent_id=agent_id, request=request, intent=intent, resource=resource, mode=mode, base_hash=base_hash, patch_id=patch_id, claim_id=claim_id, last_verdict=last_verdict, host_tool=host_tool, model_name=model_name)
        claims = server._active_claims_for(agent_id)
        if claims["owned"]:
            return _BASE_WORKFLOW_NEXT(agent_id=agent_id, request=request, intent=intent, resource=resource, mode=mode, base_hash=base_hash, patch_id=patch_id, claim_id=claim_id, last_verdict=last_verdict, host_tool=host_tool, model_name=model_name)
        if _requires_scribe_record(intent, last_verdict):
            return server._next_payload(
                state="SCRIBE_RECORD_REQUIRED",
                tool="scribe_record",
                args={
                    "agent_id": agent_id,
                    "request": request or "task completed",
                    "summary": "record useful task outcome before finish",
                    "touched_resources": [resource] if resource else [],
                    "resources": [resource] if resource else [],
                    "verdict": last or "READY_TO_FINISH",
                    "record_type": "task_summary",
                    "severity": "medium",
                    "tags": ["workflow_next"],
                },
                reason="Typed memory recording is required before finish_task when useful: writes, deletions, tests, refactors, decisions, scars, debt or conflicts.",
                forbidden=["finish_task", "direct_file_edit"],
            )

    gate = _context_gate(agent_id=agent_id, request=request, intent=intent, resource=resource, last_verdict=last_verdict)
    if gate is not None:
        return gate

    if normalized not in _DELETE_INTENTS:
        delegated_last = last_verdict
        if last == "GRAPHIFY_UNAVAILABLE":
            delegated_last = "GRAPHIFY_QUERY_DONE"
        elif last == "SCRIBE_UNAVAILABLE" and not _requires_graphify(request, intent, resource):
            delegated_last = "SCRIBE_QUERY_DONE"
        return _BASE_WORKFLOW_NEXT(
            agent_id=agent_id,
            request=request,
            intent=intent,
            resource=resource,
            mode=mode,
            base_hash=base_hash,
            patch_id=patch_id,
            claim_id=claim_id,
            last_verdict=delegated_last,
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
    if name == "scribe_record":
        return {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "request": {"type": "string"},
                "summary": {"type": "string"},
                "touched_resources": {"type": "array", "items": {"type": "string"}},
                "verdict": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "record_type": {"type": "string"},
                "severity": {"type": "string"},
                "evidence": {"type": "string"},
                "root_cause": {"type": "string"},
                "fix": {"type": "string"},
                "prevention": {"type": "string"},
                "related_errors": {"type": "array", "items": {"type": "string"}},
                "related_tests": {"type": "array", "items": {"type": "string"}},
                "resources": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["agent_id", "request", "summary", "touched_resources", "verdict"],
            "additionalProperties": False,
        }
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
server.scribe_record = scribe_record
server.tool_schema = tool_schema
server.TOOLS["workflow_next"] = workflow_next
server.TOOLS["delete_resource"] = delete_resource
server.TOOLS["scribe_record"] = scribe_record

handle = server.handle
list_tools = server.list_tools
main = server.main

if __name__ == "__main__":
    raise SystemExit(server.main())
