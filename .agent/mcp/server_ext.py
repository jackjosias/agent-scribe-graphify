#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from typing import Any, Dict, List

import server  # type: ignore
from runtime import delete_ops, patch_queue, task_context  # type: ignore
from runtime.state_paths import prepare_state_dirs  # type: ignore

server.SERVER_VERSION = "0.2.8"
_BASE_WORKFLOW_NEXT = server.workflow_next
_BASE_TOOL_SCHEMA = server.tool_schema
_BASE_BEFORE_TASK = server.before_task
_BASE_SCRIBE_QUERY = server.scribe_query
_BASE_GRAPHIFY_QUERY = server.graphify_query
_BASE_FINISH_TASK = server.finish_task
_DELETE_INTENTS = {"delete", "remove"}
_SCRIBE_VERDICTS = {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}
_GRAPHIFY_VERDICTS = {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}
_CONTEXT_VERDICTS = {"BEFORE_TASK_OK", *_SCRIBE_VERDICTS, *_GRAPHIFY_VERDICTS}
_WRITE_DONE_VERDICTS = {"PATCH_APPLIED", "PATCH_APPLIED_CONFIRMED", "RESOURCE_DELETED"}
_RECORD_REQUIRED_VERDICTS = {*_WRITE_DONE_VERDICTS, "CLAIM_RELEASED"}
_RECORD_DONE_VERDICTS = {"SCRIBE_RECORD_WRITTEN"}
_WRITE_OR_DECISION_INTENTS = {"write", "edit", "patch", "modify", "code", "fix", "refactor", "test", "create", "delete", "remove", "decision"}
_MUTATING_CONTEXT_INTENTS = {"write", "edit", "patch", "modify", "code", "fix", "refactor", "test", "create", "delete", "remove"}
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


