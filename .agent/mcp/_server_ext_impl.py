#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

import server  # type: ignore
from runtime import db, delete_ops, discipline, patch_queue, root_hygiene, runtime_backup_retention, scribe_commit_gate, task_context, direct_fs_tripwire, canonical_memory_gate  # type: ignore
from runtime.resource_locks import preflight_apply_patch as _preflight_lock  # type: ignore
from runtime.state_paths import prepare_state_dirs  # type: ignore
try:
    from runtime import portability  # type: ignore
except Exception:
    portability = None  # type: ignore
try:
    from runtime import graphify_scribe_bridge as _gsb  # type: ignore
except Exception:
    _gsb = None  # type: ignore

# Graphify Mandatory Guard (V2.15) — blocks writes when Graphify is absent.
try:
    from runtime import graphify_guard as _gg  # type: ignore
except Exception:
    _gg = None  # type: ignore

# TENOR task prompt generator (V2.15.4)
# Uses __file__-relative import so it works even when .agent/ is not on sys.path
try:
    import sys as _ttp_sys
    from pathlib import Path as _ttp_Path
    _ttp_agent_dir = _ttp_Path(__file__).resolve().parent.parent
    if str(_ttp_agent_dir) not in _ttp_sys.path:
        _ttp_sys.path.insert(0, str(_ttp_agent_dir))
    from host_adapter import tenor_task_prompt as _ttp  # type: ignore
except Exception:
    _ttp = None  # type: ignore

try:
    from host_adapter.host_config import verify_host_process_binding as _verify_host_process_binding  # type: ignore
    _HOST_BINDING_AVAILABLE = True
except Exception:
    _HOST_BINDING_AVAILABLE = False

# Proof signer — non-circular TENOR proof verification (v0.2.15+)
try:
    import sys as _sys
    from pathlib import Path as _Path
    _SEL_SCRIPTS = _Path(__file__).parent.parent / "workflow" / "scribe" / "sel" / "scripts"
    if str(_SEL_SCRIPTS) not in _sys.path:
        _sys.path.insert(0, str(_SEL_SCRIPTS))
    from proof_signer import (  # type: ignore
        consume_agent_proof as _consume_agent_proof,
        purge_expired_proofs as _purge_expired_proofs,
        verify_proof as _verify_proof,
    )
    _PROOF_SIGNER_AVAILABLE = True
except Exception as _proof_import_exc:  # noqa: BLE001
    _PROOF_SIGNER_AVAILABLE = False
    _proof_import_exc_msg = str(_proof_import_exc)

server.SERVER_VERSION = "0.2.15"
if os.environ.get("AGENT_SCRIBE_GRAPHIFY_ROOT"):
    server.ROOT = Path(os.environ["AGENT_SCRIBE_GRAPHIFY_ROOT"]).resolve()
    server.AGENT_DIR = server.ROOT / ".agent"
_BASE_WORKFLOW_NEXT = server.workflow_next
_BASE_TOOL_SCHEMA = server.tool_schema
_BASE_BEFORE_TASK = server.before_task
_BASE_SCRIBE_QUERY = server.scribe_query
_BASE_GRAPHIFY_QUERY = server.graphify_query
_BASE_FINISH_TASK = server.finish_task
_BASE_CLAIM_RESOURCE = server.claim_resource
_BASE_REGISTER_AGENT = server.register_agent
_BASE_RETIRE_AGENT = server.retire_agent
_DELETE_INTENTS = {"delete", "remove"}
_SCRIBE_VERDICTS = {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}
_GRAPHIFY_VERDICTS = {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}
_CONTEXT_VERDICTS = {"BEFORE_TASK_OK", *_SCRIBE_VERDICTS, *_GRAPHIFY_VERDICTS}
_WRITE_DONE_VERDICTS = {"PATCH_APPLIED", "PATCH_APPLIED_CONFIRMED", "RESOURCE_DELETED"}
_RECORD_REQUIRED_VERDICTS = {*_WRITE_DONE_VERDICTS, "CLAIM_RELEASED"}
_RECORD_DONE_VERDICTS = {"SCRIBE_RECORD_STAGED_ONLY", "SCRIBE_RECORD_WRITTEN"}
_CANONICAL_MEMORY_TERMINAL_VERDICTS = {"CANONICAL_MEMORY_PROMOTED", "CANONICAL_MEMORY_SKIPPED_WITH_REASON"}
_CANONICAL_MEMORY_BLOCKING_VERDICTS = {"CANONICAL_MEMORY_REQUIRED", "CANONICAL_MEMORY_SKIP_REJECTED"}
_WRITE_OR_DECISION_INTENTS = {"write", "edit", "patch", "modify", "code", "fix", "refactor", "test", "create", "delete", "remove", "decision"}
_MUTATING_CONTEXT_INTENTS = {"write", "edit", "patch", "modify", "code", "fix", "refactor", "test", "create", "delete", "remove"}
_CANONICAL_INTENTS = ["read", "write", "delete"]

_GENERIC_TOKENS: frozenset = frozenset({
    "file", "files", "code", "main", "index", "core", "utils", "util",
    "module", "modules", "api", "backend", "frontend", "component",
    "components", "page", "pages", "service", "services", "helper",
    "helpers", "common", "shared", "base", "config", "data", "docs",
    "node", "public", "static", "assets", "media", "dist", "build",
    "target", "bin", "obj", "cache", "temp", "logs", "migrations",
    "middleware", "types", "type", "enum", "enums", "interfaces",
    "model", "models", "view", "views", "controller", "controllers",
    "input", "output", "result", "error", "errors", "handler",
    "handlers", "style", "styles", "route", "routes", "schema",
    "schemas", "table", "tables", "field", "fields", "column",
    "columns", "value", "values", "param", "params", "method",
    "methods", "event", "events", "state", "status", "rules",
    "policy", "policies", "action", "actions", "name", "names",
    "group", "groups", "role", "roles", "user", "users", "admin",
    "token", "tokens", "key", "keys", "session", "sessions",
})
_EXTENSION_TOKENS: frozenset = frozenset({
    "py", "ts", "tsx", "js", "jsx", "md", "json", "yaml", "yml",
    "toml", "ini", "cfg", "conf", "xml", "html", "css", "scss",
    "sass", "less", "svg", "png", "jpg", "jpeg", "gif", "ico",
    "woff", "woff2", "ttf", "eot", "pdf", "doc", "docx", "xls",
    "xlsx", "csv", "txt", "log", "env", "gitignore", "dockerfile",
    "makefile", "sql", "db", "lock", "sum", "mod", "sum",
})


def _check_scribe_scope(task_resource: str, query: str, stdout: str) -> tuple[bool, str]:
    if not task_resource:
        return (True, "no resource to scope against")
    q = query.strip().lower()
    s = stdout.strip().lower()
    r = task_resource.strip().lower()
    r_name = Path(r).name.lower() if r else ""
    r_parent = Path(r).parent.name.lower() if Path(r).parent.name else ""

    if r_name and (r_name in q or r_name in s):
        return (True, "resource name found in query or stdout")
    if r_parent and (r_parent in q or r_parent in s):
        return (True, "resource parent path found in query or stdout")
    r_tokens: set[str] = set()
    for sep in (".", "/", "-", "_"):
        r_tokens.update(t for t in r.split(sep) if len(t) >= 4 and t not in _GENERIC_TOKENS and t not in _EXTENSION_TOKENS and not t.isdigit())
    if r_name:
        r_tokens.update(t for t in r_name.split(".") if len(t) >= 4 and t not in _GENERIC_TOKENS and t not in _EXTENSION_TOKENS and not t.isdigit())
        r_tokens.update(t for t in r_name.split("-") if len(t) >= 4 and t not in _GENERIC_TOKENS and t not in _EXTENSION_TOKENS and not t.isdigit())
    if r_parent:
        r_tokens.update(t for t in r_parent.split("-") if len(t) >= 4 and t not in _GENERIC_TOKENS and t not in _EXTENSION_TOKENS and not t.isdigit())
    matching_tokens = [t for t in r_tokens if t in q or t in s]
    if matching_tokens:
        return (True, f"resource token(s) found in query or stdout: {', '.join(matching_tokens[:3])}")
    if "project-wide" in q or "global-context" in q:
        has_reason = "because:" in q or "reason:" in q or (r_name and r_name in q)
        if has_reason:
            return (True, "project-wide/global-context scope with explicit reason or resource reference")
    return (False, "SCRIBE context irrelevant for write task — query and stdout do not reference the target resource or any parent scope")


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


def _task_requires_canonical_promotion(task_data: dict[str, Any] | None) -> bool:
    return bool(task_data and task_data.get("scribe_record_done") and task_data.get("scribe_record_required") and not task_data.get("scribe_record_promoted"))


def _scribe_record_args(agent_id: str, task_id: str, context_token: str, request: str, intent: str, resource: str, task_data: dict[str, Any] | None = None) -> dict[str, Any]:
    record_type = "task_summary"
    if task_data and task_data.get("scribe_record_policy") == "canonical_required":
        record_type = str(task_data.get("scribe_record_type") or "validation")
    return {
        "agent_id": agent_id,
        "task_id": task_id,
        "context_token": context_token,
        "request": request or "task completed",
        "summary": "record useful task outcome before finish",
        "touched_resources": [resource] if resource else [],
        "resources": [resource] if resource else [],
        "verdict": "READ_TASK_NOTE" if (intent or "").strip().lower() == "read" else "READY_TO_FINISH",
        "record_type": record_type,
        "severity": "medium",
        "tags": ["workflow_next"],
    }


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
    if code in {"AGENT_ID_REQUIRED", "AGENT_UNKNOWN_OR_UNREGISTERED"} and _host_process_binding_verified():
        return server.ok({
            "ok": False,
            "verdict": "TENOR_INIT_REQUIRED",
            "state": "HARD_STOP",
            "reason": "The verified host has no bridged active identity. Re-run canonical TENOR INIT; never mint a replacement agent.",
            "agent_id": agent_id,
            "next_action": ".agent/workflow/scribe/scribe tenor-init --type cli --host $AGENT_MCP_HOST",
            "after_success_must_call": {
                "tool": "tenor_init_bridge",
                "args": {
                    "agent_session_id": agent_id or "<agent-id-from-tenor-init>",
                    "host_tool": os.environ.get("AGENT_MCP_HOST", "unknown"),
                    "model_name": "",
                },
            },
            "forbidden": ["register_agent", "retire_agent", "before_task", "direct_file_edit"],
        })
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


