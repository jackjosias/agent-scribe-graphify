from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

try:
    from . import db
except Exception:
    import db  # type: ignore

try:
    _SCRIBE_SCRIPTS = Path(__file__).resolve().parents[2] / "workflow" / "scribe" / "sel" / "scripts"
    if str(_SCRIBE_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIBE_SCRIPTS))
    from scribe_store import load_scribe  # type: ignore
except Exception:
    load_scribe = None  # type: ignore

CANONICAL_MEMORY_REQUIRED = "CANONICAL_MEMORY_REQUIRED"
CANONICAL_MEMORY_PROMOTED = "CANONICAL_MEMORY_PROMOTED"
CANONICAL_MEMORY_SKIPPED_WITH_REASON = "CANONICAL_MEMORY_SKIPPED_WITH_REASON"
CANONICAL_MEMORY_SKIP_REJECTED = "CANONICAL_MEMORY_SKIP_REJECTED"
CANONICAL_MEMORY_DISABLED = "CANONICAL_MEMORY_DISABLED"

MUTATING_INTENTS = {"write", "edit", "patch", "modify", "code", "fix", "refactor", "test", "create", "delete", "remove", "decision", "build"}
PROMOTION_REQUIRED_HINTS = (
    "bug",
    "feature",
    "refactor",
    "decision",
    "ui",
    "ux",
    "feedback",
    "regression",
    "dette",
    "piège",
    "trap",
    "problem",
    "fix",
)
WEAK_SKIP_REASONS = {
    "not needed",
    "minor",
    "ok",
    "none",
    "no memory",
    "n/a",
    "just a change",
    "pas nécessaire",
    "petit changement",
}


def _now() -> int:
    return int(time.time())


def _project_root(project_root: Path | None = None) -> Path:
    return (project_root or Path.cwd()).resolve()


def _scribe_path(project_root: Path | None = None) -> Path:
    return _project_root(project_root) / "AGENT-MEMOIRE_PROJECT_STATUS.scribe"


