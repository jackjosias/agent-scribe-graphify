from __future__ import annotations

"""Task-local discovery evidence for first-write resources.

A missing SCRIBE hit is not permission to write. It is a distinct workflow
state: the agent must bind fresh discovery evidence to the active task, exact
resource, current base hash and Graphify context before any write lock, claim
or patch can proceed.
"""

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from runtime import db, patch_queue

DISCOVERY_TABLE = "task_first_write_discovery_v1"
DISCOVERY_TTL_SECONDS = 900
_MUTATING_INTENTS = {"write", "delete"}
_GENERIC_TOKENS = {
    "file", "files", "code", "main", "index", "core", "utils", "module",
    "component", "components", "page", "service", "config", "data", "docs",
    "public", "static", "assets", "types", "interfaces", "model", "models",
    "view", "controller", "route", "routes", "schema", "state", "status",
    "strategy", "strategies", "implementation", "implementations",
    "technical", "analysis", "drawing", "renderer",
}


class TaskDiscoveryError(RuntimeError):
    def __init__(self, code: str, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


def _now() -> int:
    return int(time.time())


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ensure_schema() -> None:
    db.init_db()
    with db.connect() as con:
        con.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {DISCOVERY_TABLE}(
              task_id TEXT PRIMARY KEY,
              agent_id TEXT NOT NULL,
              resource TEXT NOT NULL,
              scribe_miss INTEGER NOT NULL DEFAULT 0,
              scribe_query_digest TEXT,
              scribe_result_digest TEXT,
              scribe_miss_reason TEXT,
              discovery_done INTEGER NOT NULL DEFAULT 0,
              base_hash TEXT,
              summary TEXT,
              evidence TEXT,
              evidence_digest TEXT,
              proof_id TEXT,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_{DISCOVERY_TABLE}_agent_resource
              ON {DISCOVERY_TABLE}(agent_id,resource,expires_at);
            """
        )


def _task_context_module():
    from runtime import task_context
    return task_context


def _active_task(agent_id: str, task_id: str, context_token: str) -> dict[str, Any]:
    task_context = _task_context_module()
    data = task_context.verify_active_context(agent_id, task_id, context_token)
    canonical = task_context.normalize_intent(str(data.get("intent") or ""))
    data = dict(data)
    data["intent"] = canonical
    if canonical not in _MUTATING_INTENTS:
        raise TaskDiscoveryError(
            "TASK_DISCOVERY_MUTATING_INTENT_REQUIRED",
            {"intent": canonical, "task_id": task_id},
        )
    return data


def _exact_resource(task: dict[str, Any], resource: str) -> str:
    safe = patch_queue.safe_resource(resource)
    stored_raw = str(task.get("resource") or "")
    if not stored_raw:
        raise TaskDiscoveryError("TASK_DISCOVERY_EXACT_RESOURCE_REQUIRED")
    stored = patch_queue.safe_resource(stored_raw)
    if safe != stored:
        task_context = _task_context_module()
        if task_context.is_scope_container(stored) and task_context._scope_contains(stored, safe):
            raise TaskDiscoveryError(
                "TASK_DISCOVERY_SCOPE_TASK_RESOURCE_REQUIRED",
                {
                    "task_resource": stored,
                    "action_resource": safe,
                    "required_internal_action": "scope_task_resource",
                    "reason": "Discovery evidence stays hash-bound to one exact file even when the task starts at directory scope.",
                },
            )
        raise TaskDiscoveryError(
            "TASK_DISCOVERY_RESOURCE_MISMATCH",
            {"task_resource": stored, "action_resource": safe},
        )
    return safe


def _resource_tokens(resource: str) -> set[str]:
    normalized = resource.replace("\\", "/")
    path = Path(normalized)
    stem = path.stem
    camel = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", stem)
    raw_tokens = [stem.lower()]
    raw_tokens.extend(
        token.lower()
        for token in re.split(r"[^A-Za-z0-9]+", camel)
        if token
    )
    if path.parent.name:
        raw_tokens.extend(
            token.lower()
            for token in re.split(r"[^A-Za-z0-9]+", path.parent.name)
            if token
        )
    return {
        token
        for token in raw_tokens
        if len(token) >= 4 and token not in _GENERIC_TOKENS
        and not token.isdigit()
    }


def result_is_relevant(resource: str, stdout: str) -> bool:
    """Require relevance in the SCRIBE result itself, not merely in the query."""

    text = (stdout or "").strip().lower()
    if not text:
        return False
    safe = patch_queue.safe_resource(resource)
    lowered = safe.lower().replace("\\", "/")
    path = Path(lowered)
    basename = path.name
    stem = path.stem
    if lowered in text or basename in text:
        return True
    if len(stem) >= 4 and stem not in _GENERIC_TOKENS and stem in text:
        return True
    tokens = _resource_tokens(safe)
    if not tokens:
        return False
    matches = {token for token in tokens if token in text}
    # One distinctive filename/parent token is enough after generic engine
    # vocabulary has been removed. Multiple matches strengthen the receipt.
    return bool(matches)


def mark_scribe_miss(
    agent_id: str,
    task_id: str,
    context_token: str,
    resource: str,
    *,
    query: str,
    stdout: str,
    reason: str,
) -> dict[str, Any]:
    task = _active_task(agent_id, task_id, context_token)
    safe = _exact_resource(task, resource)
    now = _now()
    expires = min(
        int(task.get("expires_at") or now + DISCOVERY_TTL_SECONDS),
        now + DISCOVERY_TTL_SECONDS,
    )
    query_digest = hashlib.sha256((query or "").encode("utf-8")).hexdigest()
    result_digest = hashlib.sha256((stdout or "").encode("utf-8")).hexdigest()
    ensure_schema()
    with db.connect() as con:
        con.execute(
            f"""
            INSERT INTO {DISCOVERY_TABLE}(
              task_id,agent_id,resource,scribe_miss,scribe_query_digest,
              scribe_result_digest,scribe_miss_reason,discovery_done,
              base_hash,summary,evidence,evidence_digest,proof_id,
              created_at,updated_at,expires_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(task_id) DO UPDATE SET
              agent_id=excluded.agent_id,
              resource=excluded.resource,
              scribe_miss=1,
              scribe_query_digest=excluded.scribe_query_digest,
              scribe_result_digest=excluded.scribe_result_digest,
              scribe_miss_reason=excluded.scribe_miss_reason,
              discovery_done=0,
              base_hash=NULL,
              summary=NULL,
              evidence=NULL,
              evidence_digest=NULL,
              proof_id=NULL,
              updated_at=excluded.updated_at,
              expires_at=excluded.expires_at
            """,
            (
                task_id,
                agent_id,
                safe,
                1,
                query_digest,
                result_digest,
                reason or "SCRIBE returned no resource-relevant historical context",
                0,
                None,
                None,
                None,
                None,
                None,
                now,
                now,
                expires,
            ),
        )
        db.add_event(
            con,
            "task.first_write_scribe_miss",
            {
                "task_id": task_id,
                "resource": safe,
                "query_digest": query_digest,
                "result_digest": result_digest,
            },
            agent_id,
        )
    return {
        "verdict": "SCRIBE_CONTEXT_MISS_FOR_WRITE",
        "task_id": task_id,
        "agent_id": agent_id,
        "resource": safe,
        "scribe_miss": True,
        "query_digest": query_digest,
        "result_digest": result_digest,
        "expires_at": expires,
    }


def clear_scribe_miss(agent_id: str, task_id: str, context_token: str) -> None:
    _active_task(agent_id, task_id, context_token)
    ensure_schema()
    with db.connect() as con:
        con.execute(
            f"DELETE FROM {DISCOVERY_TABLE} WHERE task_id=? AND agent_id=?",
            (task_id, agent_id),
        )


def clear_task(task_id: str, agent_id: str = "") -> None:
    ensure_schema()
    with db.connect() as con:
        if agent_id:
            con.execute(
                f"DELETE FROM {DISCOVERY_TABLE} WHERE task_id=? AND agent_id=?",
                (task_id, agent_id),
            )
        else:
            con.execute(
                f"DELETE FROM {DISCOVERY_TABLE} WHERE task_id=?",
                (task_id,),
            )



def status(task_id: str) -> dict[str, Any]:
    ensure_schema()
    with db.connect() as con:
        row = con.execute(
            f"SELECT * FROM {DISCOVERY_TABLE} WHERE task_id=?",
            (task_id,),
        ).fetchone()
    if not row:
        return {
            "task_id": task_id,
            "scribe_miss": False,
            "discovery_done": False,
            "verdict": "TASK_DISCOVERY_NOT_REQUIRED",
        }
    data = dict(row)
    data["scribe_miss"] = bool(data["scribe_miss"])
    data["discovery_done"] = bool(data["discovery_done"])
    data["verdict"] = (
        "TASK_DISCOVERY_RECORDED"
        if data["discovery_done"]
        else "TASK_DISCOVERY_REQUIRED"
    )
    return data


def scribe_miss_exists(task_id: str) -> bool:
    return bool(status(task_id).get("scribe_miss"))


def _validate_evidence(resource: str, summary: str, evidence: str) -> None:
    clean_summary = (summary or "").strip()
    clean_evidence = (evidence or "").strip()
    if len(clean_summary) < 24:
        raise TaskDiscoveryError("TASK_DISCOVERY_SUMMARY_TOO_SHORT")
    if len(clean_evidence) < 80:
        raise TaskDiscoveryError("TASK_DISCOVERY_EVIDENCE_TOO_SHORT")
    text = f"{clean_summary}\n{clean_evidence}".lower()
    if not any(token in text for token in _resource_tokens(resource)):
        raise TaskDiscoveryError(
            "TASK_DISCOVERY_RESOURCE_EVIDENCE_REQUIRED",
            {"resource": resource},
        )


def record_discovery(
    agent_id: str,
    task_id: str,
    context_token: str,
    resource: str,
    base_hash: str,
    summary: str,
    evidence: str,
) -> dict[str, Any]:
    task = _active_task(agent_id, task_id, context_token)
    safe = _exact_resource(task, resource)
    current = patch_queue.file_hash(safe)
    if not base_hash or base_hash != current.get("hash"):
        raise TaskDiscoveryError(
            "TASK_DISCOVERY_BASE_HASH_MISMATCH",
            {
                "resource": safe,
                "expected_hash": current.get("hash"),
                "provided_hash": base_hash or "",
            },
        )
    if bool(task.get("requires_graphify")) and not bool(task.get("graphify_done")):
        raise TaskDiscoveryError("TASK_DISCOVERY_GRAPHIFY_REQUIRED")
    _validate_evidence(safe, summary, evidence)
    ensure_schema()
    with db.connect() as con:
        row = con.execute(
            f"SELECT * FROM {DISCOVERY_TABLE} WHERE task_id=? AND agent_id=?",
            (task_id, agent_id),
        ).fetchone()
        if not row or not bool(row["scribe_miss"]):
            raise TaskDiscoveryError("TASK_DISCOVERY_SCRIBE_MISS_REQUIRED")
        now = _now()
        expires = min(
            int(task.get("expires_at") or now + DISCOVERY_TTL_SECONDS),
            now + DISCOVERY_TTL_SECONDS,
        )
        proof_payload = {
            "task_id": task_id,
            "agent_id": agent_id,
            "resource": safe,
            "base_hash": base_hash,
            "summary": summary.strip(),
            "evidence": evidence.strip(),
            "scribe_query_digest": row["scribe_query_digest"],
            "scribe_result_digest": row["scribe_result_digest"],
            "created_at": now,
        }
        evidence_digest = _digest(proof_payload)
        proof_id = f"discovery-{evidence_digest[:20]}"
        con.execute(
            f"""
            UPDATE {DISCOVERY_TABLE}
            SET discovery_done=1,base_hash=?,summary=?,evidence=?,
                evidence_digest=?,proof_id=?,updated_at=?,expires_at=?
            WHERE task_id=? AND agent_id=?
            """,
            (
                base_hash,
                summary.strip(),
                evidence.strip(),
                evidence_digest,
                proof_id,
                now,
                expires,
                task_id,
                agent_id,
            ),
        )
        db.add_event(
            con,
            "task.first_write_discovery_recorded",
            {
                "task_id": task_id,
                "resource": safe,
                "base_hash": base_hash,
                "evidence_digest": evidence_digest,
                "proof_id": proof_id,
            },
            agent_id,
        )
    return {
        "ok": True,
        "verdict": "TASK_DISCOVERY_RECORDED",
        "state": "FIRST_WRITE_DISCOVERY_READY",
        "task_id": task_id,
        "agent_id": agent_id,
        "resource": safe,
        "base_hash": base_hash,
        "evidence_digest": evidence_digest,
        "proof_id": proof_id,
        "expires_at": expires,
        "canonical_memory_updated": False,
        "next_action": (
            "request a fresh pre_action_guard and continue through "
            "resource lock, claim and patch queue"
        ),
    }


def require_discovery_ready(
    agent_id: str,
    task_id: str,
    resource: str = "",
) -> dict[str, Any]:
    state = status(task_id)
    if not state.get("scribe_miss"):
        return {"verdict": "TASK_DISCOVERY_NOT_REQUIRED", "task_id": task_id}
    if state.get("agent_id") != agent_id:
        raise TaskDiscoveryError("TASK_DISCOVERY_AGENT_MISMATCH")
    if resource:
        safe = patch_queue.safe_resource(resource)
        if safe != state.get("resource"):
            raise TaskDiscoveryError(
                "TASK_DISCOVERY_RESOURCE_MISMATCH",
                {
                    "task_resource": state.get("resource"),
                    "action_resource": safe,
                },
            )
    if not state.get("discovery_done"):
        raise TaskDiscoveryError(
            "TASK_DISCOVERY_REQUIRED",
            {"task_id": task_id, "resource": state.get("resource")},
        )
    if int(state.get("expires_at") or 0) < _now():
        raise TaskDiscoveryError("TASK_DISCOVERY_EXPIRED")
    current = patch_queue.file_hash(str(state.get("resource") or ""))
    if current.get("hash") != state.get("base_hash"):
        raise TaskDiscoveryError(
            "TASK_DISCOVERY_BASE_STALE",
            {
                "resource": state.get("resource"),
                "recorded_hash": state.get("base_hash"),
                "current_hash": current.get("hash"),
            },
        )
    return state