def _host_process_binding_verified() -> bool:
    if not _HOST_BINDING_AVAILABLE:
        return False
    try:
        result = _verify_host_process_binding(
            server.ROOT,
            environ=os.environ,
            claimed_host=os.environ.get("AGENT_MCP_HOST", ""),
        )
    except Exception:
        return False
    return bool(result.get("ok"))


def register_agent(host_tool: str, model_name: str = "", agent_id: str | None = None) -> Dict[str, Any]:
    """Keep the TENOR-bridged identity stable inside a verified host process."""

    if not _host_process_binding_verified():
        return _BASE_REGISTER_AGENT(host_tool=host_tool, model_name=model_name, agent_id=agent_id)
    existing = db.get_agent(agent_id or "") if agent_id else None
    if not existing or str(existing.get("status") or "") == "retired":
        return server.ok({
            "ok": False,
            "verdict": "AGENT_REGISTRATION_REQUIRES_TENOR_INIT",
            "state": "HARD_STOP",
            "reason": "A verified host cannot mint a replacement identity. Re-run TENOR INIT and bridge the session.",
            "agent_id": agent_id or "",
            "next_action": ".agent/workflow/scribe/scribe tenor-init --type cli --host $AGENT_MCP_HOST",
            "after_success_must_call": {
                "tool": "tenor_init_bridge",
                "args": {
                    "agent_session_id": agent_id or "<agent-id-from-tenor-init>",
                    "host_tool": host_tool or "unknown",
                    "model_name": model_name or "",
                },
            },
            "forbidden": ["register_agent", "retire_agent", "before_task", "direct_file_edit"],
        })
    return server.ok({
        "verdict": "AGENT_REGISTERED",
        "state": "AGENT_IDENTITY_STABLE",
        "agent": existing,
        "idempotent": True,
    })