def _ensure_schema(project_root: Path | None = None) -> None:
    db.init_db(project_root)
    with db.connect(project_root) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS canonical_memory_gate_v1(
              task_id TEXT PRIMARY KEY,
              agent_id TEXT NOT NULL,
              request TEXT NOT NULL,
              intent TEXT NOT NULL,
              resource TEXT NOT NULL,
              baseline_hash TEXT NOT NULL,
              baseline_entity_id TEXT,
              baseline_entity_signature TEXT,
              active INTEGER NOT NULL,
              created_at INTEGER NOT NULL,
              resolved_at INTEGER,
              decision TEXT,
              current_hash TEXT,
              skip_reason TEXT,
              retrieval_ok INTEGER,
              retrieval_terms TEXT,
              latest_entity_id TEXT,
              latest_entity_signature TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_canonical_memory_gate_agent
              ON canonical_memory_gate_v1(agent_id,created_at);
            """
        )


def is_active(project_root: Path | None = None) -> bool:
    return _scribe_path(project_root).exists()


def _hash_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _current_hash(project_root: Path | None = None) -> str:
    path = _scribe_path(project_root)
    if not path.exists():
        return ""
    return _hash_bytes(path.read_bytes())


def _load_row(con: Any, task_id: str, agent_id: str) -> dict[str, Any] | None:
    row = con.execute(
        "SELECT * FROM canonical_memory_gate_v1 WHERE task_id=? AND agent_id=?",
        (task_id, agent_id),
    ).fetchone()
    return dict(row) if row else None


def _normalize_intent(intent: str) -> str:
    return (intent or "").strip().lower()


def is_mutating_intent(intent: str) -> bool:
    return _normalize_intent(intent) in MUTATING_INTENTS


def _promotion_required(request: str, intent: str, summary: str) -> bool:
    text = " ".join(part for part in [request, intent, summary] if part).lower()
    return any(hint in text for hint in PROMOTION_REQUIRED_HINTS)


def _skip_reason_is_strong(skip_reason: str) -> bool:
    normalized = " ".join((skip_reason or "").split()).strip().lower()
    if not normalized:
        return False
    if normalized in WEAK_SKIP_REASONS:
        return False
    if len(normalized) < 24:
        return False
    return True


def _latest_entity(store: Any) -> Any | None:
    journal = store.data.get("journal")
    if isinstance(journal, list) and journal:
        last = journal[-1]
        if isinstance(last, dict) and last.get("id"):
            entity = store.by_id(str(last["id"]))
            if entity is not None:
                return entity
    for collection in ("ghosts", "patterns", "scars", "vaccins", "debts", "dettes"):
        items = store.data.get(collection)
        if isinstance(items, list) and items:
            last = items[-1]
            if isinstance(last, dict) and last.get("id"):
                entity = store.by_id(str(last["id"]))
                if entity is not None:
                    return entity
    return None


def _entity_signature(entity: Any) -> str:
    if entity is None:
        return ""
    value = getattr(entity, "value", {}) or {}
    payload = {
        "id": getattr(entity, "id", "") or "",
        "collection": getattr(entity, "collection", "") or "",
        "path": getattr(entity, "path", "") or "",
        "value": value,
    }
    return _hash_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _retrieval_terms(entity: Any) -> str:
    if entity is None:
        return ""
    value = getattr(entity, "value", {}) or {}
    parts = [
        getattr(entity, "id", "") or "",
        str(value.get("titre") or ""),
        str(value.get("title") or ""),
        str(value.get("l0_abstract") or ""),
        str(value.get("pourquoi") or ""),
    ]
    text = " ".join(part for part in parts if part)
    return " ".join(text.split()).strip()


def _retrieval_hits(project_root: Path | None, terms: str, expected_id: str) -> bool:
    if not terms or not expected_id or load_scribe is None:
        return False
    try:
        store = load_scribe(_scribe_path(project_root))
    except Exception:
        return False
    results = store.search(terms, limit=5)
    return any((doc.entity.id or "") == expected_id for _, doc in results)


def snapshot_before_task(
    project_root: Path | None,
    task_id: str,
    agent_id: str,
    request: str,
    intent: str,
    resource: str = "",
) -> dict[str, Any]:
    root = _project_root(project_root)
    if not task_id or not agent_id:
        raise ValueError("task_id and agent_id are required")
    if not is_active(root) or not is_mutating_intent(intent):
        return {"verdict": CANONICAL_MEMORY_DISABLED, "task_id": task_id, "agent_id": agent_id}
    _ensure_schema(root)
    baseline = _current_hash(root)
    latest = None
    if load_scribe is not None:
        try:
            latest = _latest_entity(load_scribe(_scribe_path(root)))
        except Exception:
            latest = None
    with db.connect(root) as con:
        existing = _load_row(con, task_id, agent_id)
        if existing:
            return {"verdict": "CANONICAL_MEMORY_SNAPSHOT_EXISTS", "task_id": task_id, "agent_id": agent_id, "baseline_hash": existing["baseline_hash"]}
        con.execute(
            """
            INSERT INTO canonical_memory_gate_v1(
              task_id,agent_id,request,intent,resource,baseline_hash,baseline_entity_id,baseline_entity_signature,active,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                task_id,
                agent_id,
                request or "",
                _normalize_intent(intent),
                resource or "",
                baseline,
                getattr(latest, "id", "") or "",
                _entity_signature(latest),
                1,
                _now(),
            ),
        )
        db.add_event(con, "canonical_memory_gate.snapshot", {"task_id": task_id, "resource": resource or "", "baseline_hash": baseline}, agent_id)
    return {
        "verdict": "CANONICAL_MEMORY_SNAPSHOT_CREATED",
        "task_id": task_id,
        "agent_id": agent_id,
        "baseline_hash": baseline,
        "baseline_entity_id": getattr(latest, "id", "") or "",
        "baseline_entity_signature": _entity_signature(latest),
    }


