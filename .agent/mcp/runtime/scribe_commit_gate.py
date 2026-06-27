from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

try:
    from . import task_context
    from .db import add_event, connect, init_db
except Exception:
    import task_context  # type: ignore
    from db import add_event, connect, init_db  # type: ignore

ALLOWED_DECISIONS = {"commit", "edit", "skip"}
ALLOWED_RECORD_TYPES = {"TASK_SUMMARY", "DECISION_RATIONALE", "ERROR_ROLLBACK", "SKIP"}
PENDING = "pending"
COMMITTED = "committed"
SKIPPED = "skipped"


class ScribeCommitGateError(RuntimeError):
    def __init__(self, code: str, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


def ensure_schema() -> None:
    init_db()
    with connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS scribe_commit_gate_v1(
              gate_id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL UNIQUE,
              agent_id TEXT NOT NULL,
              token_hash TEXT NOT NULL,
              status TEXT NOT NULL,
              proposed_record_json TEXT NOT NULL,
              final_record_json TEXT,
              decision TEXT,
              skip_reason TEXT,
              scribe_record_path TEXT,
              created_at INTEGER NOT NULL,
              resolved_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_scribe_commit_gate_v1_agent_status
              ON scribe_commit_gate_v1(agent_id,status,created_at);
            """
        )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _gate_id(task_id: str) -> str:
    return "scg-" + _hash(task_id)[:20]


def is_mutating_intent(intent: str) -> bool:
    return (intent or "").strip().lower() in {
        "write", "edit", "patch", "modify", "code", "fix", "refactor",
        "test", "create", "delete", "remove", "decision",
    }


def build_proposed_record(task: dict[str, Any], summary: str = "") -> dict[str, Any]:
    resource = str(task.get("resource") or "")
    return {
        "type": "TASK_SUMMARY",
        "task_id": str(task.get("task_id") or ""),
        "agent_id": str(task.get("agent_id") or ""),
        "summary": summary or str(task.get("request") or "task completed"),
        "touched_resources": [resource] if resource else [],
        "verdict": "PENDING_HUMAN_MEMORY_DECISION",
        "tags": ["scribe_commit_gate"],
    }


def _public(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    proposed = json.loads(data["proposed_record_json"])
    final = json.loads(data["final_record_json"]) if data.get("final_record_json") else None
    result = {
        "gate_id": data["gate_id"],
        "task_id": data["task_id"],
        "agent_id": data["agent_id"],
        "status": data["status"],
        "proposed_record": proposed,
        "allowed_decisions": sorted(ALLOWED_DECISIONS),
        "created_at": data["created_at"],
        "resolved_at": data.get("resolved_at"),
    }
    if data.get("decision"):
        result["decision"] = data["decision"]
    if data.get("skip_reason"):
        result["skip_reason"] = data["skip_reason"]
    if data.get("scribe_record_path"):
        result["scribe_record_path"] = data["scribe_record_path"]
    if final is not None:
        result["final_record"] = final
    return result


def _load_gate(task_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with connect() as con:
        row = con.execute("SELECT * FROM scribe_commit_gate_v1 WHERE task_id=?", (task_id,)).fetchone()
    return _public(row)


def _validate_task(agent_id: str, task_id: str, context_token: str) -> dict[str, Any]:
    try:
        task_context.require_context_ready(agent_id, task_id, context_token)
        task = task_context.task_status(task_id)
    except task_context.TaskContextError as exc:
        raise ScribeCommitGateError(exc.code, exc.details) from exc
    if task.get("agent_id") != agent_id:
        raise ScribeCommitGateError("TASK_CONTEXT_AGENT_MISMATCH")
    if not is_mutating_intent(str(task.get("intent") or "")):
        raise ScribeCommitGateError("SCRIBE_COMMIT_GATE_NOT_REQUIRED")
    return task


def get_status(agent_id: str, task_id: str, context_token: str) -> dict[str, Any]:
    _validate_task(agent_id, task_id, context_token)
    gate = _load_gate(task_id)
    if gate is None:
        return {"verdict": "SCRIBE_COMMIT_GATE_NOT_CREATED", "status": "missing", "task_id": task_id}
    return {"verdict": "SCRIBE_COMMIT_GATE_STATUS", **gate}


def require_or_create(agent_id: str, task_id: str, context_token: str, summary: str = "") -> dict[str, Any]:
    task = _validate_task(agent_id, task_id, context_token)
    ensure_schema()
    now = int(time.time())
    gate_id = _gate_id(task_id)
    proposed = build_proposed_record(task, summary=summary)
    with connect() as con:
        con.execute(
            """
            INSERT OR IGNORE INTO scribe_commit_gate_v1(
              gate_id,task_id,agent_id,token_hash,status,proposed_record_json,created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (gate_id, task_id, agent_id, _hash(context_token), PENDING, json.dumps(proposed, ensure_ascii=False, sort_keys=True), now),
        )
        row = con.execute("SELECT * FROM scribe_commit_gate_v1 WHERE task_id=?", (task_id,)).fetchone()
        add_event(con, "scribe_commit_gate.required", {"gate_id": gate_id, "task_id": task_id, "status": row["status"]}, agent_id)
    gate = _public(row)
    if gate is None:
        raise ScribeCommitGateError("SCRIBE_COMMIT_GATE_CREATE_FAILED")
    return gate


def _validate_record(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ScribeCommitGateError("SCRIBE_COMMIT_GATE_RECORD_REQUIRED")
    record_type = str(record.get("type") or "TASK_SUMMARY")
    if record_type not in ALLOWED_RECORD_TYPES:
        raise ScribeCommitGateError("SCRIBE_COMMIT_GATE_RECORD_TYPE_INVALID")
    for field in ("task_id", "agent_id", "summary", "touched_resources", "verdict", "tags"):
        if field not in record:
            raise ScribeCommitGateError("SCRIBE_COMMIT_GATE_RECORD_INVALID", {"missing": field})
    if not isinstance(record.get("touched_resources"), list) or not isinstance(record.get("tags"), list):
        raise ScribeCommitGateError("SCRIBE_COMMIT_GATE_RECORD_INVALID")
    return record


def resolve(
    agent_id: str,
    task_id: str,
    context_token: str,
    decision: str,
    record_writer: Callable[[dict[str, Any]], dict[str, Any]],
    edited_record: dict[str, Any] | None = None,
    skip_reason: str = "",
) -> dict[str, Any]:
    _validate_task(agent_id, task_id, context_token)
    normalized = (decision or "").strip().lower()
    if normalized not in ALLOWED_DECISIONS:
        raise ScribeCommitGateError("SCRIBE_COMMIT_GATE_DECISION_INVALID")
    gate = _load_gate(task_id)
    if gate is None:
        raise ScribeCommitGateError("SCRIBE_COMMIT_GATE_NOT_CREATED")
    if gate["agent_id"] != agent_id:
        raise ScribeCommitGateError("TASK_CONTEXT_AGENT_MISMATCH")
    if gate["status"] in {COMMITTED, SKIPPED}:
        return {"verdict": "SCRIBE_COMMIT_GATE_ALREADY_RESOLVED", **gate}
    proposed = gate["proposed_record"]
    final_record = proposed
    record_path = ""
    if normalized == "skip":
        if not skip_reason or not skip_reason.strip():
            raise ScribeCommitGateError("SCRIBE_COMMIT_GATE_SKIP_REASON_REQUIRED")
        status = SKIPPED
        final_record = {**proposed, "type": "SKIP", "verdict": "MEMORY_SKIPPED", "skip_reason": skip_reason.strip()}
    else:
        if normalized == "edit":
            if edited_record is None:
                raise ScribeCommitGateError("SCRIBE_COMMIT_GATE_EDITED_RECORD_REQUIRED")
            final_record = _validate_record(edited_record)
        else:
            final_record = _validate_record(proposed)
        written = record_writer(final_record)
        record_path = str(written.get("record") or "")
        status = COMMITTED
    now = int(time.time())
    with connect() as con:
        con.execute(
            """
            UPDATE scribe_commit_gate_v1
            SET status=?, decision=?, skip_reason=?, final_record_json=?, scribe_record_path=?, resolved_at=?
            WHERE task_id=? AND status=?
            """,
            (status, normalized, skip_reason.strip(), json.dumps(final_record, ensure_ascii=False, sort_keys=True), record_path, now, task_id, PENDING),
        )
        row = con.execute("SELECT * FROM scribe_commit_gate_v1 WHERE task_id=?", (task_id,)).fetchone()
        add_event(con, "scribe_commit_gate.resolved", {"gate_id": gate["gate_id"], "task_id": task_id, "decision": normalized, "status": status}, agent_id)
    resolved = _public(row)
    if resolved is None:
        raise ScribeCommitGateError("SCRIBE_COMMIT_GATE_RESOLVE_FAILED")
    return {"verdict": "SCRIBE_COMMIT_GATE_RESOLVED", **resolved}