def retire_agent(agent_id: str, reason: str = "") -> Dict[str, Any]:
    blockers = db.agent_lifecycle_blockers(agent_id)
    if blockers["total"]:
        return server.ok({
            "ok": False,
            "verdict": "AGENT_RETIRE_REFUSED_ACTIVE_OWNERSHIP",
            "state": "HARD_STOP",
            "reason": "An identity with active tasks, claims, locks or patches cannot be retired to escape a workflow stop.",
            "agent_id": agent_id,
            "blockers": blockers,
            "must_call": {"tool": "resume_task_context", "args": {"agent_id": agent_id, "task_id": "<active-task-id>"}},
            "forbidden": ["retire_agent", "register_agent", "before_task", "direct_file_edit"],
        })
    try:
        return _BASE_RETIRE_AGENT(agent_id=agent_id, reason=reason)
    except db.CoordinationError as exc:
        return server.ok({"ok": False, "verdict": str(exc), "state": "HARD_STOP", "agent_id": agent_id})


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

    try:
        canonical_intent = task_context.require_machine_intent(intent)
    except task_context.TaskContextError as exc:
        return server.ok({
            "ok": False,
            "verdict": exc.code,
            "state": "HARD_STOP",
            "reason": "before_task accepts a bounded machine intent, never descriptive prose.",
            "intent": (intent or "").strip(),
            "allowed_intents": exc.details.get("allowed_intents", sorted(_CANONICAL_INTENTS)),
            "forbidden": _PRE_CONTEXT_FORBIDDEN,
        })

    active_tasks = [
        task for task in task_context.list_tasks(agent_id=agent_id, status="active").get("tasks", [])
        if int(task.get("expires_at") or 0) >= int(time.time())
    ]
    if active_tasks:
        active = active_tasks[0]
        return server.ok({
            "ok": False,
            "verdict": "ACTIVE_TASK_EXISTS",
            "state": "ACTIVE_TASK_EXISTS",
            "reason": "One agent identity may own only one active task. Resume it; do not create a replacement task or identity.",
            "agent_id": agent_id,
            "resource": str(active.get("resource") or ""),
            "intent": str(active.get("intent") or ""),
            "must_call": {"tool": "resume_task_context", "args": {"agent_id": agent_id, "task_id": str(active.get("task_id") or "")}},
            "forbidden": ["before_task", "register_agent", "retire_agent", "claim_resource", "propose_patch", "apply_patch", "delete_resource", "finish_task", "direct_file_edit"],
        })

    result = _BASE_BEFORE_TASK(request=request, agent_id=agent_id)
    payload = json.loads(result["content"][0]["text"])
    if payload.get("verdict") != "BEFORE_TASK_OK":
        return result
    try:
        context = task_context.create_task_context(
            agent_id=agent_id,
            request=request,
            intent=canonical_intent,
            resource=resource or "",
            requires_graphify=_requires_graphify(request, canonical_intent, resource),
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
    payload["intent"] = canonical_intent
    if direct_fs_tripwire.is_mutating_intent(canonical_intent):
        snapshot = direct_fs_tripwire.workspace_snapshot(
            server.ROOT, context["task_id"], agent_id, resource or ""
        )
        payload["direct_fs_tripwire"] = {"verdict": snapshot["verdict"]}
        canonical_snapshot = canonical_memory_gate.snapshot_before_task(
            server.ROOT,
            context["task_id"],
            agent_id,
            request=request,
            intent=canonical_intent,
            resource=resource or "",
        )
        payload["canonical_memory_gate"] = {"verdict": canonical_snapshot["verdict"]}
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

    # V2.15.17: Block write/patch_queue claims on read-only tasks
    if task_id:
        _ci = _resolve_stored_task_intent(agent_id, task_id)
        if _ci.get("ok") and (_ci.get("intent") or "") in _READ_ONLY_INTENTS:
            requested_mode = "patch_queue" if mode in server.WRITE_MODES else mode
            if requested_mode in server.WRITE_MODES:
                return server.ok({
                    "ok": False,
                    "verdict": "READ_ONLY_CLAIM_FORBIDDEN",
                    "state": "HARD_STOP",
                    "reason": (
                        f"Task '{task_id}' has intent "
                        f"'{_ci.get('intent') or ''}' which is read-only. "
                        "Write/patch_queue claims are forbidden on read tasks."
                    ),
                })

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
    payload = json.loads(result["content"][0]["text"])
    res = payload.get("result", {})
    if res.get("returncode", 0) != 0:
        return server.ok({
            "ok": False,
            "verdict": "SCRIBE_QUERY_FAILED",
            "query": query,
            "result": res,
        })
    if agent_id or task_id or context_token:
        task_intent = ""
        task_resource = ""
        base_verdict = payload.get("verdict", "")
        if agent_id and task_id:
            try:
                task_data = task_context.get_task_context(agent_id, task_id)
                task_intent = str(task_data.get("intent", "")).strip().lower()
                task_resource = task_data.get("resource", "") or ""
            except task_context.TaskContextError:
                pass
        if task_intent in _MUTATING_CONTEXT_INTENTS and base_verdict == "SCRIBE_QUERY_DONE":
            stdout = (res.get("stdout") or "").strip()
            if not stdout:
                return server.ok({
                    "ok": False,
                    "verdict": "SCRIBE_CONTEXT_EMPTY",
                    "query": query,
                    "result": res,
                    "reason": "SCRIBE returned no content. Write tasks require a non-empty SCRIBE result.",
                })
            scope_ok, scope_reason = _check_scribe_scope(task_resource, query, stdout)
            if not scope_ok:
                return server.ok({
                    "ok": False,
                    "verdict": "SCRIBE_CONTEXT_IRRELEVANT_FOR_WRITE",
                    "query": query,
                    "result": res,
                    "reason": scope_reason,
                })
        try:
            result_count = 1 if (res.get("stdout") or "").strip() else 0
            ctx_result = task_context.mark_scribe_done(
                agent_id, task_id, context_token,
                result_count=result_count,
                result_resources=task_resource,
            )
        except task_context.TaskContextError as exc:
            raise _context_error(exc) from exc
        payload["task_context"] = {
            "task_id": task_id,
            "scribe_done": True,
            "memory_hash": ctx_result.get("memory_hash"),
            "scribe_result_count": ctx_result.get("scribe_result_count"),
        }
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
    preflight = _preflight_lock(agent_id, patch_id, task_id, context_token, action_lease_id)
    if preflight is not None:
        return server.ok(preflight)
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
    applied = patch_queue.apply_patch(agent_id=agent_id, patch_id=patch_id)
    if applied.get("verdict") == "PATCH_APPLIED":
        direct_fs_tripwire.record_authorized_mutation(
            task_id=task_id, agent_id=agent_id,
            resource=str(applied.get("target_path") or ""),
            tool="apply_patch", patch_id=patch_id,
            after_hash=str(applied.get("new_hash") or ""),
            project_root=server.ROOT,
        )
    return server.ok(applied)


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
    deleted = delete_ops.delete_resource(agent_id=agent_id, resource=resource, base_hash=base_hash, confirm_phrase=confirm_phrase, reason=reason)
    if deleted.get("verdict") == "RESOURCE_DELETED":
        direct_fs_tripwire.record_authorized_mutation(
            task_id=task_id, agent_id=agent_id, resource=resource,
            tool="delete_resource", before_hash=base_hash,
            project_root=server.ROOT,
        )
    return server.ok(deleted)


_READ_ONLY_INTENTS = {"read", "query", "ask", "explain", "list", "show", "status"}
_SAFE_FINISH_INTENTS = {"read", "finish", "done", "complete", "end", "finalize"}


def _resolve_stored_task_intent(agent_id: str, task_id: str) -> dict[str, Any]:
    if not task_id:
        return {"ok": True, "state": "NO_TASK_ID", "intent": None}
    try:
        status = task_context.task_status(task_id)
    except task_context.TaskContextError:
        return {
            "ok": False, "state": "TASK_CONTEXT_UNKNOWN_TASK",
            "verdict": "TASK_CONTEXT_UNKNOWN_TASK",
            "reason": "task_id was provided but no task context exists.",
        }
    if status.get("agent_id") != agent_id:
        return {
            "ok": False, "state": "TASK_AGENT_MISMATCH",
            "verdict": "TASK_AGENT_MISMATCH",
            "reason": f"task_id belongs to agent '{status.get('agent_id')}' but caller is '{agent_id}'.",
        }
    stored = (status.get("intent") or "").strip().lower()
    return {
        "ok": True, "state": "TASK_CONTEXT_FOUND",
        "intent": stored if stored else None,
        "agent_id": status.get("agent_id"),
    }


def _scribe_gate_error(exc: scribe_commit_gate.ScribeCommitGateError) -> server.ToolError:
    return server.ToolError(exc.code)


def _write_gate_record(record: dict[str, Any]) -> dict[str, Any]:
    result = scribe_record(
        agent_id=str(record.get("agent_id") or ""),
        request=str(record.get("summary") or "task completed"),
        summary=str(record.get("summary") or ""),
        touched_resources=list(record.get("touched_resources") or []),
        verdict=str(record.get("verdict") or ""),
        tags=list(record.get("tags") or []),
        record_type=str(record.get("type") or "TASK_SUMMARY").lower(),
        severity="medium",
        resources=list(record.get("touched_resources") or []),
    )
    payload = json.loads(result["content"][0]["text"])
    if payload.get("verdict") not in {"SCRIBE_RECORD_STAGED_ONLY", "SCRIBE_RECORD_WRITTEN"}:
        raise server.ToolError("SCRIBE_COMMIT_GATE_RECORD_WRITE_FAILED")
    return payload


def _finish_ok_after_gate(agent_id: str, summary: str, task_id: str, context_token: str) -> Dict[str, Any]:
    result = _BASE_FINISH_TASK(agent_id=agent_id, summary=summary)
    payload = json.loads(result["content"][0]["text"])
    if payload.get("verdict") == "TASK_FINISHED_OK" and (task_id or context_token):
        try:
            payload["task_context"] = task_context.finish_task_context(agent_id, task_id, context_token)
        except task_context.TaskContextError as exc:
            raise _context_error(exc) from exc
        payload["next_state_hint"] = "READY_FOR_NEXT_TASK"
        payload["terminal"] = True
        return server.ok(payload)
    return result


def finish_task(agent_id: str, summary: str = "", task_id: str = "", context_token: str = "", action_lease_id: str = "", intent: str = "", canonical_memory_skip_reason: str = "") -> Dict[str, Any]:
    ctx = _resolve_stored_task_intent(agent_id, task_id)
    normalized_intent = (intent or "").strip().lower()

    if not ctx["ok"]:
        return server.ok({
            "ok": False,
            "verdict": ctx["verdict"],
            "state": "HARD_STOP",
            "reason": ctx["reason"],
        })

    if ctx["state"] == "TASK_CONTEXT_FOUND":
        stored = ctx.get("intent") or ""
        is_read_only = stored in _READ_ONLY_INTENTS
    else:
        is_read_only = normalized_intent in _SAFE_FINISH_INTENTS

    task_memory_state: dict[str, Any] | None = None
    if task_id:
        try:
            task_memory_state = task_context.get_task_context(agent_id, task_id)
        except task_context.TaskContextError:
            task_memory_state = None
    if _task_requires_canonical_promotion(task_memory_state):
        skip_reason = (canonical_memory_skip_reason or "").strip()
        if skip_reason:
            if not canonical_memory_gate._skip_reason_is_strong(skip_reason):
                return server.ok({
                    "ok": False,
                    "verdict": "CANONICAL_MEMORY_SKIP_REJECTED",
                    "state": "CANONICAL_MEMORY_SKIP_REJECTED",
                    "reason": "Skip reason is too weak or generic to justify finishing a durable memory task without promotion.",
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "skip_reason": skip_reason,
                    "forbidden": ["finish_task", "direct_file_edit"],
                })
            try:
                task_context.mark_scribe_record_skipped(agent_id, task_id, context_token, skip_reason=skip_reason)
            except task_context.TaskContextError as exc:
                raise _context_error(exc) from exc
            result = _finish_ok_after_gate(agent_id, summary, task_id, context_token)
            payload = json.loads(result["content"][0]["text"])
            payload.update({
                "verdict": "CANONICAL_MEMORY_SKIPPED_WITH_REASON",
                "state": "CANONICAL_MEMORY_SKIPPED_WITH_REASON",
                "skip_reason": skip_reason,
                "terminal": True,
                "next_state_hint": "READY_FOR_NEXT_TASK",
            })
            return server.ok(payload)
        return server.ok({
            "ok": False,
            "verdict": "CANONICAL_MEMORY_REQUIRED",
            "state": "CANONICAL_MEMORY_REQUIRED",
            "reason": "A staged durable record must be promoted before finish_task can close.",
            "task_id": task_id,
            "agent_id": agent_id,
            "record_path": task_memory_state.get("scribe_record_path") or "",
            "must_call": {
                "tool": "scribe_promote_record",
                "args": {
                    "agent_id": agent_id,
                    "task_id": task_id,
                    "context_token": context_token,
                    "record_path": task_memory_state.get("scribe_record_path") or "",
                },
            },
            "forbidden": ["finish_task", "direct_file_edit"],
        })

    if is_read_only:
        return _finish_ok_after_gate(agent_id, summary, task_id, context_token)

    lease_check = _require_action_lease(
        action_lease_id, "finish_task", agent_id, "finish_task",
        task_id=task_id,
    )
    if lease_check is not None:
        return lease_check

    try:
        task_context.require_context_ready(agent_id, task_id, context_token, allowed_intents=_MUTATING_CONTEXT_INTENTS)
    except task_context.TaskContextError as exc:
        raise _context_error(exc) from exc

    # A task with a queued patch is mechanically unfinished regardless of the
    # wider checkout state. Return this deterministic ownership verdict before
    # the workspace-wide tripwire so unrelated concurrent activity cannot hide
    # the actionable pending-patch gate.
    pending = server._agent_pending_patches(agent_id)
    if pending:
        return server.ok({"verdict": "FINISH_REFUSED_PENDING_PATCHES", "pending_patches": pending})

    tripwire = direct_fs_tripwire.assert_no_unauthorized_mutations(
        server.ROOT, task_id, agent_id
    )
    if tripwire.get("verdict") == direct_fs_tripwire.DIRECT_WRITE_BYPASS_DETECTED:
        return server.ok({
            "ok": False,
            "verdict": direct_fs_tripwire.DIRECT_WRITE_BYPASS_DETECTED,
            "state": "WORKSPACE_AUDIT_REQUIRED",
            "reason": "Unauthorized dirty workspace mutation detected outside MCP-authorized writes.",
            "task_id": task_id,
            "agent_id": agent_id,
            "suspects": tripwire.get("suspects", []),
            "git_status": tripwire.get("git_status", []),
            "must_call": {"tool": "workspace_audit", "args": {"agent_id": agent_id, "task_id": task_id}},
            "forbidden": ["finish_task", "git_commit", "git_push", "direct_file_edit"],
        })

    patch_ids = direct_fs_tripwire.applied_patch_ids(server.ROOT, task_id, agent_id, resource=direct_fs_tripwire.MEMOIRE_FILE)
    if patch_ids:
        memo_file = server.ROOT / direct_fs_tripwire.MEMOIRE_FILE
        memory_content = memo_file.read_text(encoding="utf-8", errors="replace") if memo_file.is_file() else ""
        memory_proven = any(pid and pid in memory_content for pid in patch_ids)
        if not memory_proven:
            return server.ok({
                "ok": False,
                "verdict": "MEMORY_PROOF_REQUIRED",
                "state": "MEMORY_PROOF_REQUIRED",
                "reason": (
                    f"Canonical memory required: none of the {len(patch_ids)} MCP-applied "
                    "patch_ids appear in AGENT-MEMOIRE_PROJECT_STATUS.scribe. "
                    "Record a SCRIBE entry referencing at least one applied patch_id "
                    "before finish_task."
                ),
                "applied_patch_ids": patch_ids,
                "forbidden": ["finish_task", "git_commit", "git_push", "direct_file_edit"],
            })

    claims = server._active_claims_for(agent_id)
    if claims["owned"]:
        return server.ok({
            "ok": False,
            "verdict": "FINISH_REFUSED_ACTIVE_CLAIMS",
            "state": "ACTIVE_CLAIM_BEFORE_FINISH",
            "active_claims": claims["owned"],
            "forbidden": ["finish_task", "direct_file_edit"],
        })

    if canonical_memory_gate.is_active(server.ROOT):
        canonical = canonical_memory_gate.evaluate_finish(
            server.ROOT,
            task_id,
            agent_id,
            request=summary or "",
            intent=stored or normalized_intent,
            summary=summary or "",
            skip_reason=canonical_memory_skip_reason or "",
        )
        verdict = canonical.get("verdict")
        if verdict in _CANONICAL_MEMORY_BLOCKING_VERDICTS:
            return server.ok({
                "ok": False,
                **canonical,
                "state": canonical.get("state") or verdict,
                "terminal": False,
                "forbidden": ["finish_task", "direct_file_edit"],
            })
        if verdict in _CANONICAL_MEMORY_TERMINAL_VERDICTS:
            result = _finish_ok_after_gate(agent_id, summary, task_id, context_token)
            payload = json.loads(result["content"][0]["text"])
            payload.update(canonical)
            payload["terminal"] = True
            payload["next_state_hint"] = "READY_FOR_NEXT_TASK"
            return server.ok(payload)

    try:
        gate = scribe_commit_gate.require_or_create(agent_id, task_id, context_token, summary=summary)
    except scribe_commit_gate.ScribeCommitGateError as exc:
        raise _scribe_gate_error(exc) from exc
    if gate["status"] == scribe_commit_gate.PENDING:
        return server.ok({
            "ok": True,
            "verdict": "SCRIBE_COMMIT_GATE_REQUIRED",
            "state": "SCRIBE_COMMIT_GATE_REQUIRED",
            "gate_id": gate["gate_id"],
            "task_id": task_id,
            "proposed_record": gate["proposed_record"],
            "allowed_decisions": gate["allowed_decisions"],
            "terminal": False,
            "must_call": {
                "tool": "scribe_commit_gate_resolve",
                "args": {"agent_id": agent_id, "task_id": task_id, "context_token": context_token, "decision": "commit"},
            },
            "forbidden": ["direct_file_edit"],
        })
    return _finish_ok_after_gate(agent_id, summary, task_id, context_token)


def scribe_commit_gate_status(agent_id: str, task_id: str, context_token: str) -> Dict[str, Any]:
    try:
        return server.ok(scribe_commit_gate.get_status(agent_id, task_id, context_token))
    except scribe_commit_gate.ScribeCommitGateError as exc:
        raise _scribe_gate_error(exc) from exc


def scribe_commit_gate_resolve(
    agent_id: str,
    task_id: str,
    context_token: str,
    decision: str,
    edited_record: Dict[str, Any] | None = None,
    proposed_record: Dict[str, Any] | None = None,
    skip_reason: str = "",
) -> Dict[str, Any]:
    record = edited_record if edited_record is not None else proposed_record
    try:
        resolved = scribe_commit_gate.resolve(
            agent_id,
            task_id,
            context_token,
            decision,
            _write_gate_record,
            edited_record=record,
            skip_reason=skip_reason,
        )
    except scribe_commit_gate.ScribeCommitGateError as exc:
        raise _scribe_gate_error(exc) from exc
    return server.ok(resolved)


def _project_relative_path(path: Path) -> str:
    try:
        resolved_root = server.ROOT.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        return resolved_path.relative_to(resolved_root).as_posix()
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise server.ToolError("SCRIBE_RECORD_OUTSIDE_PROJECT") from exc


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
    task_id: str = "",
    context_token: str = "",
    memory_policy: str = "",
) -> Dict[str, Any]:
    if not agent_id:
        raise server.ToolError("agent_id is required")
    merged_resources = resources if resources is not None else touched_resources
    completion_claim = (
        (record_type or "").strip().lower() in {"bugfix", "bug_fix", "fix"}
        or (verdict or "").strip().upper() in {"FIXED", "RESOLVED", "PATCH_APPLIED_CONFIRMED"}
    )
    if completion_claim:
        if not task_id or not context_token:
            return server.ok({
                "ok": False,
                "verdict": "SCRIBE_RECORD_TASK_CONTEXT_REQUIRED",
                "state": "HARD_STOP",
                "reason": "A bugfix/completion record must be bound to the active task and context token.",
            })
        try:
            task_data = task_context.verify_active_context(agent_id, task_id, context_token)
        except task_context.TaskContextError as exc:
            raise _context_error(exc) from exc
        if task_context.normalize_intent(str(task_data.get("intent") or "")) not in {"write", "delete"}:
            return server.ok({
                "ok": False,
                "verdict": "SCRIBE_RECORD_MUTATING_TASK_REQUIRED",
                "state": "HARD_STOP",
                "task_id": task_id,
            })
        patch_ids = direct_fs_tripwire.applied_patch_ids(server.ROOT, task_id, agent_id)
        if not patch_ids:
            return server.ok({
                "ok": False,
                "verdict": "SCRIBE_RECORD_MCP_PATCH_RECEIPT_REQUIRED",
                "state": "HARD_STOP",
                "reason": "A FIXED/bugfix record requires at least one applied MCP patch receipt.",
                "task_id": task_id,
                "agent_id": agent_id,
                "applied_patch_ids": [],
                "forbidden": ["scribe_record", "finish_task", "git_commit", "git_push", "direct_file_edit"],
            })
        audit = direct_fs_tripwire.detect_unauthorized_mutations(server.ROOT, task_id, agent_id)
        if audit.get("verdict") == direct_fs_tripwire.DIRECT_WRITE_BYPASS_DETECTED:
            return server.ok({
                "ok": False,
                "verdict": direct_fs_tripwire.DIRECT_WRITE_BYPASS_DETECTED,
                "state": "HARD_STOP",
                "reason": "A completion record cannot certify a workspace containing unauthorized mutations.",
                "task_id": task_id,
                "suspects": audit.get("suspects", []),
                "forbidden": ["scribe_record", "finish_task", "git_commit", "git_push", "direct_file_edit"],
            })
    now = int(time.time())
    policy = canonical_memory_gate.derive_memory_policy(record_type or "", memory_policy or "", request or "", summary or "", verdict or "")
    required = canonical_memory_gate.policy_requires_canonical_promotion(policy)
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
        "task_id": task_id or "",
        "context_token": context_token or "",
        "memory_policy": policy,
        "canonical_memory_required": required,
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
        record_path = _project_relative_path(target)
        if task_id and context_token:
            try:
                task_context.mark_scribe_record_staged(
                    agent_id,
                    task_id,
                    context_token,
                    required=required,
                    policy=policy,
                    record_path=record_path,
                    record_digest=digest,
                )
            except task_context.TaskContextError as exc:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
                raise _context_error(exc) from exc
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return server.ok({
        "verdict": "SCRIBE_RECORD_STAGED_ONLY",
        "record_json_created": True,
        "canonical_memory_updated": False,
        "canonical_memory_required": required,
        "promotion_required": required,
        "promotion_tool": "scribe_promote_record" if required else "",
        "memory_policy": policy,
        "record_path": record_path,
        "entry": payload,
    })


