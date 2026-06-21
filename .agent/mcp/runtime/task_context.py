from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
import uuid
from typing import Any

try:
    from . import patch_queue
    from .db import connect, init_db, now_ts as db_now_ts
except Exception:
    import patch_queue  # type: ignore
    from db import connect, init_db, now_ts as db_now_ts  # type: ignore

DEFAULT_TTL_SECONDS = 900


class TaskContextError(RuntimeError):
    pass


def now_ts() -> int:
    return int(time.time())


def ttl_seconds(default: int = DEFAULT_TTL_SECONDS) -> int:
    raw = os.environ.get("AGENT_TASK_CONTEXT_TTL_SECONDS", "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise TaskContextError("TASK_CONTEXT_TTL_INVALID") from exc
    if value < 60 or value > 86400:
        raise TaskContextError("TASK_CONTEXT_TTL_INVALID")
    return value


def ensure_schema() -> None:
    init_db()
    with connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_context_v2(
              task_id TEXT PRIMARY KEY,
              token_hash TEXT NOT NULL,
              agent_id TEXT NOT NULL,
              request_hash TEXT NOT NULL,
              request TEXT NOT NULL,
              intent TEXT,
              resource TEXT,
              requires_graphify INTEGER NOT NULL DEFAULT 0,
              before_done INTEGER NOT NULL DEFAULT 1,
              scribe_done INTEGER NOT NULL DEFAULT 0,
              graphify_done INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'active',
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              finished_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_task_context_v2_agent_status
              ON task_context_v2(agent_id,status,expires_at);
            CREATE INDEX IF NOT EXISTS idx_task_context_v2_agent_resource
              ON task_context_v2(agent_id,resource,status,request_hash);
            CREATE TABLE IF NOT EXISTS workflow_retry_v1(
              retry_id TEXT PRIMARY KEY,
              agent_id TEXT NOT NULL,
              resource TEXT NOT NULL,
              error_code TEXT NOT NULL,
              count INTEGER NOT NULL,
              first_seen INTEGER NOT NULL,
              last_seen INTEGER NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_retry_key
              ON workflow_retry_v1(agent_id,resource,error_code);
            """
        )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_hash(request: str, intent: str, resource: str) -> str:
    payload = "\0".join([request or "", intent or "", resource or ""])
    return _hash(payload)


def _safe_resource(resource: str) -> str:
    return patch_queue.safe_resource(resource) if resource else ""


def create_task_context(
    agent_id: str,
    request: str,
    intent: str = "",
    resource: str = "",
    requires_graphify: bool = False,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    if not agent_id:
        raise TaskContextError("TASK_CONTEXT_REQUIRED: agent_id is required")
    if not request:
        raise TaskContextError("TASK_CONTEXT_REQUIRED: request is required")
    ensure_schema()
    safe_resource = _safe_resource(resource)
    token = secrets.token_urlsafe(32)
    task_id = f"task-{uuid.uuid4().hex[:16]}"
    created = now_ts()
    ttl = ttl_seconds if ttl_seconds is not None else globals()["ttl_seconds"]()
    expires = created + ttl
    with connect() as con:
        existing = con.execute(
            """
            SELECT task_id,agent_id,request,intent,resource,expires_at
            FROM task_context_v2
            WHERE agent_id=? AND request_hash=? AND status='active' AND expires_at>=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (agent_id, _request_hash(request, intent, safe_resource), created),
        ).fetchone()
        if existing:
            raise TaskContextError("ACTIVE_TASK_EXISTS")
        con.execute(
            """
            INSERT INTO task_context_v2(
              task_id,token_hash,agent_id,request_hash,request,intent,resource,
              requires_graphify,before_done,scribe_done,graphify_done,status,
              created_at,expires_at,finished_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                task_id,
                _hash(token),
                agent_id,
                _request_hash(request, intent, safe_resource),
                request,
                intent or "",
                safe_resource,
                1 if requires_graphify else 0,
                1,
                0,
                0,
                "active",
                created,
                expires,
                None,
            ),
        )
    return {"task_id": task_id, "context_token": token, "expires_at": expires, "requires_graphify": bool(requires_graphify)}


def _load_ready(agent_id: str, task_id: str, context_token: str) -> dict[str, Any]:
    if not agent_id or not task_id or not context_token:
        raise TaskContextError("TASK_CONTEXT_REQUIRED: task_id and context_token are required")
    ensure_schema()
    with connect() as con:
        row = con.execute("SELECT * FROM task_context_v2 WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        raise TaskContextError("TASK_CONTEXT_UNKNOWN_TASK")
    data = dict(row)
    if data["agent_id"] != agent_id:
        raise TaskContextError("TASK_CONTEXT_AGENT_MISMATCH")
    if data["status"] != "active":
        raise TaskContextError("TASK_CONTEXT_EXPIRED_RENEW_REQUIRED")
    if data["expires_at"] < now_ts():
        raise TaskContextError("TASK_CONTEXT_EXPIRED_RENEW_REQUIRED")
    if not hmac.compare_digest(data["token_hash"], _hash(context_token)):
        raise TaskContextError("TASK_CONTEXT_TOKEN_MISMATCH")
    return data


def mark_scribe_done(agent_id: str, task_id: str, context_token: str) -> dict[str, Any]:
    data = _load_ready(agent_id, task_id, context_token)
    with connect() as con:
        con.execute("UPDATE task_context_v2 SET scribe_done=1 WHERE task_id=?", (task_id,))
    data["scribe_done"] = 1
    return {"task_id": task_id, "scribe_done": True, "requires_graphify": bool(data["requires_graphify"])}


def mark_graphify_done(agent_id: str, task_id: str, context_token: str) -> dict[str, Any]:
    data = _load_ready(agent_id, task_id, context_token)
    with connect() as con:
        con.execute("UPDATE task_context_v2 SET graphify_done=1 WHERE task_id=?", (task_id,))
    data["graphify_done"] = 1
    return {"task_id": task_id, "graphify_done": True, "requires_graphify": bool(data["requires_graphify"])}


def require_context_ready(
    agent_id: str,
    task_id: str,
    context_token: str,
    resource: str = "",
    require_graphify: bool | None = None,
    strict_resource: bool = False,
    allowed_intents: set[str] | None = None,
) -> dict[str, Any]:
    data = _load_ready(agent_id, task_id, context_token)
    safe_resource = _safe_resource(resource)
    context_resource = data.get("resource") or ""
    if strict_resource:
        if not safe_resource:
            raise TaskContextError("TASK_CONTEXT_RESOURCE_REQUIRED: action resource is required")
        if not context_resource:
            raise TaskContextError("TASK_CONTEXT_RESOURCE_REQUIRED: task context resource is required")
        if context_resource != safe_resource:
            raise TaskContextError("TASK_CONTEXT_RESOURCE_MISMATCH: resource does not match task context")
    if context_resource and safe_resource and context_resource != safe_resource:
        raise TaskContextError("TASK_CONTEXT_RESOURCE_MISMATCH: resource does not match task context")
    if allowed_intents is not None:
        context_intent = str(data.get("intent") or "").strip().lower()
        normalized_intents = {intent.strip().lower() for intent in allowed_intents}
        if not context_intent:
            raise TaskContextError("TASK_CONTEXT_INTENT_REQUIRED: task context intent is required")
        if context_intent not in normalized_intents:
            raise TaskContextError("READ_INTENT_CANNOT_WRITE" if context_intent == "read" else "TASK_CONTEXT_INTENT_MISMATCH")
    if not data.get("before_done"):
        raise TaskContextError("TASK_CONTEXT_NOT_READY: before_task is not done")
    if not data.get("scribe_done"):
        raise TaskContextError("TASK_CONTEXT_NOT_READY: scribe_query is required")
    graphify_required = bool(data.get("requires_graphify")) if require_graphify is None else bool(require_graphify)
    if graphify_required and not data.get("graphify_done"):
        raise TaskContextError("TASK_CONTEXT_NOT_READY: graphify_query is required")
    return data


def finish_task_context(agent_id: str, task_id: str, context_token: str) -> dict[str, Any]:
    _load_ready(agent_id, task_id, context_token)
    finished = now_ts()
    with connect() as con:
        con.execute(
            "UPDATE task_context_v2 SET status='finished', finished_at=? WHERE task_id=? AND agent_id=?",
            (finished, task_id, agent_id),
        )
    return {"task_id": task_id, "status": "finished", "finished_at": finished}


def list_tasks(agent_id: str = "", status: str = "") -> dict[str, Any]:
    ensure_schema()
    where = []
    params: list[Any] = []
    if agent_id:
        where.append("agent_id=?")
        params.append(agent_id)
    if status:
        where.append("status=?")
        params.append(status)
    query = "SELECT task_id,agent_id,request,intent,resource,requires_graphify,before_done,scribe_done,graphify_done,status,created_at,expires_at,finished_at FROM task_context_v2"
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY created_at DESC"
    with connect() as con:
        rows = [dict(r) for r in con.execute(query, tuple(params)).fetchall()]
    return {"tasks": rows, "count": len(rows)}


def task_status(task_id: str) -> dict[str, Any]:
    if not task_id:
        raise TaskContextError("TASK_CONTEXT_REQUIRED: task_id is required")
    ensure_schema()
    with connect() as con:
        row = con.execute("SELECT task_id,agent_id,request,intent,resource,requires_graphify,before_done,scribe_done,graphify_done,status,created_at,expires_at,finished_at FROM task_context_v2 WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        raise TaskContextError("TASK_CONTEXT_UNKNOWN_TASK")
    return dict(row)


def record_retry(agent_id: str, resource: str, error_code: str, limit: int = 3) -> dict[str, Any]:
    if not agent_id or not error_code:
        return {"verdict": "RETRY_NOT_TRACKED", "count": 0}
    ensure_schema()
    safe_resource = _safe_resource(resource) if resource else "__none__"
    now = db_now_ts()
    retry_id = f"retry-{_hash(agent_id + safe_resource + error_code)[:16]}"
    with connect() as con:
        con.execute(
            """
            INSERT INTO workflow_retry_v1(retry_id,agent_id,resource,error_code,count,first_seen,last_seen)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(agent_id,resource,error_code) DO UPDATE SET
              count=workflow_retry_v1.count+1,
              last_seen=excluded.last_seen
            """,
            (retry_id, agent_id, safe_resource, error_code, 1, now, now),
        )
        row = con.execute("SELECT * FROM workflow_retry_v1 WHERE retry_id=?", (retry_id,)).fetchone()
    data = dict(row)
    verdict = "MAX_WORKFLOW_RETRIES_EXCEEDED" if int(data["count"]) >= limit else "RETRY_RECORDED"
    if int(data["count"]) >= max(2, limit - 1):
        verdict = "RETRY_LOOP_DETECTED" if verdict != "MAX_WORKFLOW_RETRIES_EXCEEDED" else verdict
    data["verdict"] = verdict
    return data


def clear_retry(agent_id: str, resource: str = "") -> None:
    if not agent_id:
        return
    ensure_schema()
    safe_resource = _safe_resource(resource) if resource else "__none__"
    with connect() as con:
        con.execute("DELETE FROM workflow_retry_v1 WHERE agent_id=? AND resource=?", (agent_id, safe_resource))
