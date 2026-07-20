from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from runtime import canonical_memory_gate, db


ADMISSION_TABLE = "tenor_memory_admission_v1"
SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js", ".jsx",
    ".kt", ".kts", ".php", ".py", ".rb", ".rs", ".scala", ".sql", ".swift",
    ".ts", ".tsx", ".vue", ".svelte",
}
DURABLE_TERMS = {
    "bug", "fix", "regression", "feature", "security", "migration", "schema", "refactor",
    "architecture", "decision", "portability", "race", "deadlock", "rollback", "incident",
}
UNRESOLVED_TERMS = {"choose between", "decision pending", "user approval", "unsure whether"}


def _now() -> int:
    return int(time.time())


def ensure_schema(project_root: Path) -> None:
    root = project_root.resolve()
    db.init_db(root)
    with db.connect(root) as con:
        con.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {ADMISSION_TABLE}(
              task_id TEXT PRIMARY KEY,
              agent_id TEXT NOT NULL,
              decision TEXT NOT NULL,
              status TEXT NOT NULL,
              reason TEXT NOT NULL,
              record_path TEXT NOT NULL,
              entry_id TEXT NOT NULL DEFAULT '',
              details_json TEXT NOT NULL DEFAULT '{{}}',
              created_at INTEGER NOT NULL,
              resolved_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_{ADMISSION_TABLE}_agent_status
              ON {ADMISSION_TABLE}(agent_id,status,created_at);
            """
        )


def classify_outcome(
    *,
    objective: str,
    intent: str,
    summary: str,
    files: list[dict[str, Any]],
    validators: list[dict[str, Any]],
    canonical_memory_active: bool,
) -> dict[str, Any]:
    normalized_intent = (intent or "").strip().lower()
    if normalized_intent == "read":
        return {
            "decision": "runtime_only",
            "reason": "Read-only observation produced no durable project mutation requiring canonical memory.",
        }
    if normalized_intent not in {"write", "delete"} or not files:
        return {
            "decision": "conflict",
            "reason": "A mutating memory admission requires a committed changeset with at least one file receipt.",
        }
    if not validators or any(item.get("ok") is not True or int(item.get("returncode", 1)) != 0 for item in validators):
        return {
            "decision": "conflict",
            "reason": "Canonical memory cannot certify a mutation without successful validator evidence.",
        }
    text = " ".join([objective or "", summary or ""]).lower()
    if any(term in text for term in UNRESOLVED_TERMS):
        return {
            "decision": "ask_user",
            "reason": "The outcome contains an unresolved architectural choice and cannot be canonized without user approval.",
        }
    if not canonical_memory_active:
        return {
            "decision": "runtime_only",
            "reason": "Canonical SCRIBE memory is not installed in this project; the auditable runtime receipt is retained locally.",
        }
    source_change = any(Path(str(item.get("path") or "")).suffix.lower() in SOURCE_SUFFIXES for item in files)
    deletion = normalized_intent == "delete" or any(item.get("operation") == "delete" for item in files)
    durable_language = any(term in text for term in DURABLE_TERMS)
    if source_change or deletion or durable_language:
        return {
            "decision": "promote",
            "reason": "Validated durable source, deletion, architecture, or regression knowledge must be retrievable by future agents.",
        }
    return {
        "decision": "runtime_only",
        "reason": "Validated non-source maintenance has no durable architectural or causal knowledge; runtime evidence is sufficient.",
    }


def _store(
    root: Path,
    task_id: str,
    agent_id: str,
    decision: str,
    status: str,
    reason: str,
    record_path: str,
    entry_id: str,
    details: dict[str, Any],
) -> None:
    ensure_schema(root)
    with db.connect(root) as con:
        con.execute(
            f"""
            INSERT INTO {ADMISSION_TABLE}(task_id,agent_id,decision,status,reason,record_path,entry_id,details_json,created_at,resolved_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(task_id) DO UPDATE SET
              decision=excluded.decision,status=excluded.status,reason=excluded.reason,
              record_path=excluded.record_path,entry_id=excluded.entry_id,details_json=excluded.details_json,
              resolved_at=excluded.resolved_at
            """,
            (
                task_id,
                agent_id,
                decision,
                status,
                reason,
                record_path,
                entry_id,
                json.dumps(details, ensure_ascii=False, sort_keys=True),
                _now(),
                _now() if status == "resolved" else None,
            ),
        )
        db.add_event(
            con,
            "tenor.memory_admission",
            {"task_id": task_id, "decision": decision, "status": status, "reason": reason},
            agent_id,
        )


def get_admission(project_root: Path, task_id: str, agent_id: str = "") -> dict[str, Any] | None:
    root = project_root.resolve()
    ensure_schema(root)
    with db.connect(root) as con:
        row = con.execute(f"SELECT * FROM {ADMISSION_TABLE} WHERE task_id=?", (task_id,)).fetchone()
    if not row or (agent_id and row["agent_id"] != agent_id):
        return None
    result = dict(row)
    result["details"] = json.loads(result.pop("details_json") or "{}")
    return result


def admit_runtime_record(
    *,
    project_root: Path,
    task_id: str,
    agent_id: str,
    objective: str,
    intent: str,
    summary: str,
    files: list[dict[str, Any]],
    validators: list[dict[str, Any]],
    record: dict[str, Any],
    record_path: Path,
    scope: str,
) -> dict[str, Any]:
    root = project_root.resolve()
    existing = get_admission(root, task_id, agent_id)
    if existing:
        return {"ok": existing["status"] == "resolved", "verdict": "TENOR_MEMORY_ADMISSION_EXISTS", **existing}
    classification = classify_outcome(
        objective=objective,
        intent=intent,
        summary=summary,
        files=files,
        validators=validators,
        canonical_memory_active=canonical_memory_gate.is_active(root),
    )
    decision = classification["decision"]
    reason = classification["reason"]
    relative_record = str(record_path.relative_to(root)) if record_path.is_relative_to(root) else str(record_path)
    if decision == "conflict":
        _store(root, task_id, agent_id, decision, "blocked", reason, relative_record, "", {})
        return {"ok": False, "verdict": "TENOR_MEMORY_ADMISSION_CONFLICT", "decision": decision, "reason": reason}
    if decision == "ask_user":
        _store(root, task_id, agent_id, decision, "awaiting_user", reason, relative_record, "", {})
        return {
            "ok": False,
            "verdict": "TENOR_MEMORY_ADMISSION_USER_DECISION_REQUIRED",
            "decision": decision,
            "reason": reason,
            "next_action": "tenor_task_control:memory_promote_or_memory_skip",
        }
    promotion: dict[str, Any] = {}
    entry_id = ""
    if decision == "promote":
        promotion = canonical_memory_gate.promote_record(
            root,
            record,
            record_path,
            scope=scope,
            memory_policy="canonical_required",
            agent_id=agent_id,
            task_id=task_id,
        )
        if not promotion.get("ok"):
            failure = str(promotion.get("reason") or promotion.get("verdict") or "canonical promotion failed")
            _store(root, task_id, agent_id, "conflict", "blocked", failure, relative_record, "", promotion)
            return {
                "ok": False,
                "verdict": "TENOR_MEMORY_ADMISSION_PROMOTION_FAILED",
                "decision": "conflict",
                "reason": failure,
                "promotion": promotion,
            }
        entry_id = str(promotion.get("entry_id") or "")
    _store(root, task_id, agent_id, decision, "resolved", reason, relative_record, entry_id, promotion)
    return {
        "ok": True,
        "verdict": "TENOR_MEMORY_ADMISSION_PROMOTED" if decision == "promote" else "TENOR_MEMORY_ADMISSION_RUNTIME_ONLY",
        "decision": decision,
        "reason": reason,
        "entry_id": entry_id,
        "record_path": relative_record,
        "promotion": promotion,
    }


def resolve_pending(
    project_root: Path,
    task_id: str,
    agent_id: str,
    decision: str,
    reason: str = "",
) -> dict[str, Any]:
    root = project_root.resolve()
    admission = get_admission(root, task_id, agent_id)
    if not admission or admission["status"] != "awaiting_user":
        return {"ok": False, "verdict": "TENOR_MEMORY_ADMISSION_NOT_PENDING", "task_id": task_id}
    normalized = (decision or "").strip().lower()
    if normalized == "runtime_only":
        clean_reason = " ".join((reason or "").split())
        if len(clean_reason) < 24:
            return {"ok": False, "verdict": "TENOR_MEMORY_ADMISSION_SKIP_REASON_REQUIRED", "task_id": task_id}
        _store(root, task_id, agent_id, "runtime_only", "resolved", clean_reason, admission["record_path"], "", {})
        return {"ok": True, "verdict": "TENOR_MEMORY_ADMISSION_RUNTIME_ONLY", "decision": "runtime_only", "reason": clean_reason}
    if normalized != "promote":
        return {"ok": False, "verdict": "TENOR_MEMORY_ADMISSION_DECISION_INVALID", "task_id": task_id}
    record_path = root / admission["record_path"]
    if not record_path.is_file():
        return {"ok": False, "verdict": "TENOR_MEMORY_ADMISSION_RECORD_MISSING", "task_id": task_id}
    record = json.loads(record_path.read_text(encoding="utf-8"))
    promoted = canonical_memory_gate.promote_record(
        root,
        record,
        record_path,
        scope="",
        memory_policy="canonical_required",
        agent_id=agent_id,
        task_id=task_id,
    )
    if not promoted.get("ok"):
        return {"ok": False, "verdict": "TENOR_MEMORY_ADMISSION_PROMOTION_FAILED", "promotion": promoted}
    entry_id = str(promoted.get("entry_id") or "")
    _store(root, task_id, agent_id, "promote", "resolved", reason or "User approved canonical promotion.", admission["record_path"], entry_id, promoted)
    return {"ok": True, "verdict": "TENOR_MEMORY_ADMISSION_PROMOTED", "decision": "promote", "entry_id": entry_id}