def scribe_promote_record(
    agent_id: str = "",
    task_id: str = "",
    context_token: str = "",
    record_path: str = "",
) -> Dict[str, Any]:
    if not agent_id:
        raise server.ToolError("agent_id is required")
    if not task_id or not context_token:
        raise server.ToolError("task_id and context_token are required")
    try:
        task_data = task_context.get_task_context(agent_id, task_id)
    except task_context.TaskContextError as exc:
        raise _context_error(exc) from exc
    safe_record_path = record_path or str(task_data.get("scribe_record_path") or "")
    if not safe_record_path:
        raise server.ToolError("record_path is required")
    source = Path(safe_record_path)
    if not source.is_absolute():
        source = (server.ROOT / source).resolve()
    else:
        source = source.resolve()
    if not source.is_file():
        raise server.ToolError("SCRIBE_RECORD_NOT_FOUND")
    _project_relative_path(source)
    record = json.loads(source.read_text(encoding="utf-8"))
    policy = canonical_memory_gate.derive_memory_policy(
        str(record.get("record_type") or record.get("type") or ""),
        str(record.get("memory_policy") or task_data.get("scribe_record_policy") or ""),
        str(record.get("request") or ""),
        str(record.get("summary") or ""),
        str(record.get("verdict") or ""),
    )
    scope = str(task_data.get("resource") or "") or ""
    result = canonical_memory_gate.promote_record(
        server.ROOT,
        record,
        source,
        scope=scope,
        memory_policy=policy,
        agent_id=agent_id,
        task_id=task_id,
    )
    if result.get("verdict") == "CANONICAL_MEMORY_PROMOTED":
        try:
            task_context.mark_scribe_record_promoted(agent_id, task_id, context_token, entry_id=str(result.get("entry_id") or ""))
        except task_context.TaskContextError as exc:
            raise _context_error(exc) from exc
    return server.ok(result)

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

    tdata: Dict[str, Any] | None = None
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

    if tdata and tdata.get("status") == "active":
        intent = str(tdata.get("intent") or "")
        if not safe_resource:
            safe_resource = str(tdata.get("resource") or "")

    # Graphify Mandatory Guard: authorization uses the stored machine intent,
    # never caller-supplied descriptive prose.
    normalized_intent = task_context.normalize_intent(intent)
    if normalized_intent in {"write", "delete"}:
        guard_block = _enforce_graphify_guard()
        if guard_block is not None:
            return guard_block

    normalized = normalized_intent
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
        result = direct_fs_tripwire.detect_unauthorized_mutations(
            server.ROOT, task_id, agent_id, resource=resource,
        )
    except db.CoordinationError as exc:
        return server.ok({
            "verdict": str(exc),
            "state": str(exc),
            "forbidden": _LEASE_FORBIDDEN,
        })
    if result.get("verdict") == direct_fs_tripwire.DIRECT_WRITE_BYPASS_DETECTED:
        result["state"] = "WORKSPACE_AUDIT_REQUIRED"
        result["forbidden"] = ["finish_task", "git_commit", "git_push", "direct_file_edit"]
    elif result.get("verdict") == direct_fs_tripwire.TRIPWIRE_CLEAN:
        result["verdict"] = "WORKSPACE_AUDIT_OK"
        result["state"] = "WORKSPACE_AUDIT_OK"
    return server.ok(result)


