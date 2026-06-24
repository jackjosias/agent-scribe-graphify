#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from typing import Any, Dict, List

import server  # type: ignore
from runtime import db, delete_ops, discipline, patch_queue, task_context  # type: ignore
from runtime.state_paths import prepare_state_dirs  # type: ignore
try:
    from runtime import portability  # type: ignore
except Exception:
    portability = None  # type: ignore
try:
    from runtime import graphify_scribe_bridge as _gsb  # type: ignore
except Exception:
    _gsb = None  # type: ignore

# Proof signer — non-circular TENOR proof verification (v0.2.15+)
try:
    import sys as _sys
    from pathlib import Path as _Path
    _SEL_SCRIPTS = _Path(__file__).parent.parent / "workflow" / "scribe" / "sel" / "scripts"
    if str(_SEL_SCRIPTS) not in _sys.path:
        _sys.path.insert(0, str(_SEL_SCRIPTS))
    from proof_signer import verify_proof as _verify_proof, purge_expired_proofs as _purge_expired_proofs  # type: ignore
    _PROOF_SIGNER_AVAILABLE = True
except Exception as _proof_import_exc:  # noqa: BLE001
    _PROOF_SIGNER_AVAILABLE = False
    _proof_import_exc_msg = str(_proof_import_exc)

server.SERVER_VERSION = "0.2.15"
if os.environ.get("AGENT_SCRIBE_GRAPHIFY_ROOT"):
    from pathlib import Path
    server.ROOT = Path(os.environ["AGENT_SCRIBE_GRAPHIFY_ROOT"]).resolve()
    server.AGENT_DIR = server.ROOT / ".agent"
