from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from runtime import db, discipline, patch_queue, task_context


LOCK_TABLE = "resource_exclusive_locks"
DEFAULT_TTL = 300
MAX_TTL = 900
LOCK_FORBIDDEN = [
    "before_task", "claim_resource", "scribe_query", "graphify_query",
    "propose_patch", "apply_patch", "delete_resource",
    "finish_task", "direct_file_edit",
]


def ensure_schema() -> None:
    db.init_db()
    with db.connect() as con:
        con.executescript(f"""
            CREATE TABLE IF NOT EXISTS {LOCK_TABLE} (
              lock_id TEXT PRIMARY KEY,
              resource TEXT NOT NULL UNIQUE,
              agent_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              mode TEXT NOT NULL DEFAULT 'exclusive',
              created_at REAL NOT NULL,
              expires_at REAL NOT NULL,
              heartbeat_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_{LOCK_TABLE}_resource
              ON {LOCK_TABLE}(resource, expires_at);
            CREATE INDEX IF NOT EXISTS idx_{LOCK_TABLE}_agent
              ON {LOCK_TABLE}(agent_id, expires_at);
        """)


def _canonical_resource(resource: str) -> str:
    if not resource or not resource.strip():
        raise ValueError("PATCH_TARGET_INVALID")
    r = resource.strip()
    if r == "." or r == ".." or "/.." in r or r.startswith("../"):
        raise ValueError("PATCH_TARGET_INVALID")
    if r.endswith("/"):
        raise ValueError("PATCH_TARGET_INVALID")
    root = db.paths()["root"] if hasattr(db, "paths") else Path.cwd()
    full = (root / r).resolve()
    try:
        full.relative_to(root.resolve())
    except ValueError:
        raise ValueError("PATCH_TARGET_INVALID")
    if full.is_dir():
        raise ValueError("PATCH_TARGET_INVALID")
    return r


def _validated_task_context(agent_id: str, task_id: str, context_token: str) -> dict[str, Any]:
    if not task_id:
        return {"ok": False, "verdict": "TASK_CONTEXT_REQUIRED", "state": "HARD_STOP",
                "reason": "task_id is required for resource_lock_claim."}
    if not context_token:
        return {"ok": False, "verdict": "TASK_CONTEXT_TOKEN_REQUIRED", "state": "HARD_STOP",
                "reason": "context_token is required."}
    try:
        st = task_context.task_status(task_id)
    except task_context.TaskContextError as exc:
        return {"ok": False, "verdict": "TASK_CONTEXT_UNKNOWN_TASK", "state": "HARD_STOP",
                "reason": str(exc)}
    if st.get("agent_id") != agent_id:
        return {"ok": False, "verdict": "TASK_AGENT_MISMATCH", "state": "HARD_STOP",
                "reason": f"task_id belongs to '{st.get('agent_id')}' not '{agent_id}'."}
    stored_intent = (st.get("intent") or "").strip().lower()
    if stored_intent in {"read", "query", "ask", "explain", "list", "show", "status"}:
        return {"ok": False, "verdict": "READ_ONLY_LOCK_FORBIDDEN", "state": "HARD_STOP",
                "reason": "Read-only tasks cannot claim write locks.",
                "forbidden": LOCK_FORBIDDEN}
    try:
        task_context.require_context_ready(
            agent_id, task_id, context_token,
            resource="", strict_resource=False,
            allowed_intents=discipline.WRITE_INTENTS,
        )
    except task_context.TaskContextError as exc:
        return {"ok": False, "verdict": str(exc), "state": "HARD_STOP", "reason": str(exc),
                "forbidden": LOCK_FORBIDDEN}
    return {"ok": True, "task_data": st}