def _claims_for(agent_id: str, resource: str) -> Dict[str, Any]:
    return server._active_claims_for(agent_id, resource)


# ── V2.15.17: Read-only FSM — isolated from write reflexes ──────────
_READ_ONLY_FORBIDDEN = [
    "claim_resource", "propose_patch", "apply_patch",
    "delete_resource", "direct_file_edit",
    "resource_lock_claim", "resource_lock_release", "resource_lock_heartbeat",
]


def _read_only_workflow_next(
    agent_id: str,
    request: str,
    intent: str,
    resource: str,
    task_id: str,
    context_token: str,
    last: str,
    task_data: dict[str, Any],
) -> dict[str, Any]:
    if last == "TASK_FINISHED_OK":
        return server.ok({
            "ok": True,
            "verdict": "READY_FOR_NEXT_TASK",
            "state": "READY_FOR_NEXT_TASK",
            "reason": "Read task finished. Awaiting next user task.",
        })
    if not task_data or task_data.get("status") != "active":
        return server._next_payload(
            state="BEFORE_TASK_REQUIRED",
            tool="before_task",
            args={"agent_id": agent_id, "request": request or "", "intent": intent or "", "resource": resource or ""},
            reason="No active task context. Start a before_task first.",
            forbidden=_READ_ONLY_FORBIDDEN,
        )
    # AFTER before_task — guide through scribe → graphify → record → finish
    if not task_data.get("scribe_done") and last not in _SCRIBE_VERDICTS:
        return server._next_payload(
            state="SCRIBE_CONTEXT_REQUIRED",
            tool="scribe_query",
            args={"agent_id": agent_id, "task_id": task_id, "context_token": context_token,
                  "query": _targeted_scribe_query(request, intent, resource), "limit": 5},
            reason="Targeted SCRIBE RAG query is recommended for read task context.",
            forbidden=_READ_ONLY_FORBIDDEN,
        )
    if task_data.get("requires_graphify") and not task_data.get("graphify_done") \
            and last not in _GRAPHIFY_VERDICTS:
        return server._next_payload(
            state="GRAPHIFY_CONTEXT_REQUIRED",
            tool="graphify_query",
            args={"agent_id": agent_id, "task_id": task_id, "context_token": context_token,
                  "query": _targeted_graphify_query(request, intent, resource),
                  "resource": resource or ""},
            reason="Targeted Graphify impact query is recommended for read task context.",
            forbidden=_READ_ONLY_FORBIDDEN,
        )
    # Scribe or graphify done → scribe_record, then optional canonical promotion, then finish
    if _task_requires_canonical_promotion(task_data):
        return server._next_payload(
            state="SCRIBE_PROMOTION_REQUIRED",
            tool="scribe_promote_record",
            args={
                "agent_id": agent_id,
                "task_id": task_id,
                "context_token": context_token,
                "record_path": str(task_data.get("scribe_record_path") or ""),
            },
            reason="The staged memory record is durable and must be promoted before finish_task.",
            forbidden=_READ_ONLY_FORBIDDEN,
        )
    if task_data.get("scribe_record_done"):
        return server._next_payload(
            state="FINISH_TASK_RECOMMENDED",
            tool="finish_task",
            args={"agent_id": agent_id, "task_id": task_id, "context_token": context_token, "intent": "read"},
            reason="Read task complete. Call finish_task to close.",
            forbidden=_READ_ONLY_FORBIDDEN,
        )
    if last in _SCRIBE_VERDICTS | _GRAPHIFY_VERDICTS or last == "BEFORE_TASK_OK" or (task_data.get("scribe_done") and not task_data.get("scribe_record_done")):
        return server._next_payload(
            state="SCRIBE_RECORD_RECOMMENDED",
            tool="scribe_record",
            args=_scribe_record_args(agent_id, task_id, context_token, request, intent, resource, task_data),
            reason="Typed memory recording is recommended before finishing the read task.",
            forbidden=_READ_ONLY_FORBIDDEN,
        )
    # Safety fallback — guide toward finish_task
    return server._next_payload(
        state="FINISH_TASK_RECOMMENDED",
        tool="finish_task",
        args={"agent_id": agent_id, "task_id": task_id, "context_token": context_token, "intent": "read"},
        reason="Read task ready for finish.",
        forbidden=_READ_ONLY_FORBIDDEN,
    )
    return server._next_payload(
        state="FINISH_TASK_RECOMMENDED",
        tool="finish_task",
        args={"agent_id": agent_id, "task_id": task_id, "context_token": context_token, "intent": "read"},
        reason="Read task ready for finish.",
        forbidden=_READ_ONLY_FORBIDDEN,
    )


# ── V2.15.18: Strict task context resolver for workflow_next ──────────
# Must not fall through to legacy when task_id is provided but invalid.