def _missing_context_payload() -> Dict[str, Any]:
    return server.ok({
        "verdict": "INPUT_REQUIRED",
        "state": "TASK_CONTEXT_REQUIRED",
        "reason": "task_id and context_token returned by before_task are required for this workflow step.",
        "required_inputs": ["task_id", "context_token"],
        "missing_inputs": ["task_id", "context_token"],
        "forbidden": ["claim_resource", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"],
    })


def before_task(request: str, agent_id: str = "", intent: str = "", resource: str = "") -> Dict[str, Any]:
    result = _BASE_BEFORE_TASK(request=request, agent_id=agent_id)
    payload = json.loads(result["content"][0]["text"])
    if payload.get("verdict") != "BEFORE_TASK_OK":
        return result
    try:
        context = task_context.create_task_context(
            agent_id=agent_id,
            request=request,
            intent=intent or "",
            resource=resource or "",
            requires_graphify=_requires_graphify(request, intent, resource),
        )
    except task_context.TaskContextError as exc:
        if str(exc) == "ACTIVE_TASK_EXISTS":
            return server.ok({
                "verdict": "ACTIVE_TASK_EXISTS",
                "state": "ACTIVE_TASK_EXISTS",
                "reason": "An active task already exists for this agent/request/resource. Use resume_task/task_status or start a new task explicitly; before_task will not rotate context silently.",
                "agent_id": agent_id,
                "resource": resource or "",
                "forbidden": ["claim_resource", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"],
            })
        raise _context_error(exc) from exc
    payload.update(context)
    return server.ok(payload)


def _context_gate(
    agent_id: str,
    request: str,
    intent: str,
    resource: str,
    last_verdict: str,
    task_id: str,
    context_token: str,
) -> Dict[str, Any] | None:
    last = _last(last_verdict)
    if not request:
        return None
    if last not in _CONTEXT_VERDICTS:
        return server._next_payload(
            state="TASK_NOT_ACKED",
            tool="before_task",
            args={"request": request, "agent_id": agent_id, "intent": intent or "", "resource": resource or ""},
            reason="The task must be acknowledged before SCRIBE, Graphify, claims, hashes, patches, deletion or finish.",
            forbidden=["claim_resource", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"],
        )
    if not task_id or not context_token:
        return _missing_context_payload()
    if last == "BEFORE_TASK_OK":
        return server._next_payload(
            state="SCRIBE_CONTEXT_REQUIRED",
            tool="scribe_query",
            args={"agent_id": agent_id, "task_id": task_id, "context_token": context_token, "query": _targeted_scribe_query(request, intent, resource), "limit": 5},
            reason="Targeted SCRIBE RAG query is required, not full memory read.",
            forbidden=["claim_resource", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"],
        )
    if last in _SCRIBE_VERDICTS and _requires_graphify(request, intent, resource):
        return server._next_payload(
            state="GRAPHIFY_CONTEXT_REQUIRED",
            tool="graphify_query",
            args={"agent_id": agent_id, "task_id": task_id, "context_token": context_token, "query": _targeted_graphify_query(request, intent, resource), "resource": resource or ""},
            reason="Targeted Graphify impact query is required.",
            forbidden=["claim_resource", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"],
        )
    return None


def _context_error(exc: task_context.TaskContextError) -> server.ToolError:
    return server.ToolError(str(exc))


def _require_context_ready(
    agent_id: str,
    task_id: str,
    context_token: str,
    resource: str,
    strict_resource: bool = False,
    allowed_intents: set[str] | None = None,
) -> Dict[str, Any]:
    try:
        return task_context.require_context_ready(
            agent_id,
            task_id,
            context_token,
            resource=resource,
            strict_resource=strict_resource,
            allowed_intents=allowed_intents,
        )
    except task_context.TaskContextError as exc:
        raise _context_error(exc) from exc


def scribe_query(query: str, limit: int = 5, agent_id: str = "", task_id: str = "", context_token: str = "") -> Dict[str, Any]:
    result = _BASE_SCRIBE_QUERY(query=query, limit=limit)
    if agent_id or task_id or context_token:
        try:
            task_context.mark_scribe_done(agent_id, task_id, context_token)
        except task_context.TaskContextError as exc:
            raise _context_error(exc) from exc
        payload = json.loads(result["content"][0]["text"])
        payload["task_context"] = {"task_id": task_id, "scribe_done": True}
        return server.ok(payload)
    return result


def graphify_query(query: str = "", resource: str = "", agent_id: str = "", task_id: str = "", context_token: str = "") -> Dict[str, Any]:
    result = _BASE_GRAPHIFY_QUERY(query=query, resource=resource)
    if agent_id or task_id or context_token:
        try:
            task_context.mark_graphify_done(agent_id, task_id, context_token)
        except task_context.TaskContextError as exc:
            raise _context_error(exc) from exc
        payload = json.loads(result["content"][0]["text"])
        payload["task_context"] = {"task_id": task_id, "graphify_done": True}
        return server.ok(payload)
    return result


def propose_patch(agent_id: str, target: str, base_hash: str, diff_text: str, task_id: str = "", context_token: str = "") -> Dict[str, Any]:
    _require_context_ready(
        agent_id,
        task_id,
        context_token,
        target,
        strict_resource=True,
        allowed_intents=_MUTATING_CONTEXT_INTENTS,
    )
    return server.ok(patch_queue.propose_patch(agent_id=agent_id, target=target, base_hash=base_hash, diff_text=diff_text))


def delete_resource(
    agent_id: str,
    resource: str,
    base_hash: str,
    confirm_phrase: str = "",
    reason: str = "",
    task_id: str = "",
    context_token: str = "",
) -> Dict[str, Any]:
    _require_context_ready(
        agent_id,
        task_id,
        context_token,
        resource,
        strict_resource=True,
        allowed_intents=_MUTATING_CONTEXT_INTENTS,
    )
    return server.ok(delete_ops.delete_resource(agent_id=agent_id, resource=resource, base_hash=base_hash, confirm_phrase=confirm_phrase, reason=reason))


def finish_task(agent_id: str, summary: str = "", task_id: str = "", context_token: str = "") -> Dict[str, Any]:
    result = _BASE_FINISH_TASK(agent_id=agent_id, summary=summary)
    if task_id or context_token:
        payload = json.loads(result["content"][0]["text"])
        if payload.get("verdict") == "TASK_FINISHED_OK":
            try:
                payload["task_context"] = task_context.finish_task_context(agent_id, task_id, context_token)
            except task_context.TaskContextError as exc:
                raise _context_error(exc) from exc
            return server.ok(payload)
    return result


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
    task_id: str = "",
    context_token: str = "",
) -> Dict[str, Any]:
    normalized = (intent or "").strip().lower()
    last = _last(last_verdict)

    if last.startswith("TASK_CONTEXT") or last in {"READ_INTENT_CANNOT_WRITE", "ACTIVE_TASK_EXISTS"}:
        retry = task_context.record_retry(agent_id, resource, last)
        if retry.get("verdict") in {"RETRY_LOOP_DETECTED", "MAX_WORKFLOW_RETRIES_EXCEEDED"}:
            return server.ok({
                "verdict": retry["verdict"],
                "state": retry["verdict"],
                "reason": "The same agent/resource/error repeated. Stop instead of retrying or falling back to direct writes.",
                "retry": retry,
                "forbidden": ["bootstrap", "before_task", "claim_resource", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"],
            })

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

    gate = _context_gate(agent_id, request, intent, resource, last_verdict, task_id, context_token)
    if gate is not None:
        return gate

    if normalized not in _DELETE_INTENTS:
        delegated_last = last_verdict
        if last == "GRAPHIFY_UNAVAILABLE":
            delegated_last = "GRAPHIFY_QUERY_DONE"
        elif last == "SCRIBE_UNAVAILABLE" and not _requires_graphify(request, intent, resource):
            delegated_last = "SCRIBE_QUERY_DONE"
        result = _BASE_WORKFLOW_NEXT(
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
        payload = json.loads(result["content"][0]["text"])
        if (payload.get("must_call") or {}).get("tool") == "propose_patch":
            payload["must_call"]["args"].update({"task_id": task_id, "context_token": context_token})
            return server.ok(payload)
        return result

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
        args={"agent_id": agent_id, "resource": safe, "base_hash": base_hash, "task_id": task_id, "context_token": context_token},
        reason=f"Ask the user for explicit permission before deletion. Required confirmation phrase: {confirmation}",
        forbidden=["direct_file_edit", "finish_task"],
        missing_inputs=["confirm_phrase", "reason"],
        context={"required_confirmation": confirmation, "claim": owned_write[0]},
    )




def list_tasks(agent_id: str = "", status: str = "") -> Dict[str, Any]:
    return server.ok({"verdict": "TASKS_LISTED", **task_context.list_tasks(agent_id=agent_id, status=status)})


def task_status(task_id: str) -> Dict[str, Any]:
    try:
        return server.ok({"verdict": "TASK_STATUS", "task": task_context.task_status(task_id)})
    except task_context.TaskContextError as exc:
        raise _context_error(exc) from exc


def wait_for_tasks(
    task_ids: List[str] | None = None,
    agent_id: str = "",
    timeout_seconds: int = 0,
    poll_interval_seconds: float = 0.5,
) -> Dict[str, Any]:
    timeout = max(0, min(int(timeout_seconds or 0), 300))
    interval = max(0.1, min(float(poll_interval_seconds or 0.5), 5.0))
    deadline = time.monotonic() + timeout
    wanted = set(task_ids or [])

    while True:
        tasks = task_context.list_tasks(agent_id=agent_id).get("tasks", [])
        if wanted:
            tasks = [task for task in tasks if task.get("task_id") in wanted]
        unfinished = [task for task in tasks if task.get("status") not in {"finished", "done", "failed", "cancelled"}]
        if not unfinished:
            return server.ok({"verdict": "TASKS_DONE", "tasks": tasks, "unfinished": [], "count": len(tasks), "timeout_seconds": timeout})
        if timeout == 0:
            return server.ok({"verdict": "TASKS_WAITING", "tasks": tasks, "unfinished": unfinished, "count": len(tasks), "timeout_seconds": timeout})
        if time.monotonic() >= deadline:
            return server.ok({"verdict": "WAIT_TIMEOUT", "tasks": tasks, "unfinished": unfinished, "count": len(tasks), "timeout_seconds": timeout})
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))

def _schema_props(base: Dict[str, Any], extra: Dict[str, str]) -> Dict[str, Any]:
    schema = json.loads(json.dumps(base))
    props = schema.setdefault("properties", {})
    for name, kind in extra.items():
        props[name] = {"type": kind}
    schema["additionalProperties"] = False
    return schema


def tool_schema(name: str) -> Dict[str, Any]:
    if name == "before_task":
        return _schema_props(_BASE_TOOL_SCHEMA(name), {"intent": "string", "resource": "string"})
    if name == "workflow_next":
        return _schema_props(_BASE_TOOL_SCHEMA(name), {"task_id": "string", "context_token": "string"})
    if name in {"scribe_query", "graphify_query"}:
        return _schema_props(_BASE_TOOL_SCHEMA(name), {"agent_id": "string", "task_id": "string", "context_token": "string"})
    if name == "propose_patch":
        return _schema_props(_BASE_TOOL_SCHEMA(name), {"task_id": "string", "context_token": "string"})
    if name == "finish_task":
        return _schema_props(_BASE_TOOL_SCHEMA(name), {"task_id": "string", "context_token": "string"})
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
    if name == "list_tasks":
        return {"type": "object", "properties": {"agent_id": {"type": "string"}, "status": {"type": "string"}}, "additionalProperties": False}
    if name == "task_status":
        return {"type": "object", "properties": {"task_id": {"type": "string"}}, "additionalProperties": False}
    if name == "wait_for_tasks":
        return {
            "type": "object",
            "properties": {
                "task_ids": {"type": "array", "items": {"type": "string"}},
                "agent_id": {"type": "string"},
                "timeout_seconds": {"type": "integer"},
                "poll_interval_seconds": {"type": "number"},
            },
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
                "task_id": {"type": "string"},
                "context_token": {"type": "string"},
            },
            "additionalProperties": False,
        }
    if name == "workflow_snapshot":
        return {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "task_id": {"type": "string"},
                "resource": {"type": "string"},
            },
            "additionalProperties": False,
        }
    if name == "batch_file_hash":
        return {
            "type": "object",
            "properties": {
                "resources": {"type": "array", "items": {"type": "string"}},
                "max_workers": {"type": "integer"},
            },
            "additionalProperties": False,
        }
    return _BASE_TOOL_SCHEMA(name)


def workflow_snapshot(
    agent_id: str = "",
    task_id: str = "",
    resource: str = "",
) -> Dict[str, Any]:
    if not agent_id:
        return server.ok({
            "verdict": "INPUT_REQUIRED",
            "reason": "agent_id is required for workflow_snapshot",
            "required_inputs": ["agent_id"],
        })
    result: Dict[str, Any] = {
        "verdict": "WORKFLOW_SNAPSHOT",
        "agent": None,
        "tasks": [],
        "task": None,
        "claims": [],
        "pending_patches": [],
        "resource": resource or "",
    }
    active_ids = server._active_agent_ids()
    if agent_id in active_ids:
        try:
            result["agent"] = server._agent_states().get(agent_id)
        except Exception:
            result["agent"] = None
    if task_id:
        try:
            tdata = task_context.task_status(task_id)
            result["task"] = tdata
        except Exception:
            result["task"] = None
    try:
        result["tasks"] = task_context.list_tasks(agent_id=agent_id).get("tasks", [])
    except Exception:
        result["tasks"] = []
    try:
        claims = server._active_claims_for(agent_id, resource)
        result["claims"] = claims.get("owned", []) + claims.get("foreign", [])
    except Exception:
        result["claims"] = []
    try:
        result["pending_patches"] = server._agent_pending_patches(agent_id, resource)
    except Exception:
        result["pending_patches"] = []
    return server.ok(result)


server.workflow_next = workflow_next
server.workflow_snapshot = workflow_snapshot
server.before_task = before_task
server.scribe_query = scribe_query
server.graphify_query = graphify_query
server.propose_patch = propose_patch
server.delete_resource = delete_resource
server.finish_task = finish_task
server.scribe_record = scribe_record
server.list_tasks = list_tasks
server.task_status = task_status
server.wait_for_tasks = wait_for_tasks
server.tool_schema = tool_schema
server.TOOLS["workflow_next"] = workflow_next
server.TOOLS["before_task"] = before_task
server.TOOLS["scribe_query"] = scribe_query
server.TOOLS["graphify_query"] = graphify_query
server.TOOLS["propose_patch"] = propose_patch
server.TOOLS["delete_resource"] = delete_resource
server.TOOLS["finish_task"] = finish_task
server.TOOLS["scribe_record"] = scribe_record
server.TOOLS["list_tasks"] = list_tasks
server.TOOLS["task_status"] = task_status
server.TOOLS["wait_for_tasks"] = wait_for_tasks
server.TOOLS["workflow_snapshot"] = workflow_snapshot

handle = server.handle
list_tools = server.list_tools
main = server.main

if __name__ == "__main__":
    raise SystemExit(server.main())
