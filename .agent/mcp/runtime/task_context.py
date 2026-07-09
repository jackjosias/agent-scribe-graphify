from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
import uuid
from typing import Any

try:
    from . import patch_queue
    from .db import add_event, connect, init_db, now_ts as db_now_ts, require_agent_active
    from .state_paths import project_root_from
except Exception:
    import patch_queue  # type: ignore
    from db import add_event, connect, init_db, now_ts as db_now_ts, require_agent_active  # type: ignore
    from state_paths import project_root_from  # type: ignore

DEFAULT_TTL_SECONDS = 900


class TaskContextError(RuntimeError):
    def __init__(self, code: str, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


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
              scribe_record_done INTEGER NOT NULL DEFAULT 0,
              scribe_record_required INTEGER NOT NULL DEFAULT 0,
              scribe_record_policy TEXT,
              scribe_record_path TEXT,
              scribe_record_digest TEXT,
              scribe_record_promoted INTEGER NOT NULL DEFAULT 0,
              scribe_record_entry_id TEXT,
              scribe_record_skip_reason TEXT,
              graphify_done INTEGER NOT NULL DEFAULT 0,
              memory_hash TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              finished_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_task_context_v2_agent_status
              ON task_context_v2(agent_id,status,expires_at);
            CREATE INDEX IF NOT EXISTS idx_task_context_v2_agent_resource
              ON task_context_v2(agent_id,resource,status,request_hash);
            """)
        try:
            con.execute("ALTER TABLE task_context_v2 ADD COLUMN memory_hash TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            con.execute("ALTER TABLE task_context_v2 ADD COLUMN scribe_result_count INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            con.execute("ALTER TABLE task_context_v2 ADD COLUMN scribe_result_resources TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        for ddl in (
            "ALTER TABLE task_context_v2 ADD COLUMN scribe_record_done INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE task_context_v2 ADD COLUMN scribe_record_required INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE task_context_v2 ADD COLUMN scribe_record_policy TEXT",
            "ALTER TABLE task_context_v2 ADD COLUMN scribe_record_path TEXT",
            "ALTER TABLE task_context_v2 ADD COLUMN scribe_record_digest TEXT",
            "ALTER TABLE task_context_v2 ADD COLUMN scribe_record_promoted INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE task_context_v2 ADD COLUMN scribe_record_entry_id TEXT",
            "ALTER TABLE task_context_v2 ADD COLUMN scribe_record_skip_reason TEXT",
        ):
            try:
                con.execute(ddl)
            except sqlite3.OperationalError:
                pass
        con.executescript("""

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


def _normalize_intent(intent: str) -> str:
    value = (intent or "").strip().lower()
    if value in {"write", "edit", "patch", "modify", "code", "fix", "refactor", "test", "create"}:
        return "write"
    if value in {"delete", "remove"}:
        return "delete"
    if value in {"read", "inspect", "query"}:
        return "read"
    return value


MEMOIRE_FILE = "AGENT-MEMOIRE_PROJECT_STATUS.scribe"


def _compute_memory_hash() -> str | None:
    root = project_root_from()
    memo = root / MEMOIRE_FILE
    if not memo.is_file():
        return None
    return hashlib.sha256(memo.read_bytes()).hexdigest()


def _task_public(row: Any) -> dict[str, Any]:
    try:
        memory_hash = row["memory_hash"]
    except (KeyError, IndexError, TypeError):
        memory_hash = None
    return {
        "task_id": row["task_id"],
        "agent_id": row["agent_id"],
        "request": row["request"],
        "intent": row["intent"],
        "resource": row["resource"],
        "requires_graphify": bool(row["requires_graphify"]),
        "before_done": bool(row["before_done"]),
        "scribe_done": bool(row["scribe_done"]),
        "scribe_record_done": bool(row["scribe_record_done"]),
        "scribe_record_required": bool(row["scribe_record_required"]),
        "scribe_record_policy": row["scribe_record_policy"],
        "scribe_record_path": row["scribe_record_path"],
        "scribe_record_digest": row["scribe_record_digest"],
        "scribe_record_promoted": bool(row["scribe_record_promoted"]),
        "scribe_record_entry_id": row["scribe_record_entry_id"],
        "scribe_record_skip_reason": row["scribe_record_skip_reason"],
        "graphify_done": bool(row["graphify_done"]),
        "memory_hash": memory_hash,
        "status": row["status"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "finished_at": row["finished_at"],
    }


def find_active_task(agent_id: str, intent: str, resource: str) -> dict[str, Any] | None:
    if not agent_id:
        return None
    ensure_schema()
    safe_resource = _safe_resource(resource)
    normalized = _normalize_intent(intent)
    current = now_ts()
    with connect() as con:
        rows = con.execute(
            """
            SELECT * FROM task_context_v2
            WHERE agent_id=? AND resource=? AND status='active' AND expires_at>=?
            ORDER BY created_at DESC
            """,
            (agent_id, safe_resource, current),
        ).fetchall()
    for row in rows:
        if _normalize_intent(row["intent"] or "") == normalized:
            return _task_public(row)
    return None


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
    require_agent_active(agent_id)
    ensure_schema()
    safe_resource = _safe_resource(resource)
    existing = find_active_task(agent_id, intent, safe_resource)
    if existing:
        with connect() as con:
            add_event(con, "task.active_exists", {"task_id": existing["task_id"], "intent": _normalize_intent(intent), "resource": safe_resource}, agent_id)
        raise TaskContextError("ACTIVE_TASK_EXISTS", {"task_id": existing["task_id"], "task": existing})
    token = secrets.token_urlsafe(32)
    task_id = f"task-{uuid.uuid4().hex[:16]}"
    created = now_ts()
    ttl = ttl_seconds if ttl_seconds is not None else globals()["ttl_seconds"]()
    expires = created + ttl
    with connect() as con:
        con.execute(
            """
            INSERT INTO task_context_v2(
              task_id,token_hash,agent_id,request_hash,request,intent,resource,
              requires_graphify,before_done,scribe_done,graphify_done,memory_hash,status,
              created_at,expires_at,finished_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                None,
                "active",
                created,
                expires,
                None,
            ),
        )
        add_event(con, "task.context_created", {"task_id": task_id, "intent": _normalize_intent(intent), "resource": safe_resource}, agent_id)
    return {"task_id": task_id, "context_token": token, "expires_at": expires, "requires_graphify": bool(requires_graphify)}


def resume_task_context(agent_id: str, task_id: str) -> dict[str, Any]:
    if not agent_id:
        raise TaskContextError("TASK_CONTEXT_REQUIRED: agent_id is required")
    if not task_id:
        raise TaskContextError("TASK_CONTEXT_REQUIRED: task_id is required")
    require_agent_active(agent_id)
    ensure_schema()
    token = secrets.token_urlsafe(32)
    refreshed = now_ts()
    expires = refreshed + ttl_seconds()
    with connect() as con:
        row = con.execute("SELECT * FROM task_context_v2 WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise TaskContextError("TASK_CONTEXT_UNKNOWN_TASK")
        data = dict(row)
        if data["agent_id"] != agent_id:
            raise TaskContextError("TASK_CONTEXT_AGENT_MISMATCH")
        if data["status"] != "active" or data["expires_at"] < refreshed:
            raise TaskContextError("TASK_CONTEXT_EXPIRED_RENEW_REQUIRED")
        con.execute(
            "UPDATE task_context_v2 SET token_hash=?, expires_at=? WHERE task_id=? AND agent_id=?",
            (_hash(token), expires, task_id, agent_id),
        )
        add_event(con, "task.context_resumed", {"task_id": task_id, "expires_at": expires}, agent_id)
    return {"task_id": task_id, "context_token": token, "status": "active", "expires_at": expires}


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


def mark_scribe_done(agent_id: str, task_id: str, context_token: str, result_count: int = 0, result_resources: str = "") -> dict[str, Any]:
    data = _load_ready(agent_id, task_id, context_token)
    memory_hash = _compute_memory_hash()
    with connect() as con:
        con.execute(
            "UPDATE task_context_v2 SET scribe_done=1, memory_hash=?, scribe_result_count=?, scribe_result_resources=? WHERE task_id=?",
            (memory_hash, result_count, result_resources, task_id),
        )
    data["scribe_done"] = 1
    data["memory_hash"] = memory_hash
    data["scribe_result_count"] = result_count
    data["scribe_result_resources"] = result_resources
    return {
        "task_id": task_id,
        "scribe_done": True,
        "memory_hash": memory_hash,
        "requires_graphify": bool(data["requires_graphify"]),
        "scribe_result_count": result_count,
        "scribe_result_resources": result_resources,
    }


def mark_scribe_record_staged(
    agent_id: str,
    task_id: str,
    context_token: str,
    *,
    required: bool,
    policy: str,
    record_path: str,
    record_digest: str,
) -> dict[str, Any]:
    data = _load_ready(agent_id, task_id, context_token)
    with connect() as con:
        con.execute(
            """
            UPDATE task_context_v2
            SET scribe_record_done=1,
                scribe_record_required=?,
                scribe_record_policy=?,
                scribe_record_path=?,
                scribe_record_digest=?,
                scribe_record_promoted=0,
                scribe_record_entry_id=NULL,
                scribe_record_skip_reason=NULL
            WHERE task_id=?
            """,
            (1 if required else 0, policy or "", record_path or "", record_digest or "", task_id),
        )
    data.update({
        "scribe_record_done": 1,
        "scribe_record_required": 1 if required else 0,
        "scribe_record_policy": policy or "",
        "scribe_record_path": record_path or "",
        "scribe_record_digest": record_digest or "",
        "scribe_record_promoted": 0,
        "scribe_record_entry_id": "",
        "scribe_record_skip_reason": "",
    })
    return {
        "task_id": task_id,
        "scribe_record_done": True,
        "scribe_record_required": bool(required),
        "scribe_record_policy": policy or "",
        "scribe_record_path": record_path or "",
        "scribe_record_digest": record_digest or "",
        "scribe_record_promoted": False,
        "requires_graphify": bool(data["requires_graphify"]),
    }


def mark_scribe_record_promoted(
    agent_id: str,
    task_id: str,
    context_token: str,
    *,
    entry_id: str,
) -> dict[str, Any]:
    data = _load_ready(agent_id, task_id, context_token)
    with connect() as con:
        con.execute(
            """
            UPDATE task_context_v2
            SET scribe_record_promoted=1,
                scribe_record_entry_id=?,
                scribe_record_skip_reason=NULL
            WHERE task_id=?
            """,
            (entry_id or "", task_id),
        )
    data.update({
        "scribe_record_promoted": 1,
        "scribe_record_entry_id": entry_id or "",
        "scribe_record_skip_reason": "",
    })
    return {
        "task_id": task_id,
        "scribe_record_promoted": True,
        "scribe_record_entry_id": entry_id or "",
    }


def mark_scribe_record_skipped(
    agent_id: str,
    task_id: str,
    context_token: str,
    *,
    skip_reason: str,
) -> dict[str, Any]:
    data = _load_ready(agent_id, task_id, context_token)
    with connect() as con:
        con.execute(
            """
            UPDATE task_context_v2
            SET scribe_record_required=0,
                scribe_record_skip_reason=?
            WHERE task_id=?
            """,
            (skip_reason or "", task_id),
        )
    data.update({
        "scribe_record_required": 0,
        "scribe_record_skip_reason": skip_reason or "",
    })
    return {
        "task_id": task_id,
        "scribe_record_required": False,
        "scribe_record_skip_reason": skip_reason or "",
    }


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
    stored_memory_hash = data.get("memory_hash")
    if stored_memory_hash:
        current_memory_hash = _compute_memory_hash()
        if current_memory_hash and current_memory_hash != stored_memory_hash:
            with connect() as con:
                con.execute(
                    "UPDATE task_context_v2 SET scribe_done=0, memory_hash=NULL WHERE task_id=?",
                    (task_id,),
                )
            raise TaskContextError(
                "TASK_CONTEXT_NOT_READY: scribe_query is required (memory changed since scribe_query)"
            )
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
    query = "SELECT task_id,agent_id,request,intent,resource,requires_graphify,before_done,scribe_done,scribe_record_done,scribe_record_required,scribe_record_policy,scribe_record_path,scribe_record_digest,scribe_record_promoted,scribe_record_entry_id,scribe_record_skip_reason,graphify_done,memory_hash,scribe_result_count,scribe_result_resources,status,created_at,expires_at,finished_at FROM task_context_v2"
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
        row = con.execute("SELECT task_id,agent_id,request,intent,resource,requires_graphify,before_done,scribe_done,scribe_record_done,scribe_record_required,scribe_record_policy,scribe_record_path,scribe_record_digest,scribe_record_promoted,scribe_record_entry_id,scribe_record_skip_reason,graphify_done,memory_hash,scribe_result_count,scribe_result_resources,status,created_at,expires_at,finished_at FROM task_context_v2 WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        raise TaskContextError("TASK_CONTEXT_UNKNOWN_TASK")
    return dict(row)


def get_task_context(agent_id: str, task_id: str) -> dict[str, Any]:
    if not agent_id or not task_id:
        raise TaskContextError("TASK_CONTEXT_REQUIRED: agent_id and task_id are required")
    ensure_schema()
    with connect() as con:
        row = con.execute(
            "SELECT * FROM task_context_v2 WHERE task_id=? AND agent_id=?",
            (task_id, agent_id),
        ).fetchone()
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