def _resolve_task_context_for_workflow_next(
    agent_id: str,
    task_id: str,
) -> dict[str, Any]:
    if not task_id:
        return {"ok": True, "state": "NO_TASK_ID", "task_data": None}
    try:
        task_data = task_context.task_status(task_id)
    except task_context.TaskContextError as exc:
        return {
            "ok": False,
            "verdict": "TASK_CONTEXT_UNKNOWN_TASK",
            "state": "HARD_STOP",
            "reason": f"task_id was provided but no active task context exists: {exc}",
        }
    if task_data.get("agent_id") != agent_id:
        return {
            "ok": False,
            "verdict": "TASK_AGENT_MISMATCH",
            "state": "HARD_STOP",
            "reason": (
                f"task_id belongs to agent '{task_data.get('agent_id')}' "
                f"but caller is '{agent_id}'."
            ),
        }
    return {
        "ok": True,
        "state": "TASK_CONTEXT_FOUND",
        "task_data": task_data,
    }


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

    # TASK_FINISHED_OK is a terminal verdict — the workflow is done.
    if last in {"TASK_FINISHED_OK", "CANONICAL_MEMORY_PROMOTED", "CANONICAL_MEMORY_SKIPPED_WITH_REASON"}:
        return server.ok({
            "ok": True,
            "verdict": "READY_FOR_NEXT_TASK",
            "state": "READY_FOR_NEXT_TASK",
            "reason": "Previous task finished. No pending actions. Awaiting next user task.",
        })
    if last == direct_fs_tripwire.DIRECT_WRITE_BYPASS_DETECTED:
        return server._next_payload(
            state="BYPASS_BLOCKED",
            tool="workspace_audit",
            args={"agent_id": agent_id, "task_id": task_id, "resource": resource or ""},
            reason="Direct write bypass was detected. Re-audit or resolve the dirty workspace before any new claim, patch or finish attempt.",
            forbidden=["before_task", "claim_resource", "resource_lock_claim", "file_hash", "propose_patch", "apply_patch", "delete_resource", "finish_task", "git_commit", "git_push", "direct_file_edit"],
        )
    if last == "SCRIBE_COMMIT_GATE_REQUIRED":
        return server._next_payload(
            state="SCRIBE_COMMIT_GATE_REQUIRED",
            tool="scribe_commit_gate_resolve",
            args={"agent_id": agent_id, "task_id": task_id, "context_token": context_token, "decision": "commit"},
            reason="Mutating finish_task is blocked until an explicit SCRIBE memory decision is resolved.",
            forbidden=["finish_task", "direct_file_edit"],
        )
    if last in _CANONICAL_MEMORY_BLOCKING_VERDICTS:
        return server._next_payload(
            state=last,
            tool="finish_task",
            args={
                "agent_id": agent_id,
                "task_id": task_id,
                "context_token": context_token,
                "intent": intent or "",
                "canonical_memory_skip_reason": "",
            },
            reason="Canonical memory must be promoted or an auditable skip reason must be provided before finish_task can close.",
            forbidden=["direct_file_edit"],
        )

    stop = _track_loop(agent_id, resource, last)
    if stop is not None:
        return stop

    task_data: Dict[str, Any] | None = None
    if task_id:
        ctx = _resolve_task_context_for_workflow_next(agent_id, task_id)
        if not ctx["ok"]:
            return server.ok({
                "ok": False,
                "verdict": ctx["verdict"],
                "state": "HARD_STOP",
                "reason": ctx["reason"],
                "forbidden": [
                    "before_task", "claim_resource", "scribe_query", "graphify_query",
                    "propose_patch", "apply_patch", "delete_resource",
                    "finish_task", "direct_file_edit",
                ],
            })
        task_data = ctx.get("task_data")
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

        if task_data and task_data.get("status") == "active":
            intent = str(task_data.get("intent") or "")
            normalized = task_context.normalize_intent(intent)
            if not resource:
                resource = str(task_data.get("resource") or "")

    if task_data and task_data.get("status") == "active" and direct_fs_tripwire.is_mutating_intent(task_data.get("intent") or ""):
        tripwire = direct_fs_tripwire.detect_unauthorized_mutations(
            server.ROOT, task_id, agent_id, resource=resource or ""
        )
        if tripwire.get("verdict") == direct_fs_tripwire.DIRECT_WRITE_BYPASS_DETECTED:
            return server._next_payload(
                state="WORKSPACE_AUDIT_REQUIRED",
                tool="workspace_audit",
                args={"agent_id": agent_id, "task_id": task_id, "resource": resource or ""},
                reason="Unauthorized dirty workspace mutation detected outside MCP-authorized writes.",
                forbidden=["finish_task", "git_commit", "git_push", "direct_file_edit"],
                context={"suspects": tripwire.get("suspects", []), "git_status": tripwire.get("git_status", [])},
            )

    # V2.15.17: Read-only FSM — stored intent isolates read path from write reflexes
    if task_data and task_data.get("status") == "active":
        _ri = _resolve_stored_task_intent(agent_id, task_id)
        if _ri.get("ok") and (_ri.get("intent") or "") in _READ_ONLY_INTENTS:
            return _read_only_workflow_next(
                agent_id, request, _ri["intent"], resource,
                task_id, context_token, last, task_data,
            )

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

    if normalized in server.FINISH_INTENTS:
        if _task_requires_canonical_promotion(task_data):
            return server._next_payload(
                state="SCRIBE_PROMOTION_REQUIRED",
                tool="scribe_promote_record",
                args={
                    "agent_id": agent_id,
                    "task_id": task_id,
                    "context_token": context_token,
                    "record_path": str(task_data.get("scribe_record_path") or ""),
                },
                reason="The staged memory record is durable and must be promoted before finish_task.",
                forbidden=["finish_task", "direct_file_edit"],
            )
        pending = server._agent_pending_patches(agent_id, resource)
        if pending:
            return _BASE_WORKFLOW_NEXT(agent_id=agent_id, request=request, intent=intent, resource=resource, mode=mode, base_hash=base_hash, patch_id=patch_id, claim_id=claim_id, last_verdict=last_verdict, host_tool=host_tool, model_name=model_name)
        claims = server._active_claims_for(agent_id)
        if claims["owned"]:
            return _BASE_WORKFLOW_NEXT(agent_id=agent_id, request=request, intent=intent, resource=resource, mode=mode, base_hash=base_hash, patch_id=patch_id, claim_id=claim_id, last_verdict=last_verdict, host_tool=host_tool, model_name=model_name)
        if _requires_scribe_record(intent, last_verdict) and not (task_data or {}).get("scribe_record_done"):
            return server._next_payload(
                state="SCRIBE_RECORD_REQUIRED",
                tool="scribe_record",
                args=_scribe_record_args(agent_id, task_id, context_token, request, intent, resource, task_data),
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
            if must_tool == "claim_resource":
                payload["must_call"]["tool"] = "resource_lock_claim"
                payload["reason"] = "Exclusive resource lock is recommended before apply_patch."
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


def root_hygiene_status(max_parent_depth: int = 4, strict: bool = False) -> Dict[str, Any]:
    try:
        report = root_hygiene.assert_safe_project_root(server.ROOT, strict=bool(strict), max_parent_depth=max_parent_depth)
    except RuntimeError as exc:
        report = root_hygiene.inspect_root_hygiene(server.ROOT, max_parent_depth=max_parent_depth)
        report["strict_error"] = str(exc)
    if max_parent_depth != 4:
        report = root_hygiene.inspect_root_hygiene(server.ROOT, max_parent_depth=max_parent_depth)
    report["formatted"] = root_hygiene.format_root_hygiene_report(report)
    return server.ok(report)


def runtime_backup_status(keep_last: int = 20) -> Dict[str, Any]:
    return server.ok(runtime_backup_retention.runtime_backup_report(server.ROOT, keep_last=keep_last))


def runtime_backup_cleanup(keep_last: int = 20, apply: bool = False, organize: bool = False, cleanup: bool = False) -> Dict[str, Any]:
    if organize:
        return server.ok(runtime_backup_retention.organize_runtime_backups(server.ROOT, apply=bool(apply)))
    if cleanup:
        return server.ok(runtime_backup_retention.cleanup_runtime_backups(server.ROOT, keep_last=keep_last, apply=bool(apply)))
    return server.ok(runtime_backup_retention.runtime_backup_report(server.ROOT, keep_last=keep_last))


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

def graphify_project_build(timeout_seconds: int = 180) -> Dict[str, Any]:
    """Build Graphify through the canonical isolated project wrapper."""

    timeout = int(timeout_seconds)
    if timeout < 1 or timeout > 3600:
        return server.ok({
            "ok": False,
            "verdict": "GRAPHIFY_BUILD_TIMEOUT_INVALID",
            "state": "HARD_STOP",
            "reason": "timeout_seconds must be between 1 and 3600.",
        })
    ownership = db.workspace_mutation_blockers()
    if ownership["total"]:
        return server.ok({
            "ok": False,
            "verdict": "GRAPHIFY_BUILD_ACTIVE_OWNERSHIP",
            "state": "HARD_STOP",
            "reason": "Release active product claims before rebuilding the project graph.",
            "ownership": ownership,
        })
    command = [
        sys.executable,
        str(server.ROOT / ".agent" / "workflow" / "scribe" / "scribe"),
        "graph",
        "--project-build",
        "--timeout",
        str(timeout),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(server.ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout + 30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return server.ok({
            "ok": False,
            "verdict": "GRAPHIFY_BUILD_TIMEOUT",
            "state": "HARD_STOP",
            "output": output[-20000:],
        })
    output = completed.stdout or ""
    if completed.returncode != 0:
        return server.ok({
            "ok": False,
            "verdict": "GRAPHIFY_PROJECT_BUILD_FAILED",
            "state": "HARD_STOP",
            "returncode": completed.returncode,
            "output": output[-20000:],
        })
    readiness = _gg.graphify_readiness.inspect_graphify_readiness(server.ROOT) if _gg is not None else None
    if readiness is None or not readiness.ok:
        return server.ok({
            "ok": False,
            "verdict": getattr(readiness, "verdict", "GRAPHIFY_VERIFY_UNAVAILABLE"),
            "state": "HARD_STOP",
            "output": output[-20000:],
            "readiness": readiness.to_dict() if readiness is not None else {},
        })
    return server.ok({
        "ok": True,
        "verdict": "GRAPHIFY_PROJECT_BUILD_OK",
        "state": "GRAPHIFY_READY",
        "output": output[-20000:],
        "readiness": readiness.to_dict(),
    })


def _schema_props(base: Dict[str, Any], extra: Dict[str, str]) -> Dict[str, Any]:
    schema = json.loads(json.dumps(base))
    props = schema.setdefault("properties", {})
    for name, kind in extra.items():
        props[name] = {"type": kind}
    schema["additionalProperties"] = False
    return schema


def _with_intent_enum(schema: Dict[str, Any]) -> Dict[str, Any]:
    result = json.loads(json.dumps(schema))
    result.setdefault("properties", {})["intent"] = {
        "type": "string",
        "enum": list(_CANONICAL_INTENTS),
        "description": "Canonical machine intent; descriptive prose is forbidden.",
    }
    return result


def tool_schema(name: str) -> Dict[str, Any]:
    if name == "before_task":
        return _with_intent_enum(_schema_props(_BASE_TOOL_SCHEMA(name), {"intent": "string", "resource": "string"}))
    if name == "workflow_next":
        return _with_intent_enum(_schema_props(_BASE_TOOL_SCHEMA(name), {"task_id": "string", "context_token": "string"}))
    if name == "resume_task_context":
        return {"type": "object", "properties": {"agent_id": {"type": "string"}, "task_id": {"type": "string"}}, "additionalProperties": False}
    if name == "root_hygiene_status":
        return {"type": "object", "properties": {"max_parent_depth": {"type": "integer"}, "strict": {"type": "boolean"}}, "additionalProperties": False}
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
        return _with_intent_enum(_schema_props(_BASE_TOOL_SCHEMA(name), {"task_id": "string", "context_token": "string", "action_lease_id": "string", "intent": "string", "canonical_memory_skip_reason": "string"}))
    if name == "scribe_commit_gate_status":
        return {"type": "object", "properties": {"agent_id": {"type": "string"}, "task_id": {"type": "string"}, "context_token": {"type": "string"}}, "required": ["agent_id", "task_id", "context_token"], "additionalProperties": False}
    if name == "scribe_commit_gate_resolve":
        return {"type": "object", "properties": {"agent_id": {"type": "string"}, "task_id": {"type": "string"}, "context_token": {"type": "string"}, "decision": {"type": "string"}, "edited_record": {"type": "object"}, "proposed_record": {"type": "object"}, "skip_reason": {"type": "string"}}, "required": ["agent_id", "task_id", "context_token", "decision"], "additionalProperties": False}
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
                "task_id": {"type": "string"},
                "context_token": {"type": "string"},
                "memory_policy": {"type": "string"},
            },
            "required": ["agent_id", "request", "summary", "touched_resources", "verdict"],
            "additionalProperties": False,
        }
    if name == "scribe_promote_record":
        return {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "task_id": {"type": "string"},
                "context_token": {"type": "string"},
                "record_path": {"type": "string"},
            },
            "required": ["agent_id", "task_id", "context_token"],
            "additionalProperties": False,
        }
    if name == "list_tasks":
        return {"type": "object", "properties": {"agent_id": {"type": "string"}, "status": {"type": "string"}}, "additionalProperties": False}
    if name == "task_status":
        return {"type": "object", "properties": {"task_id": {"type": "string"}}, "additionalProperties": False}
    if name == "runtime_backup_status":
        return {"type": "object", "properties": {"keep_last": {"type": "integer"}}, "additionalProperties": False}
    if name == "runtime_backup_cleanup":
        return {"type": "object", "properties": {"keep_last": {"type": "integer"}, "apply": {"type": "boolean"}, "organize": {"type": "boolean"}, "cleanup": {"type": "boolean"}}, "additionalProperties": False}
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
        return _with_intent_enum({"type": "object", "properties": {"agent_id": {"type": "string"}, "request": {"type": "string"}, "intent": {"type": "string"}, "resource": {"type": "string"}, "task_id": {"type": "string"}, "context_token": {"type": "string"}, "planned_action": {"type": "string"}}, "additionalProperties": False})
    if name == "graphify_project_build":
        return {
            "type": "object",
            "properties": {"timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600}},
            "additionalProperties": False,
        }
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
                "context_token": {"type": "string"},
                "ttl_seconds": {"type": "integer"},
            },
            "required": ["agent_id", "resource", "task_id", "context_token"],
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
    if name == "resource_lock_heartbeat":
        return {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "resource": {"type": "string"},
                "ttl_seconds": {"type": "integer"},
            },
            "required": ["agent_id", "resource"],
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
    if name == "graphify_required_check":
        return {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "workspace_root": {"type": "string"},
            },
            "additionalProperties": False,
        }
    if name == "tenor_task_prompt":
        return {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "mode": {"type": "string"},
                "intent": {"type": "string"},
                "resource": {"type": "string"},
                "model_tier": {"type": "string"},
            },
            "required": ["task"],
            "additionalProperties": False,
        }
    if name == "tenor_init_bridge":
        return {
            "type": "object",
            "properties": {
                "agent_session_id": {"type": "string"},
                "host_tool": {"type": "string"},
                "model_name": {"type": "string"},
                "proof_token": {
                    "type": "string",
                    "description": "Deprecated compatibility path. Normal V2.16 host bridge consumes the server-side proof without exposing a token.",
                },
            },
            "required": ["agent_session_id"],
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
    context_token: str = "",
    ttl_seconds: int = 300,
) -> Dict[str, Any]:
    from runtime.resource_locks import resource_lock_claim as _rl_claim  # type: ignore
    return server.ok(_rl_claim(
        agent_id=agent_id, resource=resource, task_id=task_id,
        context_token=context_token, ttl_seconds=ttl_seconds,
    ))