_BASE_WORKFLOW_NEXT = server.workflow_next
_BASE_TOOL_SCHEMA = server.tool_schema
_BASE_BEFORE_TASK = server.before_task
_BASE_SCRIBE_QUERY = server.scribe_query
_BASE_GRAPHIFY_QUERY = server.graphify_query
_BASE_FINISH_TASK = server.finish_task
_BASE_CLAIM_RESOURCE = server.claim_resource
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
_LOOP_VERDICTS = {
    "TASK_NOT_ACKED",
    "ACTIVE_TASK_EXISTS",
    "AGENT_UNKNOWN_OR_UNREGISTERED",
    "TASK_CONTEXT_UNKNOWN_TASK",
    "TASK_CONTEXT_TOKEN_MISMATCH",
    "CLAIM_CONTEXT_NOT_READY",
    "BEFORE_TASK_REQUIRED",
}
_HARD_STOP_FORBIDDEN = ["bootstrap", "before_task", "claim_resource", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"]
_PRE_CONTEXT_FORBIDDEN = ["before_task", "scribe_query", "graphify_query", "claim_resource", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"]
_LEASE_FORBIDDEN = ["direct_file_edit"]
_STRICT_LEASE_TOOLS = {"propose_patch", "apply_patch", "delete_resource", "finish_task"}


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


def _require_action_lease(
    action_lease_id: str, tool_name: str, agent_id: str, action: str,
    task_id: str = "", resource: str = "", intent: str = "",
) -> dict[str, Any] | None:
    if not action_lease_id:
        return server.ok({
            "ok": True,
            "verdict": "ACTION_LEASE_REQUIRED",
            "state": "ACTION_LEASE_REQUIRED",
            "reason": f"{tool_name} requires a fresh action_lease from pre_action_guard.",
            "must_call": {
                "tool": "pre_action_guard",
                "args": {
                    "agent_id": agent_id,
                    "task_id": task_id,
                    "resource": resource or "",
                    "intent": intent or "",
                    "planned_action": action,
                },
            },
            "forbidden": _LEASE_FORBIDDEN,
        })
    try:
        discipline.validate_action_lease(
            action_lease_id, agent_id=agent_id, action=action,
            task_id=task_id, resource=resource, intent=intent,
        )
        discipline.consume_action_lease(
            action_lease_id, agent_id=agent_id, action=action,
            task_id=task_id, resource=resource, intent=intent,
        )
    except discipline.DisciplineError as exc:
        code = exc.code if exc.code in {"ACTION_LEASE_EXPIRED", "ACTION_LEASE_CONSUMED"} else "ACTION_LEASE_INVALID"
        return server.ok({
            "ok": True,
            "verdict": code,
            "state": code,
            "reason": exc.code,
            "details": exc.details,
            "forbidden": _LEASE_FORBIDDEN,
        })
    return None


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


def _stop_workflow_loop(retry: Dict[str, Any]) -> Dict[str, Any]:
    try:
        with db.connect(server.ROOT) as con:
            db.add_event(con, "workflow.loop_detected", {"retry": retry}, retry.get("agent_id"))
    except Exception:
        pass
    return server.ok({
        "verdict": "STOP_WORKFLOW_LOOP_DIRECT_WRITE_FORBIDDEN",
        "state": "WORKFLOW_LOOP_DETECTED",
        "reason": "Repeated MCP workflow loop detected. Stop and ask the user. Direct file edit is forbidden.",
        "retry": retry,
        "forbidden": _HARD_STOP_FORBIDDEN,
    })


def _track_loop(agent_id: str, resource: str, verdict: str) -> Dict[str, Any] | None:
    if verdict not in _LOOP_VERDICTS and not verdict.startswith("TASK_CONTEXT"):
        return None
    retry = task_context.record_retry(agent_id, resource, verdict, limit=3)
    if retry.get("verdict") == "MAX_WORKFLOW_RETRIES_EXCEEDED":
        return _stop_workflow_loop(retry)
    if retry.get("verdict") == "RETRY_LOOP_DETECTED":
        return server.ok({
            "verdict": "RETRY_LOOP_DETECTED",
            "state": "RETRY_LOOP_DETECTED",
            "reason": "Repeated MCP workflow warning detected. Continue only through the required MCP recovery tools; direct file edit is forbidden.",
            "retry": retry,
            "forbidden": ["bootstrap", "before_task", "claim_resource", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"],
        })
    return None


def _before_task_agent_block(agent_id: str, code: str) -> Dict[str, Any]:
    if code == "AGENT_ID_REQUIRED":
        return server.ok({
            "verdict": "AGENT_ID_REQUIRED",
            "state": "AGENT_ID_REQUIRED",
            "reason": "before_task requires a registered active agent_id. No task_context was created.",
            "must_call": {"tool": "register_agent", "args": {"host_tool": "unknown", "model_name": ""}},
            "forbidden": _PRE_CONTEXT_FORBIDDEN,
        })
    if code == "AGENT_UNKNOWN_OR_UNREGISTERED":
        return server.ok({
            "verdict": "AGENT_UNKNOWN_OR_UNREGISTERED",
            "state": "AGENT_UNKNOWN_OR_UNREGISTERED",
            "reason": "before_task cannot create a task_context for an unknown agent.",
            "must_call": {"tool": "register_agent", "args": {"agent_id": agent_id, "host_tool": "unknown", "model_name": ""}},
            "forbidden": _PRE_CONTEXT_FORBIDDEN,
        })
    if code == "AGENT_IDLE_RESUME_REQUIRED":
        return server.ok({
            "verdict": "AGENT_IDLE_RESUME_REQUIRED",
            "state": "AGENT_IDLE_RESUME_REQUIRED",
            "reason": "before_task requires an active agent. Resume the existing identity; do not create a replacement.",
            "must_call": {"tool": "resume_agent", "args": {"agent_id": agent_id}},
            "forbidden": ["before_task", "claim_resource", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"],
        })
    return server.ok({
        "verdict": code,
        "state": code,
        "reason": "before_task requires a registered active agent. No task_context was created.",
        "forbidden": _PRE_CONTEXT_FORBIDDEN,
    })


def before_task(request: str, agent_id: str = "", intent: str = "", resource: str = "") -> Dict[str, Any]:
    try:
        db.require_agent_active(agent_id)
    except db.CoordinationError as exc:
        code = str(exc)
        event_type = "agent.required" if code == "AGENT_ID_REQUIRED" else "agent.unknown_refused_before_task"
        try:
            with db.connect(server.ROOT) as con:
                db.add_event(con, event_type, {"reason": code, "request": request, "intent": intent, "resource": resource}, agent_id or None)
        except Exception:
            pass
        return _before_task_agent_block(agent_id, code)

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
        if exc.code == "ACTIVE_TASK_EXISTS":
            task_id = exc.details.get("task_id") or ""
            return server.ok({
                "verdict": "ACTIVE_TASK_EXISTS",
                "state": "ACTIVE_TASK_EXISTS",
                "reason": "An active task already exists for this agent/resource/intent. Do not create a new task.",
                "agent_id": agent_id,
                "resource": resource or "",
                "must_call": {"tool": "resume_task_context", "args": {"agent_id": agent_id, "task_id": task_id}},
                "forbidden": ["before_task", "claim_resource", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"],
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


def resume_task_context(agent_id: str, task_id: str) -> Dict[str, Any]:
    try:
        context = task_context.resume_task_context(agent_id=agent_id, task_id=task_id)
    except task_context.TaskContextError as exc:
        raise _context_error(exc) from exc
    return server.ok({"verdict": "TASK_CONTEXT_RESUMED", **context})


def _claim_context_not_ready(agent_id: str, resource: str, reason: str, missing: List[str] | None = None) -> Dict[str, Any]:
    try:
        with db.connect(server.ROOT) as con:
            db.add_event(con, "claim.context_not_ready", {"resource": resource, "reason": reason, "missing_inputs": missing or []}, agent_id or None)
    except Exception:
        pass
    return server.ok({
        "verdict": "CLAIM_CONTEXT_NOT_READY",
        "state": "CLAIM_CONTEXT_NOT_READY",
        "reason": reason,
        "missing_inputs": missing or [],
        "forbidden": ["claim_resource", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"],
    })


def claim_resource(agent_id: str, resource: str, mode: str = "write", ttl_seconds: int = 1800, task_id: str = "", context_token: str = "", action_lease_id: str = "") -> Dict[str, Any]:
    try:
        db.require_agent_active(agent_id)
    except db.CoordinationError as exc:
        return _claim_context_not_ready(agent_id, resource, str(exc), ["agent_id"])
    requested_mode = "patch_queue" if mode in server.WRITE_MODES else mode
    if requested_mode in server.WRITE_MODES:
        missing = [name for name, value in {"task_id": task_id, "context_token": context_token}.items() if not value]
        if missing:
            return _claim_context_not_ready(agent_id, resource, "claim_resource write/patch_queue/exclusive requires a ready task_id/context_token.", missing)
        try:
            task_context.require_context_ready(
                agent_id,
                task_id,
                context_token,
                resource=resource,
                strict_resource=True,
                allowed_intents=_MUTATING_CONTEXT_INTENTS,
            )
        except task_context.TaskContextError as exc:
            return _claim_context_not_ready(agent_id, resource, str(exc), ["task_id", "context_token"])
        lease_check = _require_action_lease(
            action_lease_id, "claim_resource", agent_id, "claim_resource",
            task_id=task_id, resource=resource, intent="write",
        )
        if lease_check is not None:
            return lease_check
    return _BASE_CLAIM_RESOURCE(agent_id=agent_id, resource=resource, mode=mode, ttl_seconds=ttl_seconds)


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


def propose_patch(agent_id: str, target: str, base_hash: str, diff_text: str, task_id: str = "", context_token: str = "", action_lease_id: str = "") -> Dict[str, Any]:
    _require_context_ready(
        agent_id,
        task_id,
        context_token,
        target,
        strict_resource=True,
        allowed_intents=_MUTATING_CONTEXT_INTENTS,
    )
    lease_check = _require_action_lease(
        action_lease_id, "propose_patch", agent_id, "propose_patch",
        task_id=task_id, resource=target,
    )
    if lease_check is not None:
        return lease_check
    return server.ok(patch_queue.propose_patch(agent_id=agent_id, target=target, base_hash=base_hash, diff_text=diff_text))


def apply_patch(agent_id: str, patch_id: str, task_id: str = "", context_token: str = "", action_lease_id: str = "") -> Dict[str, Any]:
    _require_context_ready(
        agent_id, task_id, context_token, "",
        strict_resource=False,
        allowed_intents=_MUTATING_CONTEXT_INTENTS,
    )
    lease_check = _require_action_lease(
        action_lease_id, "apply_patch", agent_id, "apply_patch",
        task_id=task_id,
    )
    if lease_check is not None:
        return lease_check
    return server.ok(patch_queue.apply_patch(agent_id=agent_id, patch_id=patch_id))


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
    _require_context_ready(
        agent_id,
        task_id,
        context_token,
        resource,
        strict_resource=True,
        allowed_intents=_MUTATING_CONTEXT_INTENTS,
    )
    lease_check = _require_action_lease(
        action_lease_id, "delete_resource", agent_id, "delete_resource",
        task_id=task_id, resource=resource,
    )
    if lease_check is not None:
        return lease_check
    _require_context_ready(
        agent_id,
        task_id,
        context_token,
        resource,
        strict_resource=True,
        allowed_intents=_MUTATING_CONTEXT_INTENTS,
    )
    return server.ok(delete_ops.delete_resource(agent_id=agent_id, resource=resource, base_hash=base_hash, confirm_phrase=confirm_phrase, reason=reason))


def finish_task(agent_id: str, summary: str = "", task_id: str = "", context_token: str = "", action_lease_id: str = "") -> Dict[str, Any]:
    lease_check = _require_action_lease(
        action_lease_id, "finish_task", agent_id, "finish_task",
        task_id=task_id,
    )
    if lease_check is not None:
        return lease_check
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


def discipline_ping(
    agent_id: str = "",
    phase: str = "",
    resource: str = "",
) -> Dict[str, Any]:
    if not agent_id:
        return server.ok({
            "verdict": "AGENT_ID_REQUIRED",
            "state": "AGENT_ID_REQUIRED",
            "reason": "discipline_ping requires agent_id.",
            "forbidden": _LEASE_FORBIDDEN,
        })
    try:
        result = discipline.record_guard_ping(agent_id, phase=phase, resource=resource)
    except db.CoordinationError as exc:
        return server.ok({
            "verdict": str(exc),
            "state": str(exc),
            "reason": str(exc),
            "forbidden": _LEASE_FORBIDDEN,
        })
    return server.ok({
        "verdict": "DISCIPLINE_PING_OK",
        "state": "DISCIPLINE_PING_OK",
        "message": "Before any code write/fix/refactor/test, call pre_action_guard.",
        **result,
        "forbidden": ["direct_file_edit_without_mcp"],
    })


def pre_action_guard(
    agent_id: str = "",
    request: str = "",
    intent: str = "",
    resource: str = "",
    task_id: str = "",
    context_token: str = "",
    planned_action: str = "",
) -> Dict[str, Any]:
    if not agent_id:
        return server.ok({
            "verdict": "AGENT_ID_REQUIRED",
            "state": "AGENT_ID_REQUIRED",
            "reason": "pre_action_guard requires agent_id.",
            "forbidden": _LEASE_FORBIDDEN,
        })
    if not planned_action:
        return server.ok({
            "verdict": "PLANNED_ACTION_REQUIRED",
            "state": "PLANNED_ACTION_REQUIRED",
            "reason": "pre_action_guard requires a planned_action.",
            "forbidden": _LEASE_FORBIDDEN,
        })

    safe_resource = patch_queue.safe_resource(resource) if resource else ""

    try:
        agent = db.require_agent_active(agent_id)
    except db.CoordinationError as exc:
        code = str(exc)
        if code == "AGENT_ID_REQUIRED":
            return server.ok({
                "verdict": "AGENT_ID_REQUIRED",
                "state": "AGENT_ID_REQUIRED",
                "must_call": {"tool": "register_agent", "args": {"host_tool": "unknown"}},
                "forbidden": _LEASE_FORBIDDEN,
            })
        if code == "AGENT_UNKNOWN_OR_UNREGISTERED":
            return server.ok({
                "verdict": "AGENT_UNKNOWN_OR_UNREGISTERED",
                "state": "AGENT_UNKNOWN_OR_UNREGISTERED",
                "must_call": {"tool": "register_agent", "args": {"agent_id": agent_id, "host_tool": "unknown"}},
                "forbidden": _LEASE_FORBIDDEN,
            })
        if code == "AGENT_IDLE_RESUME_REQUIRED":
            return server.ok({
                "verdict": "AGENT_IDLE_RESUME_REQUIRED",
                "state": "AGENT_IDLE_RESUME_REQUIRED",
                "must_call": {"tool": "resume_agent", "args": {"agent_id": agent_id}},
                "forbidden": _LEASE_FORBIDDEN,
            })
        return server.ok({
            "verdict": code,
            "state": code,
            "forbidden": _LEASE_FORBIDDEN,
        })

    if task_id:
        try:
            tdata = task_context.task_status(task_id)
            if tdata.get("status") == "active" and not context_token:
                return server.ok({
                    "verdict": "NEXT_ACTION_REQUIRED",
                    "state": "TASK_CONTEXT_TOKEN_REQUIRED",
                    "must_call": {"tool": "resume_task_context", "args": {"agent_id": agent_id, "task_id": task_id}},
                    "forbidden": _LEASE_FORBIDDEN,
                })
        except task_context.TaskContextError:
            return server.ok({
                "verdict": "NEXT_ACTION_REQUIRED",
                "state": "TASK_CONTEXT_UNKNOWN_TASK",
                "must_call": {"tool": "before_task", "args": {"agent_id": agent_id, "request": request or "", "intent": intent or "", "resource": safe_resource}},
                "forbidden": _LEASE_FORBIDDEN,
            })

    normalized = (intent or "").strip().lower()
    action_aliases = {"edit": "propose_patch", "write": "propose_patch", "delete": "delete_resource", "finish": "finish_task"}
    normalized_action = action_aliases.get(planned_action, planned_action)
    needs_context = normalized_action in {"claim_resource", "propose_patch", "apply_patch", "delete_resource", "finish_task"}
    write_like_intent = normalized in discipline.WRITE_INTENTS or needs_context

    if needs_context and write_like_intent:
        if not task_id:
            return server.ok({
                "verdict": "NEXT_ACTION_REQUIRED",
                "state": "BEFORE_TASK_REQUIRED",
                "must_call": {"tool": "before_task", "args": {"agent_id": agent_id, "request": request or "", "intent": intent or "", "resource": safe_resource}},
                "forbidden": _LEASE_FORBIDDEN,
            })
        try:
            ctx = task_context.require_context_ready(
                agent_id, task_id, context_token,
                resource=safe_resource,
                strict_resource=bool(safe_resource),
                allowed_intents=discipline.WRITE_INTENTS,
            )
        except task_context.TaskContextError as exc:
            code = exc.code if hasattr(exc, "code") else str(exc)
            if code in {"TASK_CONTEXT_UNKNOWN_TASK", "TASK_CONTEXT_TOKEN_MISMATCH"}:
                return server.ok({
                    "verdict": "NEXT_ACTION_REQUIRED",
                    "state": code,
                    "must_call": {"tool": "resume_task_context", "args": {"agent_id": agent_id, "task_id": task_id}},
                    "forbidden": _LEASE_FORBIDDEN,
                })
            if code == "ACTIVE_TASK_EXISTS":
                ed = exc.details if hasattr(exc, "details") else {}
                return server.ok({
                    "verdict": "ACTIVE_TASK_EXISTS",
                    "state": "ACTIVE_TASK_EXISTS",
                    "must_call": {"tool": "resume_task_context", "args": {"agent_id": agent_id, "task_id": ed.get("task_id", task_id)}},
                    "forbidden": _LEASE_FORBIDDEN,
                })
            if "scribe_query is required" in code:
                return server.ok({
                    "verdict": "NEXT_ACTION_REQUIRED",
                    "state": "SCRIBE_CONTEXT_REQUIRED",
                    "must_call": {"tool": "scribe_query", "args": {"agent_id": agent_id, "task_id": task_id, "context_token": context_token, "query": _targeted_scribe_query(request or "", intent or "", safe_resource), "limit": 5}},
                    "forbidden": _LEASE_FORBIDDEN,
                })
            if "graphify_query is required" in code:
                return server.ok({
                    "verdict": "NEXT_ACTION_REQUIRED",
                    "state": "GRAPHIFY_CONTEXT_REQUIRED",
                    "must_call": {"tool": "graphify_query", "args": {"agent_id": agent_id, "task_id": task_id, "context_token": context_token, "query": request or "current task", "resource": safe_resource}},
                    "forbidden": _LEASE_FORBIDDEN,
                })
            return server.ok({
                "verdict": "NEXT_ACTION_REQUIRED",
                "state": code,
                "must_call": {"tool": "resume_task_context", "args": {"agent_id": agent_id, "task_id": task_id}},
                "forbidden": _LEASE_FORBIDDEN,
            })

        if not ctx.get("scribe_done"):
            return server.ok({
                "verdict": "NEXT_ACTION_REQUIRED",
                "state": "SCRIBE_CONTEXT_REQUIRED",
                "must_call": {"tool": "scribe_query", "args": {"agent_id": agent_id, "task_id": task_id, "context_token": context_token, "query": _targeted_scribe_query(request or "", intent or "", safe_resource), "limit": 5}},
                "forbidden": _LEASE_FORBIDDEN,
            })
        if ctx.get("requires_graphify") and not ctx.get("graphify_done"):
            return server.ok({
                "verdict": "NEXT_ACTION_REQUIRED",
                "state": "GRAPHIFY_CONTEXT_REQUIRED",
                "must_call": {"tool": "graphify_query", "args": {"agent_id": agent_id, "task_id": task_id, "context_token": context_token, "query": request or "current task", "resource": safe_resource}},
                "forbidden": _LEASE_FORBIDDEN,
            })

    action = normalized_action

    if action in _STRICT_LEASE_TOOLS or action == "claim_resource":
        try:
            lease = discipline.issue_action_lease(
                agent_id=agent_id, action=action, task_id=task_id or "",
                resource=safe_resource, intent="write" if action in discipline.MUTATING_ACTIONS else (normalized or ""),
            )
        except discipline.DisciplineError as exc:
            return server.ok({
                "verdict": "ACTION_LEASE_FAILED",
                "state": "ACTION_LEASE_FAILED",
                "reason": str(exc),
                "forbidden": _LEASE_FORBIDDEN,
            })
        return server.ok({
            "verdict": "PRE_ACTION_GUARD_OK",
            "state": "ACTION_LEASE_ISSUED",
            "action_lease": lease,
            "forbidden": _LEASE_FORBIDDEN,
        })

    return server.ok({
        "verdict": "PRE_ACTION_GUARD_OK",
        "state": "READ_ONLY_NO_LEASE",
        "forbidden": _LEASE_FORBIDDEN,
    })


def workspace_audit(
    agent_id: str = "",
    task_id: str = "",
    resource: str = "",
) -> Dict[str, Any]:
    try:
        result = discipline.detect_direct_write_bypass(
            agent_id=agent_id, task_id=task_id, resource=resource,
        )
    except db.CoordinationError as exc:
        return server.ok({
            "verdict": str(exc),
            "state": str(exc),
            "forbidden": _LEASE_FORBIDDEN,
        })
    return server.ok(result)


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

    stop = _track_loop(agent_id, resource, last)
    if stop is not None:
        return stop

    task_data: Dict[str, Any] | None = None
    if task_id:
        try:
            task_data = task_context.task_status(task_id)
        except task_context.TaskContextError as exc:
            stop = _track_loop(agent_id, resource, exc.code)
            if stop is not None:
                return stop
            # Unknown task: clear task_id so base workflow_next handles it
            task_id = ""
            task_data = None
        if task_data and task_data.get("agent_id") != agent_id:
            raise server.ToolError("TASK_CONTEXT_AGENT_MISMATCH")
        if task_data and task_data.get("status") == "active" and not context_token:
            return server.ok({
                "verdict": "NEXT_ACTION_REQUIRED",
                "state": "TASK_CONTEXT_TOKEN_REQUIRED",
                "must_call": {"tool": "resume_task_context", "args": {"agent_id": agent_id, "task_id": task_id}},
                "reason": "An active task exists and its context_token is required. Do not call before_task again.",
                "forbidden": ["before_task", "direct_file_edit"],
            })
        if task_data and task_data.get("status") == "active" and request and last not in _CONTEXT_VERDICTS:
            if not task_data.get("scribe_done"):
                last_verdict = "BEFORE_TASK_OK"
            elif task_data.get("requires_graphify") and not task_data.get("graphify_done"):
                last_verdict = "SCRIBE_QUERY_DONE"
            else:
                last_verdict = "GRAPHIFY_QUERY_DONE" if task_data.get("requires_graphify") else "SCRIBE_QUERY_DONE"
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
        must_tool = (payload.get("must_call") or {}).get("tool")
        if must_tool in {"claim_resource", "propose_patch"}:
            payload["must_call"]["args"].update({"task_id": task_id, "context_token": context_token})
            return server.ok(payload)
        if must_tool == "before_task" and task_id:
            return server.ok({
                "verdict": "NEXT_ACTION_REQUIRED",
                "state": "TASK_CONTEXT_TOKEN_REQUIRED" if not context_token else "TASK_CONTEXT_CONTINUE_REQUIRED",
                "must_call": {"tool": "resume_task_context", "args": {"agent_id": agent_id, "task_id": task_id}},
                "reason": "workflow_next refused to re-enter before_task while an active task_id is present.",
                "forbidden": ["before_task", "direct_file_edit"],
            })
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
            args={"agent_id": agent_id, "resource": safe, "mode": "patch_queue", "ttl_seconds": 600, "task_id": task_id, "context_token": context_token},
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
    if name == "resume_task_context":
        return {"type": "object", "properties": {"agent_id": {"type": "string"}, "task_id": {"type": "string"}}, "additionalProperties": False}
    if name in {"scribe_query", "graphify_query"}:
        return _schema_props(_BASE_TOOL_SCHEMA(name), {"agent_id": "string", "task_id": "string", "context_token": "string"})
    if name == "claim_resource":
        return _schema_props(_BASE_TOOL_SCHEMA(name), {"task_id": "string", "context_token": "string", "action_lease_id": "string"})
    if name == "propose_patch":
        return _schema_props(_BASE_TOOL_SCHEMA(name), {"task_id": "string", "context_token": "string", "action_lease_id": "string"})
    if name == "apply_patch":
        return _schema_props(_BASE_TOOL_SCHEMA(name), {"task_id": "string", "context_token": "string", "action_lease_id": "string"})
    if name == "delete_resource":
        return _schema_props({
            "type": "object", "properties": {
                "agent_id": {"type": "string"}, "resource": {"type": "string"},
                "base_hash": {"type": "string"}, "confirm_phrase": {"type": "string"},
                "reason": {"type": "string"}, "task_id": {"type": "string"},
                "context_token": {"type": "string"}, "action_lease_id": {"type": "string"},
            }, "additionalProperties": False,
        }, {})
    if name == "finish_task":
        return _schema_props(_BASE_TOOL_SCHEMA(name), {"task_id": "string", "context_token": "string", "action_lease_id": "string"})
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
    if name == "discipline_ping":
        return {"type": "object", "properties": {"agent_id": {"type": "string"}, "phase": {"type": "string"}, "resource": {"type": "string"}}, "additionalProperties": False}
    if name == "pre_action_guard":
        return {"type": "object", "properties": {"agent_id": {"type": "string"}, "request": {"type": "string"}, "intent": {"type": "string"}, "resource": {"type": "string"}, "task_id": {"type": "string"}, "context_token": {"type": "string"}, "planned_action": {"type": "string"}}, "additionalProperties": False}
    if name == "workspace_audit":
        return {"type": "object", "properties": {"agent_id": {"type": "string"}, "task_id": {"type": "string"}, "resource": {"type": "string"}}, "additionalProperties": False}
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
    if name == "lease_extend":
        return {
            "type": "object",
            "properties": {
                "lease_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "extend_seconds": {"type": "integer"},
            },
            "required": ["lease_id", "agent_id"],
            "additionalProperties": False,
        }
    if name == "resource_lock_claim":
        return {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "resource": {"type": "string"},
                "task_id": {"type": "string"},
                "ttl_seconds": {"type": "integer"},
            },
            "required": ["agent_id", "resource"],
            "additionalProperties": False,
        }
    if name == "resource_lock_release":
        return {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "resource": {"type": "string"},
                "lock_id": {"type": "string"},
            },
            "required": ["agent_id", "resource"],
            "additionalProperties": False,
        }
    if name == "resource_lock_status":
        return {
            "type": "object",
            "properties": {"resource": {"type": "string"}},
            "required": ["resource"],
            "additionalProperties": False,
        }
    if name == "portability_check":
        return {
            "type": "object",
            "properties": {"workspace_root": {"type": "string"}},
            "additionalProperties": False,
        }
    if name == "graphify_scribe_bridge":
        return {
            "type": "object",
            "properties": {
                "workspace_root": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "drift_threshold": {"type": "number"},
            },
            "additionalProperties": False,
        }
    if name == "verify_proof":
        return {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "agent_id": {"type": "string"},
                "workspace_root": {"type": "string"},
            },
            "required": ["token", "agent_id"],
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


# ─────────────────────────────────────────────────────────────
# New V2.14 tools: lease_extend, resource_lock_*, portability_check,
# graphify_scribe_bridge
# ─────────────────────────────────────────────────────────────

def lease_extend(
    lease_id: str = "",
    agent_id: str = "",
    extend_seconds: int | None = None,
) -> Dict[str, Any]:
    """Extend an active action lease TTL without losing FSM context.

    Use this BEFORE your lease expires when an operation takes longer than
    expected (high-latency network, large file write, slow tool call).
    Prevents ACTION_LEASE_EXPIRED blocking you mid-operation.

    Limits: max 10 extensions, max 3600s cumulative from original issue.
    This anti-loop guard means lease_extend cannot be used to hold a lease
    forever — you must finish the task.
    """
    if not lease_id:
        return server.ok({
            "verdict": "LEASE_ID_REQUIRED",
            "state": "LEASE_ID_REQUIRED",
            "reason": "lease_extend requires lease_id from a previously issued action lease.",
            "forbidden": _LEASE_FORBIDDEN,
        })
    if not agent_id:
        return server.ok({
            "verdict": "AGENT_ID_REQUIRED",
            "state": "AGENT_ID_REQUIRED",
            "reason": "lease_extend requires agent_id.",
            "forbidden": _LEASE_FORBIDDEN,
        })
    try:
        result = discipline.extend_action_lease(
            lease_id=lease_id,
            agent_id=agent_id,
            extend_seconds=extend_seconds,
        )
    except discipline.DisciplineError as exc:
        code = exc.code
        hints: Dict[str, Any] = {
            "ACTION_LEASE_EXPIRED": "The lease already expired before you could extend it. Call pre_action_guard again.",
            "ACTION_LEASE_CONSUMED": "The lease was already consumed. Call pre_action_guard for a new one.",
            "ACTION_LEASE_EXTEND_LIMIT": "Maximum extensions reached. Finish the task or restart with workflow_next.",
            "ACTION_LEASE_EXTEND_CEILING_REACHED": "Maximum cumulative TTL reached. Finish the task.",
        }
        return server.ok({
            "verdict": code,
            "state": code,
            "reason": hints.get(code, exc.code),
            "details": exc.details,
            "must_call": {
                "tool": "pre_action_guard",
                "args": {"agent_id": agent_id, "planned_action": "propose_patch"},
            } if code in {"ACTION_LEASE_EXPIRED", "ACTION_LEASE_CONSUMED"} else None,
            "forbidden": _LEASE_FORBIDDEN,
        })
    except db.CoordinationError as exc:
        return server.ok({"verdict": str(exc), "state": str(exc), "forbidden": _LEASE_FORBIDDEN})
    return server.ok(result)


def resource_lock_claim(
    agent_id: str = "",
    resource: str = "",
    task_id: str = "",
    ttl_seconds: int | None = None,
) -> Dict[str, Any]:
    """Claim an exclusive write lock on a resource.

    Prevents N agents (including orchestrator sub-agents) from writing
    to the same resource concurrently. Use before long multi-file operations.

    Returns lock_id on success. Raises RESOURCE_ALREADY_LOCKED with owner
    details if another agent holds the lock. Same agent calling again is
    idempotent (returns existing lock).
    """
    if not agent_id:
        return server.ok({"verdict": "AGENT_ID_REQUIRED", "state": "AGENT_ID_REQUIRED", "forbidden": _LEASE_FORBIDDEN})
    if not resource:
        return server.ok({"verdict": "RESOURCE_REQUIRED", "state": "RESOURCE_REQUIRED",
                          "reason": "resource_lock_claim requires a resource path.", "forbidden": _LEASE_FORBIDDEN})
    try:
        result = discipline.resource_lock_claim(
            agent_id=agent_id, resource=resource, task_id=task_id or "",
            ttl_seconds=ttl_seconds,
        )
    except discipline.DisciplineError as exc:
        return server.ok({
            "verdict": exc.code,
            "state": exc.code,
            "reason": f"Resource is locked by another agent. Details: {exc.details}",
            "details": exc.details,
            "next_step": "Wait for the lock to expire or ask the owning agent to call resource_lock_release.",
            "forbidden": _LEASE_FORBIDDEN,
        })
    except db.CoordinationError as exc:
        return server.ok({"verdict": str(exc), "state": str(exc), "forbidden": _LEASE_FORBIDDEN})
    return server.ok(result)


def resource_lock_release(
    agent_id: str = "",
    resource: str = "",
    lock_id: str = "",
) -> Dict[str, Any]:
    """Release an active resource lock held by this agent.

    Only the owning agent can release its own lock.
    Releasing an already-released lock is idempotent (no error).
    """
    if not agent_id:
        return server.ok({"verdict": "AGENT_ID_REQUIRED", "state": "AGENT_ID_REQUIRED", "forbidden": _LEASE_FORBIDDEN})
    if not resource:
        return server.ok({"verdict": "RESOURCE_REQUIRED", "state": "RESOURCE_REQUIRED", "forbidden": _LEASE_FORBIDDEN})
    try:
        result = discipline.resource_lock_release(agent_id=agent_id, resource=resource, lock_id=lock_id or "")
    except discipline.DisciplineError as exc:
        return server.ok({"verdict": exc.code, "state": exc.code, "details": exc.details, "forbidden": _LEASE_FORBIDDEN})
    except db.CoordinationError as exc:
        return server.ok({"verdict": str(exc), "state": str(exc), "forbidden": _LEASE_FORBIDDEN})
    return server.ok(result)


def resource_lock_status(resource: str = "") -> Dict[str, Any]:
    """Check who holds the lock on a resource.

    Safe to call from any agent. Does not mutate state.
    Use before long operations to confirm the resource is free.
    """
    if not resource:
        return server.ok({"verdict": "RESOURCE_REQUIRED", "state": "RESOURCE_REQUIRED", "forbidden": _LEASE_FORBIDDEN})
    try:
        result = discipline.resource_lock_status(resource=resource)
    except discipline.DisciplineError as exc:
        return server.ok({"verdict": exc.code, "state": exc.code, "forbidden": _LEASE_FORBIDDEN})
    return server.ok(result)


def portability_check(workspace_root: str = "") -> Dict[str, Any]:
    """Validate that .agent is correctly placed at the project root.

    Checks: .agent/ exists, server_entry.py present, git root matches.
    Returns a simple OK/FAIL verdict with corrective_action if needed.

    Use at session start to confirm the .agent bundle is properly installed
    before running any workflow. Small LLMs: if verdict != ROOT_VALID, STOP
    and show the corrective_action to the user.
    """
    if portability is None:
        return server.ok({
            "verdict": "PORTABILITY_MODULE_UNAVAILABLE",
            "ok": False,
            "reason": "portability.py module not loaded. Check .agent/mcp/runtime/ contents.",
        })
    root = workspace_root.strip() if workspace_root else ""
    try:
        result = portability.portability_check(workspace_root=root if root else None)
    except Exception as exc:
        return server.ok({"verdict": "PORTABILITY_CHECK_ERROR", "ok": False, "error": str(exc)})
    return server.ok(result)


def graphify_scribe_bridge(
    workspace_root: str = "",
    dry_run: bool = False,
    drift_threshold: float = 0.30,
) -> Dict[str, Any]:
    """Check for Graphify centrality drift on SCAR-tagged nodes and write GHOSTs.

    Reads graphify-out/graph.json and SCRIBE file. For each function/class
    referenced in a SCAR, computes current centrality. If drift > threshold
    (default 30%), appends a GHOST entry to SCRIBE.

    Idempotent: same drift event is only written once (deterministic drift_id).
    dry_run=True detects drifts without writing anything.
    """
    if _gsb is None:
        return server.ok({
            "verdict": "BRIDGE_MODULE_UNAVAILABLE",
            "ok": False,
            "reason": "graphify_scribe_bridge.py module not loaded.",
        })
    root = workspace_root.strip() if workspace_root else ""
    try:
        result = _gsb.run_bridge_check(
            workspace_root=root if root else None,
            drift_threshold=max(0.01, min(1.0, float(drift_threshold))),
            dry_run=bool(dry_run),
        )
    except Exception as exc:
        return server.ok({"verdict": "BRIDGE_ERROR", "ok": False, "error": str(exc)})
    return server.ok(result)


# ─────────────────────────────────────────────────────────────
# verify_proof — non-circular TENOR proof verification (V2.15)
# ─────────────────────────────────────────────────────────────

def verify_proof(
    token: str = "",
    agent_id: str = "",
    workspace_root: str = "",
) -> Dict[str, Any]:
    """Verify a TENOR proof token issued during tenor-init.

    This tool breaks the circular trust loop in TENOR INIT:
    the proof token was signed server-side by HMAC-SHA256 with a project-bound
    key during `scribe tenor-init`. The LLM cannot fabricate a valid token
    without having actually run the command.

    Args:
        token          : the proof token from the SCRIBE-CHECK V4 output line
                         «Proof token».
        agent_id       : the agent_id from the same SCRIBE-CHECK (must match token).
        workspace_root : optional; defaults to the server's current project root.

    Returns JSON with:
        ok       : bool
        verdict  : one of the values below
        detail   : human-readable explanation

    Verdict codes:
        PROOF_VALID              — authentic, project-bound, within TTL.
        PROOF_INVALID_FORMAT     — token malformed (not issued by this server).
        PROOF_INVALID_SIGNATURE  — HMAC mismatch (fabricated or tampered).
        PROOF_INVALID_NOT_IN_STORE — nonce absent (token was never issued).
        PROOF_INVALID_AGENT_MISMATCH — agent_id does not match token.
        PROOF_EXPIRED            — TTL exceeded (>24 h since tenor-init).
        PROOF_CONSUMED           — already verified once; replay detected.
        PROOF_MODULE_UNAVAILABLE — proof_signer.py failed to load at startup.

    Usage after TENOR INIT:
        verify_proof(token="v1.<...>", agent_id="<agent_session_id>")
    """
    if not _PROOF_SIGNER_AVAILABLE:
        return server.ok({
            "ok": False,
            "verdict": "PROOF_MODULE_UNAVAILABLE",
            "detail": (
                f"proof_signer.py could not be loaded: {_proof_import_exc_msg}"
                if "_proof_import_exc_msg" in dir()
                else "proof_signer.py not available."
            ),
        })
    if not token or not agent_id:
        return server.ok({
            "ok": False,
            "verdict": "PROOF_INVALID_MISSING_INPUT",
            "detail": "Both `token` and `agent_id` are required.",
        })
    from pathlib import Path as _LocalPath
    root_str = workspace_root.strip() if workspace_root else ""
    try:
        root = _LocalPath(root_str).resolve() if root_str else server.ROOT.resolve()
    except Exception as exc:  # noqa: BLE001
        return server.ok({
            "ok": False,
            "verdict": "PROOF_INVALID_ROOT",
            "detail": f"Cannot resolve workspace_root: {exc}",
        })
    # Best-effort housekeeping — never blocks the verify call
    try:
        _purge_expired_proofs(root)
    except Exception:  # noqa: BLE001
        pass
    try:
        result = _verify_proof(root, token, agent_id)
    except Exception as exc:  # noqa: BLE001
        return server.ok({"ok": False, "verdict": "PROOF_INTERNAL_ERROR", "detail": str(exc)})
    return server.ok(result)


# ─────────────────────────────────────────────────────────────
# Wire all tools into the server
# ─────────────────────────────────────────────────────────────

server.workflow_next = workflow_next
server.workflow_snapshot = workflow_snapshot
server.before_task = before_task
server.resume_task_context = resume_task_context
server.claim_resource = claim_resource
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
server.TOOLS["workflow_snapshot"] = workflow_snapshot
server.TOOLS["before_task"] = before_task
server.TOOLS["resume_task_context"] = resume_task_context
server.TOOLS["claim_resource"] = claim_resource
server.TOOLS["scribe_query"] = scribe_query
server.TOOLS["graphify_query"] = graphify_query
server.TOOLS["propose_patch"] = propose_patch
server.TOOLS["apply_patch"] = apply_patch
server.TOOLS["delete_resource"] = delete_resource
server.TOOLS["finish_task"] = finish_task
server.TOOLS["scribe_record"] = scribe_record
server.TOOLS["list_tasks"] = list_tasks
server.TOOLS["task_status"] = task_status
server.TOOLS["wait_for_tasks"] = wait_for_tasks
server.TOOLS["discipline_ping"] = discipline_ping
server.TOOLS["pre_action_guard"] = pre_action_guard
server.TOOLS["workspace_audit"] = workspace_audit
# V2.14 new tools
server.TOOLS["lease_extend"] = lease_extend
server.TOOLS["resource_lock_claim"] = resource_lock_claim
server.TOOLS["resource_lock_release"] = resource_lock_release
server.TOOLS["resource_lock_status"] = resource_lock_status
server.TOOLS["portability_check"] = portability_check
server.TOOLS["graphify_scribe_bridge"] = graphify_scribe_bridge
# V2.15 new tools
server.TOOLS["verify_proof"] = verify_proof

handle = server.handle
list_tools = server.list_tools
main = server.main

if __name__ == "__main__":
    raise SystemExit(server.main())