def resource_lock_claim(
    agent_id: str,
    resource: str,
    task_id: str = "",
    context_token: str = "",
    ttl_seconds: int = DEFAULT_TTL,
) -> dict[str, Any]:
    if not agent_id:
        return {"ok": False, "verdict": "AGENT_ID_REQUIRED", "state": "AGENT_ID_REQUIRED",
                "forbidden": LOCK_FORBIDDEN}
    try:
        db.require_agent_active(agent_id)
    except db.CoordinationError as exc:
        return {"ok": False, "verdict": str(exc), "state": str(exc), "forbidden": LOCK_FORBIDDEN}
    vtc = _validated_task_context(agent_id, task_id, context_token)
    if not vtc.get("ok"):
        vtc["forbidden"] = LOCK_FORBIDDEN
        return vtc
    try:
        target = _canonical_resource(resource)
    except ValueError:
        return {"ok": False, "verdict": "PATCH_TARGET_INVALID", "state": "HARD_STOP",
                "forbidden": LOCK_FORBIDDEN}
    ttl = max(1, min(int(ttl_seconds), MAX_TTL))
    now = time.time()
    expires_at = now + ttl
    lock_id = _lock_id()
    ensure_schema()
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                f"DELETE FROM {LOCK_TABLE} WHERE resource=? AND expires_at<=?",
                (target, now),
            )
            existing = con.execute(
                f"SELECT * FROM {LOCK_TABLE} WHERE resource=?",
                (target,),
            ).fetchone()
            if existing:
                existing = dict(existing)
                if existing["agent_id"] == agent_id:
                    con.execute(
                        f"UPDATE {LOCK_TABLE} SET expires_at=?, heartbeat_at=? WHERE lock_id=?",
                        (expires_at, now, existing["lock_id"]),
                    )
                    con.execute("COMMIT")
                    return {
                        "ok": True,
                        "verdict": "RESOURCE_LOCK_ACQUIRED",
                        "lock_id": existing["lock_id"],
                        "agent_id": agent_id,
                        "resource": target,
                        "task_id": task_id,
                        "acquired_at": now,
                        "expires_at": expires_at,
                        "status": "active",
                        "note": "existing lock extended",
                    }
                remaining = max(0, int(existing["expires_at"] - now))
                con.execute("COMMIT")
                return {
                    "ok": False,
                    "verdict": "RESOURCE_BUSY",
                    "state": "WAIT_OR_CHOOSE_OTHER_RESOURCE",
                    "owner_agent_id": existing["agent_id"],
                    "ttl_remaining_seconds": remaining,
                    "lock_id": existing["lock_id"],
                    "reason": f"Resource locked by agent '{existing['agent_id']}'.",
                    "forbidden": LOCK_FORBIDDEN,
                }
            con.execute(
                f"INSERT INTO {LOCK_TABLE} (lock_id, resource, agent_id, task_id, mode, created_at, expires_at, heartbeat_at) "
                f"VALUES (?, ?, ?, ?, 'exclusive', ?, ?, ?)",
                (lock_id, target, agent_id, task_id, now, expires_at, now),
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return {
        "ok": True,
        "verdict": "RESOURCE_LOCK_ACQUIRED",
        "lock_id": lock_id,
        "agent_id": agent_id,
        "resource": target,
        "task_id": task_id,
        "acquired_at": now,
        "expires_at": expires_at,
        "status": "active",
    }


def resource_lock_release(
    agent_id: str,
    resource: str,
    lock_id: str = "",
) -> dict[str, Any]:
    if not agent_id:
        return {"ok": False, "verdict": "AGENT_ID_REQUIRED", "state": "AGENT_ID_REQUIRED",
                "forbidden": LOCK_FORBIDDEN}
    try:
        db.require_agent_active(agent_id)
    except db.CoordinationError as exc:
        return {"ok": False, "verdict": str(exc), "state": str(exc), "forbidden": LOCK_FORBIDDEN}
    try:
        target = _canonical_resource(resource)
    except ValueError:
        return {"ok": False, "verdict": "PATCH_TARGET_INVALID", "state": "HARD_STOP",
                "forbidden": LOCK_FORBIDDEN}
    ensure_schema()
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            query = f"SELECT * FROM {LOCK_TABLE} WHERE resource=?"
            params: list[Any] = [target]
            if lock_id:
                query += " AND lock_id=?"
                params.append(lock_id)
            query += " LIMIT 1"
            row = con.execute(query, params).fetchone()
            if not row:
                con.execute("COMMIT")
                return {"ok": True, "verdict": "RESOURCE_LOCK_ALREADY_RELEASED",
                        "agent_id": agent_id, "resource": target}
            if row["agent_id"] != agent_id:
                con.execute("COMMIT")
                return {"ok": False, "verdict": "RESOURCE_LOCK_OWNER_MISMATCH",
                        "state": "HARD_STOP",
                        "owner_agent_id": row["agent_id"],
                        "agent_id": agent_id,
                        "resource": target,
                        "forbidden": LOCK_FORBIDDEN}
            con.execute(
                f"DELETE FROM {LOCK_TABLE} WHERE lock_id=?",
                (row["lock_id"],),
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return {"ok": True, "verdict": "RESOURCE_LOCK_RELEASED",
            "lock_id": row["lock_id"], "agent_id": agent_id, "resource": target}


def resource_lock_heartbeat(
    agent_id: str,
    resource: str,
    ttl_seconds: int = DEFAULT_TTL,
) -> dict[str, Any]:
    if not agent_id:
        return {"ok": False, "verdict": "AGENT_ID_REQUIRED", "state": "AGENT_ID_REQUIRED",
                "forbidden": LOCK_FORBIDDEN}
    try:
        db.require_agent_active(agent_id)
    except db.CoordinationError as exc:
        return {"ok": False, "verdict": str(exc), "state": str(exc), "forbidden": LOCK_FORBIDDEN}
    try:
        target = _canonical_resource(resource)
    except ValueError:
        return {"ok": False, "verdict": "PATCH_TARGET_INVALID", "state": "HARD_STOP",
                "forbidden": LOCK_FORBIDDEN}
    ttl = max(1, min(int(ttl_seconds), MAX_TTL))
    now = time.time()
    expires_at = now + ttl
    ensure_schema()
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute(
                f"SELECT * FROM {LOCK_TABLE} WHERE resource=?",
                (target,),
            ).fetchone()
            if not row:
                con.execute("COMMIT")
                return {"ok": False, "verdict": "RESOURCE_LOCK_NOT_FOUND", "state": "HARD_STOP",
                        "forbidden": LOCK_FORBIDDEN}
            if row["agent_id"] != agent_id:
                con.execute("COMMIT")
                return {"ok": False, "verdict": "RESOURCE_LOCK_OWNER_MISMATCH", "state": "HARD_STOP",
                        "owner_agent_id": row["agent_id"], "forbidden": LOCK_FORBIDDEN}
            if row["expires_at"] <= now:
                con.execute(
                    f"DELETE FROM {LOCK_TABLE} WHERE lock_id=?",
                    (row["lock_id"],),
                )
                con.execute("COMMIT")
                return {"ok": False, "verdict": "RESOURCE_LOCK_EXPIRED", "state": "REACQUIRE_REQUIRED",
                        "reason": "Lock expired. Reacquire before continuing.", "forbidden": LOCK_FORBIDDEN}
            con.execute(
                f"UPDATE {LOCK_TABLE} SET expires_at=?, heartbeat_at=? WHERE lock_id=?",
                (expires_at, now, row["lock_id"]),
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return {"ok": True, "verdict": "RESOURCE_LOCK_EXTENDED",
            "lock_id": row["lock_id"], "agent_id": agent_id, "resource": target,
            "expires_at": expires_at, "ttl_seconds": ttl}


def resource_lock_status(resource: str) -> dict[str, Any]:
    if not resource:
        return {"ok": False, "verdict": "RESOURCE_REQUIRED", "state": "RESOURCE_REQUIRED",
                "forbidden": LOCK_FORBIDDEN}
    try:
        target = _canonical_resource(resource)
    except ValueError:
        return {"ok": False, "verdict": "PATCH_TARGET_INVALID", "state": "HARD_STOP",
                "forbidden": LOCK_FORBIDDEN}
    ensure_schema()
    now = time.time()
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                f"DELETE FROM {LOCK_TABLE} WHERE resource=? AND expires_at<=?",
                (target, now),
            )
            row = con.execute(
                f"SELECT * FROM {LOCK_TABLE} WHERE resource=?",
                (target,),
            ).fetchone()
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    if not row:
        return {"ok": True, "verdict": "RESOURCE_LOCK_FREE",
                "resource": target, "locked": False}
    row = dict(row)
    remaining = max(0, int(row["expires_at"] - now))
    return {"ok": True, "verdict": "RESOURCE_LOCK_HELD",
            "resource": target, "locked": True,
            "lock_id": row["lock_id"],
            "owner_agent_id": row["agent_id"],
            "task_id": row["task_id"],
            "expires_at": row["expires_at"],
            "ttl_remaining_seconds": remaining}


def release_agent_locks(agent_id: str) -> dict[str, Any]:
    if not agent_id:
        return {"ok": False, "verdict": "AGENT_ID_REQUIRED", "state": "AGENT_ID_REQUIRED"}
    ensure_schema()
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                f"DELETE FROM {LOCK_TABLE} WHERE agent_id=?",
                (agent_id,),
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return {"ok": True, "verdict": "AGENT_LOCKS_RELEASED", "agent_id": agent_id}


def _lock_id() -> str:
    return f"rl_{uuid.uuid4().hex[:20]}"


def preflight_apply_patch(
    agent_id: str,
    patch_id: str,
    task_id: str,
    context_token: str,
    action_lease_id: str,
) -> dict[str, Any] | None:
    try:
        db.require_agent_active(agent_id)
    except db.CoordinationError as exc:
        return {"ok": False, "verdict": str(exc), "state": str(exc), "forbidden": LOCK_FORBIDDEN}
    try:
        discipline.validate_action_lease(
            lease_id=action_lease_id, agent_id=agent_id,
            action="apply_patch", task_id=task_id,
        )
    except discipline.DisciplineError as exc:
        return {"ok": False, "verdict": exc.code, "state": str(exc.code),
                "reason": str(exc), "details": exc.details, "forbidden": LOCK_FORBIDDEN}
    try:
        tc = task_context.task_status(task_id)
    except task_context.TaskContextError as exc:
        return {"ok": False, "verdict": "TASK_CONTEXT_UNKNOWN_TASK", "state": "HARD_STOP",
                "reason": str(exc), "forbidden": LOCK_FORBIDDEN}
    if tc.get("agent_id") != agent_id:
        return {"ok": False, "verdict": "TASK_AGENT_MISMATCH", "state": "HARD_STOP",
                "forbidden": LOCK_FORBIDDEN}
    try:
        task_context.require_context_ready(
            agent_id, task_id, context_token,
            resource="", strict_resource=False,
            allowed_intents=discipline.WRITE_INTENTS,
        )
    except task_context.TaskContextError as exc:
        return {"ok": False, "verdict": str(exc), "state": "HARD_STOP",
                "reason": str(exc), "forbidden": LOCK_FORBIDDEN}
    try:
        patch_info = patch_queue.list_patches(target="", status="proposed")
    except Exception as exc:
        return {"ok": False, "verdict": "PATCH_LOOKUP_FAILED", "state": "HARD_STOP",
                "reason": f"patch queue lookup failed: {type(exc).__name__}: {exc}",
                "forbidden": LOCK_FORBIDDEN}
    patch = None
    for p in patch_info.get("patches", []):
        if p["patch_id"] == patch_id and p["agent_id"] == agent_id:
            patch = p
            break
    if not patch:
        return {"ok": False, "verdict": "PATCH_NOT_FOUND", "state": "HARD_STOP",
                "reason": "patch_id not found or not owned by agent.", "forbidden": LOCK_FORBIDDEN}
    target = patch["target_path"]
    try:
        _canonical_resource(target)
    except ValueError:
        return {"ok": False, "verdict": "PATCH_TARGET_INVALID", "state": "HARD_STOP",
                "forbidden": LOCK_FORBIDDEN}
    ensure_schema()
    now = time.time()
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                f"DELETE FROM {LOCK_TABLE} WHERE resource=? AND expires_at<=?",
                (target, now),
            )
            lock = con.execute(
                f"SELECT * FROM {LOCK_TABLE} WHERE resource=?",
                (target,),
            ).fetchone()
            if not lock:
                con.execute("COMMIT")
                return {"ok": False, "verdict": "RESOURCE_LOCK_REQUIRED", "state": "HARD_STOP",
                        "reason": "apply_patch requires an exclusive resource lock on the patch target.",
                        "forbidden": LOCK_FORBIDDEN}
            if lock["agent_id"] != agent_id:
                con.execute("COMMIT")
                return {"ok": False, "verdict": "RESOURCE_LOCK_OWNER_MISMATCH", "state": "HARD_STOP",
                        "owner_agent_id": lock["agent_id"],
                        "reason": "resource lock is owned by another agent.",
                        "forbidden": LOCK_FORBIDDEN}
            if lock["expires_at"] <= now:
                con.execute(
                    f"DELETE FROM {LOCK_TABLE} WHERE lock_id=?",
                    (lock["lock_id"],),
                )
                con.execute("COMMIT")
                return {"ok": False, "verdict": "RESOURCE_LOCK_EXPIRED", "state": "REACQUIRE_REQUIRED",
                        "reason": "lock expired; reacquire before applying patch.",
                        "forbidden": LOCK_FORBIDDEN}
            if lock["expires_at"] - now < 60:
                new_expires = now + DEFAULT_TTL
                con.execute(
                    f"UPDATE {LOCK_TABLE} SET expires_at=?, heartbeat_at=? WHERE lock_id=?",
                    (new_expires, now, lock["lock_id"]),
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    root = db.paths()["root"] if hasattr(db, "paths") else Path.cwd()
    full_path = root / target
    if full_path.exists():
        import hashlib
        current_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()[:64]
        if current_hash != patch["base_hash"]:
            return {"ok": False, "verdict": "PATCH_BASE_STALE", "state": "REVALIDATE_REQUIRED",
                    "reason": "target hash changed since patch proposal; regenerate or rebase patch.",
                    "current_hash": current_hash, "expected_hash": patch["base_hash"],
                    "forbidden": LOCK_FORBIDDEN}
    return None