def resource_lock_release(
    agent_id: str = "",
    resource: str = "",
    lock_id: str = "",
) -> Dict[str, Any]:
    from runtime.resource_locks import resource_lock_release as _rl_release  # type: ignore
    return server.ok(_rl_release(agent_id=agent_id, resource=resource, lock_id=lock_id))


def resource_lock_heartbeat(
    agent_id: str = "",
    resource: str = "",
    ttl_seconds: int = 300,
) -> Dict[str, Any]:
    from runtime.resource_locks import resource_lock_heartbeat as _rl_hb  # type: ignore
    return server.ok(_rl_hb(agent_id=agent_id, resource=resource, ttl_seconds=ttl_seconds))


def resource_lock_status(resource: str = "") -> Dict[str, Any]:
    from runtime.resource_locks import resource_lock_status as _rl_status  # type: ignore
    result = _rl_status(resource=resource)
    return server.ok(result) if result.get("ok") else server.ok(result)


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
        result = _verify_proof(root, token, agent_id, mark_consumed=False)
    except Exception as exc:  # noqa: BLE001
        return server.ok({"ok": False, "verdict": "PROOF_INTERNAL_ERROR", "detail": str(exc)})
    return server.ok(result)


# ─────────────────────────────────────────────────────────────
# tenor_task_prompt (V2.15.4) — generate disciplined TENOR task prompt
# ─────────────────────────────────────────────────────────────

def tenor_task_prompt(
    task: str = "",
    mode: str = "STANDARD",
    intent: str = "write",
    resource: str = "",
    model_tier: str = "large",
) -> dict[str, Any]:
    if _ttp is None:
        return server.ok({
            "ok": False,
            "verdict": "TENOR_TASK_PROMPT_MODULE_UNAVAILABLE",
            "state": "HARD_STOP",
            "reason": (
                "tenor_task_prompt.py module not loaded. "
                "HARD STOP: do not continue, do not improvise, do not answer directly. "
                "Use the TENOR TASK CLI instead: "
                "python3 .agent/scripts/tenor_task.py --task '...'"
            ),
            "prompt": "",
        })
    if not task or not task.strip():
        return server.ok({
            "ok": False,
            "verdict": "TENOR_TASK_PROMPT_INVALID",
            "reason": "task is required and must not be empty.",
            "prompt": "",
        })
    try:
        result = _ttp.generate_task_prompt(
            task=task,
            mode=mode,
            intent=intent,
            resource=resource,
            model_tier=model_tier,
        )
    except Exception as exc:
        return server.ok({
            "ok": False,
            "verdict": "TENOR_TASK_PROMPT_ERROR",
            "reason": str(exc),
            "prompt": "",
        })
    return server.ok(result)


# ─────────────────────────────────────────────────────────────
# tenor_init_bridge (V2.15.5) — bridge TENOR session to MCP agent registry
# ─────────────────────────────────────────────────────────────

def _retire_ghost_agents(aid: str, host_tool: str) -> dict[str, Any]:
    retired: list[dict[str, str]] = []
    observed: list[dict[str, Any]] = []
    status = "ok"
    error_msg = ""
    if not host_tool or host_tool == "unknown":
        return {"retired": retired, "status": "skipped", "error": "no host_tool"}
    try:
        all_agents = db.list_agents().get("agents", [])
        for agent in all_agents:
            gid = agent.get("agent_id", "")
            gstatus = agent.get("status", "")
            ghost = agent.get("host_tool", "")
            if gid != aid and gstatus == "active" and ghost == host_tool:
                tasks = task_context.list_tasks(agent_id=gid, status="active")
                observed.append({
                    "agent_id": gid,
                    "host_tool": host_tool,
                    "status": gstatus,
                    "process_alive": bool(agent.get("process_alive")),
                    "active_task_count": int(tasks.get("count", 0)),
                    "action": "preserved",
                })
    except Exception as exc:
        status = "failed"
        error_msg = str(exc)
    return {"retired": retired, "observed": observed, "status": status, "error": error_msg}


def tenor_init_bridge(
    agent_session_id: str = "",
    host_tool: str = "unknown",
    model_name: str = "",
    proof_token: str = "",
) -> dict[str, Any]:
    if not agent_session_id or not agent_session_id.strip():
        return server.ok({
            "ok": False,
            "verdict": "TENOR_INIT_BRIDGE_INVALID",
            "state": "AGENT_SESSION_ID_REQUIRED",
            "reason": "agent_session_id from TENOR INIT SCRIBE-CHECK output is required.",
        })

    steps: list[dict[str, Any]] = []
    aid = agent_session_id.strip()

    # Step 0 — a shell-launched local server is not host visibility proof.
    if not _HOST_BINDING_AVAILABLE:
        return server.ok({
            "ok": False,
            "verdict": "TENOR_INIT_BRIDGE_HOST_BINDING_UNAVAILABLE",
            "state": "HOST_MCP_UNBOUND",
            "reason": "Host binding verifier is unavailable; local JSON-RPC cannot establish host visibility.",
            "steps": steps,
        })
    binding = _verify_host_process_binding(server.ROOT.resolve(), claimed_host=host_tool)
    if not binding.get("ok"):
        return server.ok({
            "ok": False,
            "verdict": "TENOR_INIT_BRIDGE_HOST_UNBOUND",
            "state": "HOST_MCP_UNBOUND",
            "reason": f"Host process binding failed: {binding.get('verdict', 'UNKNOWN')}",
            "host_binding": binding,
            "steps": steps,
        })
    bound_host = str(binding.get("host_id") or "unknown")
    root_binding = {
        "ok": True,
        "verdict": "MCP_ROOT_BOUND_TO_HOST_PROCESS",
        "project_root": str(server.ROOT.resolve()),
        "host_id": bound_host,
        "binding_id": str(binding.get("binding_id") or ""),
        "config_path": str(binding.get("config_path") or ""),
        "config_sha256": str(binding.get("config_sha256") or ""),
    }
    steps.append({
        "step": "verify_host_process_binding",
        "ok": True,
        "verdict": binding.get("verdict", "HOST_PROCESS_BOUND"),
        "host_id": bound_host,
        "project_root": root_binding["project_root"],
        "config_sha256": binding.get("config_sha256", ""),
    })

    # Step 1 — consume the proof atomically. The normal path never exposes it.
    if not _PROOF_SIGNER_AVAILABLE:
        return server.ok({
            "ok": False,
            "verdict": "TENOR_INIT_BRIDGE_PROOF_UNVERIFIABLE",
            "state": "HARD_STOP",
            "reason": (
                "proof_signer.py is not loaded. "
                "Cannot verify TENOR proof. Host must NOT continue."
            ),
            "steps": steps,
        })
    try:
        if proof_token and proof_token.strip():
            proof_result = _verify_proof(server.ROOT.resolve(), proof_token, aid)
            proof_mode = "legacy_explicit_token"
        else:
            proof_result = _consume_agent_proof(server.ROOT.resolve(), aid)
            proof_mode = "host_bound_server_side"
        if not proof_result.get("ok"):
            return server.ok({
                "ok": False,
                "verdict": "TENOR_INIT_BRIDGE_PROOF_FAILED",
                "state": "HARD_STOP",
                "reason": (
                    f"Proof verification failed: {proof_result.get('verdict', 'UNKNOWN')} "
                    f"— {proof_result.get('detail', '')}"
                ),
                "steps": steps,
            })
        steps.append({
            "step": "verify_proof",
            "ok": True,
            "verdict": proof_result.get("verdict", "PROOF_VALID"),
            "consumption_mode": proof_mode,
        })
    except Exception as exc:
        return server.ok({
            "ok": False,
            "verdict": "TENOR_INIT_BRIDGE_PROOF_ERROR",
            "state": "HARD_STOP",
            "reason": f"Proof verification raised exception: {exc}",
            "steps": steps,
        })

    # Step 2 — register_agent
    try:
        agent_data = db.register_agent(
            host_tool=bound_host,
            model_name=model_name or "",
            agent_id=aid,
        )
        steps.append({
            "step": "register_agent",
            "ok": True,
            "agent_id": agent_data.get("agent_id", aid),
            "status": agent_data.get("status", ""),
        })
    except Exception as exc:
        return server.ok({
            "ok": False,
            "verdict": "TENOR_INIT_BRIDGE_REGISTER_FAILED",
            "state": "INIT_BLOCKED_MCP_AGENT_UNREGISTERED",
            "reason": f"register_agent failed for agent_session_id={aid}: {exc}",
            "steps": steps,
        })

    # Step 3 — agent_status
    try:
        status_data = db.agent_status(aid)
        status_val = str(status_data.get("status", "") or "")
        steps.append({
            "step": "agent_status",
            "ok": status_val == "active",
            "status": status_val,
        })
        if status_val != "active":
            return server.ok({
                "ok": False,
                "verdict": "TENOR_INIT_BRIDGE_AGENT_NOT_ACTIVE",
                "state": "MCP_AGENT_NOT_ACTIVE",
                "reason": f"Agent {aid} status is '{status_val}', expected 'active'.",
                "steps": steps,
            })
    except Exception as exc:
        return server.ok({
            "ok": False,
            "verdict": "TENOR_INIT_BRIDGE_STATUS_FAILED",
            "state": "MCP_AGENT_STATUS_FAILED",
            "reason": f"agent_status failed for {aid}: {exc}",
            "steps": steps,
        })

    # Step 4 — discipline_ping
    try:
        discipline.record_guard_ping(aid, phase="post-init", resource="")
        steps.append({
            "step": "discipline_ping",
            "ok": True,
            "phase": "post-init",
        })
    except Exception as exc:
        return server.ok({
            "ok": False,
            "verdict": "TENOR_INIT_BRIDGE_DISCIPLINE_PING_FAILED",
            "state": "DISCIPLINE_PING_FAILED",
            "reason": f"discipline_ping failed for {aid}: {exc}",
            "steps": steps,
        })

    # Step 5 — observe parallel agents. A new bridge never has authority to
    # retire another process merely because its task/heartbeat looks old.
    ghost_result = _retire_ghost_agents(aid, bound_host)
    steps.append({
        "step": "retire_ghosts",
        "ok": ghost_result["status"] == "ok",
        "count": len(ghost_result["retired"]),
        "ghosts": ghost_result["retired"],
        "observed_parallel_agents": ghost_result.get("observed", []),
        "status": ghost_result["status"],
        "error": ghost_result.get("error", ""),
    })

    # TENOR INIT does NOT create a user task — no workflow_next here.
    # The host calls workflow_next on its own during the first TENOR TASK.

    return server.ok({
        "ok": True,
        "verdict": "TENOR_INIT_BRIDGE_OK",
        "state": "INIT_MCP_BRIDGE_COMPLETE",
        "agent_session_id": aid,
        "host_tool": bound_host,
        "model_name": model_name or "",
        "host_binding": binding,
        "root_binding": root_binding,
        "ready_scope": "MCP_BRIDGE_ONLY",
        "tenor_init_ready_must_be_reported_by_real_host_after_root_binding": True,
        "steps": steps,
        "retired_ghosts": [g["agent_id"] for g in ghost_result["retired"]] if ghost_result["retired"] else [],
        "ghost_cleanup_status": ghost_result["status"],
    })