def evaluate_finish(
    project_root: Path | None,
    task_id: str,
    agent_id: str,
    request: str,
    intent: str,
    summary: str = "",
    skip_reason: str = "",
) -> dict[str, Any]:
    root = _project_root(project_root)
    if not task_id or not agent_id:
        raise ValueError("task_id and agent_id are required")
    if not is_active(root) or not is_mutating_intent(intent):
        return {
            "verdict": CANONICAL_MEMORY_DISABLED,
            "state": CANONICAL_MEMORY_DISABLED,
            "task_id": task_id,
            "agent_id": agent_id,
            "required": False,
        }

    _ensure_schema(root)
    current_hash = _current_hash(root)
    with db.connect(root) as con:
        snapshot = _load_row(con, task_id, agent_id)
    if not snapshot:
        return {
            "ok": False,
            "verdict": CANONICAL_MEMORY_REQUIRED,
            "state": "CANONICAL_MEMORY_REQUIRED",
            "reason": "Canonical memory baseline was not recorded before the mutating task.",
            "task_id": task_id,
            "agent_id": agent_id,
            "required": True,
            "baseline_hash": "",
            "current_hash": current_hash,
        }

    required = _promotion_required(snapshot.get("request", ""), snapshot.get("intent", ""), summary or request or "")
    if current_hash == snapshot["baseline_hash"]:
        if not skip_reason or not skip_reason.strip():
            return {
                "ok": False,
                "verdict": CANONICAL_MEMORY_REQUIRED,
                "state": "CANONICAL_MEMORY_REQUIRED",
                "reason": "Canonical memory did not change and no auditable skip reason was provided.",
                "task_id": task_id,
                "agent_id": agent_id,
                "required": True,
                "baseline_hash": snapshot["baseline_hash"],
                "current_hash": current_hash,
            }
        if not _skip_reason_is_strong(skip_reason):
            return {
                "ok": False,
                "verdict": CANONICAL_MEMORY_SKIP_REJECTED,
                "state": "CANONICAL_MEMORY_SKIP_REJECTED",
                "reason": "Skip reason is too weak or generic to justify finishing a mutating task without canonical memory.",
                "task_id": task_id,
                "agent_id": agent_id,
                "required": True,
                "baseline_hash": snapshot["baseline_hash"],
                "current_hash": current_hash,
                "skip_reason": skip_reason.strip(),
            }
        if required:
            return {
                "ok": False,
                "verdict": CANONICAL_MEMORY_REQUIRED,
                "state": "CANONICAL_MEMORY_REQUIRED",
                "reason": "This task category requires canonical memory promotion; skipping is not acceptable here.",
                "task_id": task_id,
                "agent_id": agent_id,
                "required": True,
                "baseline_hash": snapshot["baseline_hash"],
                "current_hash": current_hash,
                "skip_reason": skip_reason.strip(),
            }
        with db.connect(root) as con:
            con.execute(
                """
                UPDATE canonical_memory_gate_v1
                SET resolved_at=?, decision=?, current_hash=?, skip_reason=?, retrieval_ok=?, retrieval_terms=?, latest_entity_id=?, latest_entity_signature=?
                WHERE task_id=? AND agent_id=?
                """,
                (_now(), "skip", current_hash, skip_reason.strip(), 0, "", "", "", task_id, agent_id),
            )
            db.add_event(con, "canonical_memory_gate.skipped", {"task_id": task_id, "skip_reason": skip_reason.strip()}, agent_id)
        return {
            "ok": True,
            "verdict": CANONICAL_MEMORY_SKIPPED_WITH_REASON,
            "state": CANONICAL_MEMORY_SKIPPED_WITH_REASON,
            "task_id": task_id,
            "agent_id": agent_id,
            "baseline_hash": snapshot["baseline_hash"],
            "current_hash": current_hash,
            "skip_reason": skip_reason.strip(),
            "scribe_delta": f"SKIP:{skip_reason.strip()}",
            "terminal": True,
        }

    if load_scribe is None:
        return {
            "ok": False,
            "verdict": CANONICAL_MEMORY_REQUIRED,
            "state": "CANONICAL_MEMORY_REQUIRED",
            "reason": "SCRIBE retrieval tooling is unavailable, so canonical memory cannot be validated.",
            "task_id": task_id,
            "agent_id": agent_id,
            "required": True,
            "baseline_hash": snapshot["baseline_hash"],
            "current_hash": current_hash,
        }

    try:
        store = load_scribe(_scribe_path(root))
    except Exception as exc:
        return {
            "ok": False,
            "verdict": CANONICAL_MEMORY_REQUIRED,
            "state": "CANONICAL_MEMORY_REQUIRED",
            "reason": f"SCRIBE parse/retrieval failed: {exc}",
            "task_id": task_id,
            "agent_id": agent_id,
            "required": True,
            "baseline_hash": snapshot["baseline_hash"],
            "current_hash": current_hash,
        }

    entity = _latest_entity(store)
    terms = _retrieval_terms(entity)
    current_signature = _entity_signature(entity)
    baseline_signature = snapshot.get("baseline_entity_signature") or ""
    if current_hash != snapshot["baseline_hash"] and current_signature == baseline_signature:
        return {
            "ok": False,
            "verdict": CANONICAL_MEMORY_REQUIRED,
            "state": "CANONICAL_MEMORY_REQUIRED",
            "reason": "SCRIBE changed, but no new canonical entry was added or updated.",
            "task_id": task_id,
            "agent_id": agent_id,
            "required": True,
            "baseline_hash": snapshot["baseline_hash"],
            "current_hash": current_hash,
            "baseline_entity_id": snapshot.get("baseline_entity_id", ""),
            "baseline_entity_signature": baseline_signature,
            "latest_entity_id": getattr(entity, "id", ""),
            "latest_entity_signature": current_signature,
        }
    hit = bool(entity and _retrieval_hits(root, terms, getattr(entity, "id", "")))
    with db.connect(root) as con:
        con.execute(
            """
            UPDATE canonical_memory_gate_v1
            SET resolved_at=?, decision=?, current_hash=?, skip_reason=?, retrieval_ok=?, retrieval_terms=?, latest_entity_id=?, latest_entity_signature=?
            WHERE task_id=? AND agent_id=?
            """,
            (
                _now(),
                "promote",
                current_hash,
                "",
                1 if hit else 0,
                terms,
                getattr(entity, "id", ""),
                current_signature,
                task_id,
                agent_id,
            ),
        )
        db.add_event(
            con,
            "canonical_memory_gate.promoted" if hit else "canonical_memory_gate.retrieval_miss",
            {"task_id": task_id, "retrieval_ok": hit, "latest_entity_id": getattr(entity, "id", ""), "terms": terms},
            agent_id,
        )
    if not hit:
        return {
            "ok": False,
            "verdict": CANONICAL_MEMORY_REQUIRED,
            "state": "CANONICAL_MEMORY_REQUIRED",
            "reason": "Canonical memory changed, but scribe-rag style retrieval did not surface the new entry.",
            "task_id": task_id,
            "agent_id": agent_id,
            "required": True,
            "baseline_hash": snapshot["baseline_hash"],
            "current_hash": current_hash,
            "retrieval_terms": terms,
            "latest_entity_id": getattr(entity, "id", ""),
            "latest_entity_signature": current_signature,
        }
    return {
        "ok": True,
        "verdict": CANONICAL_MEMORY_PROMOTED,
        "state": CANONICAL_MEMORY_PROMOTED,
        "task_id": task_id,
        "agent_id": agent_id,
        "baseline_hash": snapshot["baseline_hash"],
        "current_hash": current_hash,
        "retrieval_ok": True,
        "retrieval_terms": terms,
        "latest_entity_id": getattr(entity, "id", ""),
        "latest_entity_signature": current_signature,
        "scribe_delta": getattr(entity, "id", ""),
        "terminal": True,
    }