# ─────────────────────────────────────────────────────────────
# Graphify Mandatory Guard (V2.15) — enforce Graphify presence
# ─────────────────────────────────────────────────────────────

_GRAPHIFY_FORBIDDEN = [
    "claim_resource", "file_hash", "propose_patch", "apply_patch",
    "delete_resource", "finish_task", "direct_file_edit",
]

_GRAPHIFY_GUARD_CACHE: dict[str, Any] = {}


def _enforce_graphify_guard(
    workspace_root: Path | str | None = None,
    agent_id: str = "",
) -> dict[str, Any] | None:
    """Check Graphify mandatory guard; return blocking payload or None.

    Uses a simple module-level cache to avoid re-checking every call
    within the same server invocation. The cache is reset on each
    graphify_required_check() call.
    """
    if _gg is None:
        return server.ok({
            "ok": False,
            "verdict": "GRAPHIFY_GUARD_MODULE_UNAVAILABLE",
            "state": "GRAPHIFY_GUARD_MODULE_UNAVAILABLE",
            "reason": "graphify_guard.py runtime module not loaded.",
            "blocking": True,
            "write_allowed": False,
            "forbidden": _GRAPHIFY_FORBIDDEN,
        })

    cached = _GRAPHIFY_GUARD_CACHE.get("result")
    if cached is not None:
        if not cached.get("ok") and cached.get("blocking"):
            return server.ok({
                **cached,
                "state": cached.get("verdict", "GRAPHIFY_BLOCKED"),
                "forbidden": _GRAPHIFY_FORBIDDEN,
            })
        return None

    result = _gg.check_graphify_required(
        workspace_root=workspace_root,
        host_type="opencode",
        auto_write_guide=True,
    )
    _GRAPHIFY_GUARD_CACHE["result"] = result

    if not result.get("ok") and result.get("blocking"):
        return server.ok({
            **result,
            "state": result.get("verdict", "GRAPHIFY_BLOCKED"),
            "forbidden": _GRAPHIFY_FORBIDDEN,
        })
    return None


def graphify_required_check(
    agent_id: str = "",
    workspace_root: str = "",
) -> dict[str, Any]:
    """Run the Graphify mandatory guard check and return structured verdict.

    Checks:
      1. Is the graphify CLI installed on PATH?
      2. Are graphify-out/graph.json, GRAPH_REPORT.md, graph.html present?

    If Graphify is missing or outputs are incomplete, write operations
    are blocked. Use this tool proactively at session start to verify
    Graphify readiness.

    Returns a blocking payload with next_actions when Graphify is not ready.
    """
    # Reset the cache so this call is always fresh.
    _GRAPHIFY_GUARD_CACHE.clear()

    if _gg is None:
        return server.ok({
            "ok": False,
            "verdict": "GRAPHIFY_GUARD_MODULE_UNAVAILABLE",
            "state": "GRAPHIFY_GUARD_MODULE_UNAVAILABLE",
            "reason": "graphify_guard.py runtime module not loaded.",
            "blocking": True,
            "write_allowed": False,
            "forbidden": _GRAPHIFY_FORBIDDEN,
        })

    root_path: Path | None = None
    root_str = workspace_root.strip() if workspace_root else ""
    if root_str:
        root_path = Path(root_str).resolve()
    elif os.environ.get("AGENT_SCRIBE_GRAPHIFY_ROOT"):
        root_path = Path(os.environ["AGENT_SCRIBE_GRAPHIFY_ROOT"]).resolve()
    else:
        root_path = server.ROOT if hasattr(server, "ROOT") else Path.cwd()

    result = _gg.check_graphify_required(
        workspace_root=root_path,
        host_type="opencode",
        auto_write_guide=True,
    )
    _GRAPHIFY_GUARD_CACHE["result"] = result

    if result.get("ok") and result.get("write_allowed"):
        return server.ok({
            **result,
            "verdict": result.get("verdict", "GRAPHIFY_READY"),
            "state": "WRITE_ALLOWED",
            "forbidden": _GRAPHIFY_FORBIDDEN,
        })

    return server.ok({
        **result,
        "state": result.get("verdict", "GRAPHIFY_BLOCKED"),
        "forbidden": _GRAPHIFY_FORBIDDEN,
    })


# ─────────────────────────────────────────────────────────────
# Wire all tools into the server
# ─────────────────────────────────────────────────────────────

# Guard: only register once (prevents module reload from overwriting
# server.TOOLS with functions from a different module instance).
if not getattr(server, "_EXT_REGISTERED", False):
    server._EXT_REGISTERED = True

    server.workflow_next = workflow_next
    server.workflow_snapshot = workflow_snapshot
    server.register_agent = register_agent
    server.retire_agent = retire_agent
    server.before_task = before_task
    server.resume_task_context = resume_task_context
    server.claim_resource = claim_resource
    server.scribe_query = scribe_query
    server.graphify_query = graphify_query
    server.propose_patch = propose_patch
    server.delete_resource = delete_resource
    server.finish_task = finish_task
    server.scribe_commit_gate_status = scribe_commit_gate_status
    server.scribe_commit_gate_resolve = scribe_commit_gate_resolve
    server.scribe_record = scribe_record
    server.scribe_promote_record = scribe_promote_record
    server.list_tasks = list_tasks
    server.task_status = task_status
    server.wait_for_tasks = wait_for_tasks
    server.runtime_backup_status = runtime_backup_status
    server.runtime_backup_cleanup = runtime_backup_cleanup
    server.root_hygiene_status = root_hygiene_status
    server.graphify_project_build = graphify_project_build
    server.tool_schema = tool_schema
    server.TOOLS["register_agent"] = register_agent
    server.TOOLS["retire_agent"] = retire_agent
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
    server.TOOLS["scribe_commit_gate_status"] = scribe_commit_gate_status
    server.TOOLS["scribe_commit_gate_resolve"] = scribe_commit_gate_resolve
    server.TOOLS["scribe_record"] = scribe_record
    server.TOOLS["scribe_promote_record"] = scribe_promote_record
    server.TOOLS["list_tasks"] = list_tasks
    server.TOOLS["task_status"] = task_status
    server.TOOLS["wait_for_tasks"] = wait_for_tasks
    server.TOOLS["runtime_backup_status"] = runtime_backup_status
    server.TOOLS["runtime_backup_cleanup"] = runtime_backup_cleanup
    server.TOOLS["root_hygiene_status"] = root_hygiene_status
    server.TOOLS["graphify_project_build"] = graphify_project_build
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
    server.TOOLS["graphify_required_check"] = graphify_required_check
    # V2.15.4 new tool
    server.TOOLS["tenor_task_prompt"] = tenor_task_prompt
    # V2.15.5 new tool
    server.TOOLS["tenor_init_bridge"] = tenor_init_bridge
    # V2.15.19 new tools
    server.TOOLS["resource_lock_heartbeat"] = resource_lock_heartbeat

handle = server.handle
list_tools = server.list_tools
main = server.main

if __name__ == "__main__":
    raise SystemExit(server.main())
