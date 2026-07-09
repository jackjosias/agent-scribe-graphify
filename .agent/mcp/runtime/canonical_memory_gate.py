from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    from . import db
except Exception:
    import db  # type: ignore

try:
    from . import direct_fs_tripwire
except Exception:
    import direct_fs_tripwire  # type: ignore

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
VALID_MEMORY_POLICIES = {"canonical_required", "local_only", "skip_with_reason", "ephemeral"}
CANONICAL_RECORD_TYPES = {
    "validation",
    "test_result",
    "audit_result",
    "decision",
    "scar",
    "vaccine",
    "bug_fix",
    "postmortem",
    "acceptance",
}
LOCAL_ONLY_RECORD_TYPES = {
    "task_summary",
    "journal",
    "note",
    "debug",
    "log",
    "trace",
    "receipt",
    "runtime_receipt",
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


def normalize_memory_policy(policy: str | None) -> str:
    normalized = (policy or "").strip().lower()
    return normalized if normalized in VALID_MEMORY_POLICIES else ""


def derive_memory_policy(
    record_type: str,
    memory_policy: str = "",
    request: str = "",
    summary: str = "",
    verdict: str = "",
) -> str:
    normalized = normalize_memory_policy(memory_policy)
    if normalized:
        return normalized
    record = (record_type or "").strip().lower()
    if record in CANONICAL_RECORD_TYPES:
        return "canonical_required"
    if record in LOCAL_ONLY_RECORD_TYPES:
        return "local_only"
    text = " ".join(part for part in [record, request, summary, verdict] if part).strip().lower()
    if any(term in text for term in ("validation", "test result", "audit", "decision", "postmortem", "acceptance", "bug fix", "scar", "vaccine")):
        return "canonical_required"
    return "local_only"


def policy_requires_canonical_promotion(policy: str) -> bool:
    return normalize_memory_policy(policy) == "canonical_required"


def policy_can_finish_without_promotion(policy: str, skip_reason: str = "") -> bool:
    normalized = normalize_memory_policy(policy)
    if normalized in {"local_only", "ephemeral"}:
        return True
    if normalized == "skip_with_reason":
        return _skip_reason_is_strong(skip_reason)
    return False


def _latest_entity(store: Any) -> Any | None:
    canonical = store.data.get("canonical")
    if isinstance(canonical, list) and canonical:
        last = canonical[-1]
        if isinstance(last, dict) and last.get("id"):
            entity = store.by_id(str(last["id"]))
            if entity is not None:
                return entity
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
    return _hash_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))


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


def _canonical_entry_id(source_record_digest: str, source_record_path: str) -> str:
    raw = f"{source_record_digest}:{source_record_path}".encode("utf-8")
    return "CANON-" + hashlib.sha256(raw).hexdigest()[:16].upper()


def _canonical_status(record_type: str, memory_policy: str, verdict: str = "") -> str:
    normalized = (record_type or "").strip().lower()
    if normalized in {"scar", "bug_fix"} or "fail" in (verdict or "").lower():
        return "FAIL"
    if normalize_memory_policy(memory_policy) == "skip_with_reason":
        return "INFO"
    if normalized in {"audit_result", "validation", "test_result", "acceptance"}:
        return "PASS"
    return "INFO"


def _canonical_scope(record: dict[str, Any], scope: str = "") -> str:
    if scope.strip():
        return scope.strip()
    resources = record.get("resources") or record.get("touched_resources") or []
    if isinstance(resources, list) and resources:
        first = resources[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    request = str(record.get("request") or "").strip()
    summary = str(record.get("summary") or "").strip()
    return request or summary or "project"


def _canonical_summary(record: dict[str, Any]) -> str:
    summary = str(record.get("summary") or record.get("request") or "").strip()
    if not summary:
        summary = "Canonical memory promotion"
    return " ".join(summary.split())


def _canonical_entry_block(
    entry_id: str,
    record: dict[str, Any],
    source_record_path: str,
    memory_policy: str,
    scope: str,
    source_record_digest: str,
) -> str:
    record_type = str(record.get("record_type") or record.get("type") or "journal").strip().lower() or "journal"
    status = _canonical_status(record_type, memory_policy, str(record.get("verdict") or ""))
    summary = _canonical_summary(record)
    evidence = source_record_path.replace('"', '\"')
    source_name = Path(source_record_path).name.replace('"', '\"')
    digest = source_record_digest.replace('"', '\"')
    scope_value = scope.replace('"', '\"') or "project"
    policy_value = normalize_memory_policy(memory_policy) or "local_only"
    date_value = time.strftime("%Y-%m-%d", time.gmtime(int(record.get("timestamp") or _now())))
    entry = [
        f'  - id: "{entry_id}"',
        f'    date: "{date_value}"',
        f'    type: "{record_type}"',
        f'    scope: "{scope_value}"',
        f'    status: "{status}"',
        "    summary: >",
        f'      {summary}',
        "    evidence:",
        f'      - "{evidence}"',
        "    result:",
        "      canonical_memory_promoted: true",
        f'      source_record: "{source_name}"',
        f'      source_record_path: "{evidence}"',
        f'      source_record_digest: "{digest}"',
        f'      memory_policy: "{policy_value}"',
        f'      promoted_at: "{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}"',
    ]
    return "\n".join(entry) + "\n"


def _append_canonical_entry_text(existing: str, entry_block: str) -> str:
    canon_marker = "\ncanonical:\n"
    metrics_marker = "\nmetrics:\n"
    if canon_marker in existing:
        prefix, suffix = existing.rsplit(metrics_marker, 1)
        canon_prefix, canon_body = prefix.rsplit(canon_marker, 1)
        return canon_prefix + canon_marker + canon_body.rstrip() + "\n" + entry_block + metrics_marker + suffix
    if metrics_marker in existing:
        prefix, suffix = existing.rsplit(metrics_marker, 1)
        return prefix.rstrip() + "\n" + canon_marker + entry_block + metrics_marker + suffix
    return existing.rstrip() + "\n" + canon_marker + entry_block


def promote_record(
    project_root: Path | None,
    record: dict[str, Any],
    source_record_path: Path,
    *,
    scope: str = "",
    memory_policy: str = "canonical_required",
    agent_id: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    root = _project_root(project_root)
    scribe_path = _scribe_path(root)
    if not source_record_path.is_file():
        record_path = str(source_record_path.relative_to(root)) if source_record_path.is_relative_to(root) else str(source_record_path)
        return {
            "ok": False,
            "verdict": "CANONICAL_MEMORY_PROMOTION_FAILED",
            "state": "CANONICAL_MEMORY_PROMOTION_FAILED",
            "reason": "source record is missing",
            "canonical_memory_file": str(scribe_path.relative_to(root)),
            "record_path": record_path,
        }
    normalized_policy = normalize_memory_policy(memory_policy)
    record_path = str(source_record_path.relative_to(root)) if source_record_path.is_relative_to(root) else str(source_record_path)
    if normalized_policy != "canonical_required":
        return {
            "ok": False,
            "verdict": "CANONICAL_MEMORY_NOT_REQUIRED",
            "state": "CANONICAL_MEMORY_NOT_REQUIRED",
            "reason": f"memory_policy={normalized_policy or memory_policy or 'local_only'} does not require canonical promotion",
            "canonical_memory_file": str(scribe_path.relative_to(root)),
            "record_path": record_path,
        }
    payload = dict(record)
    source_record_digest = hashlib.sha256(source_record_path.read_bytes()).hexdigest()
    entry_id = _canonical_entry_id(source_record_digest, record_path)
    existing = scribe_path.read_text(encoding="utf-8") if scribe_path.is_file() else ""
    if entry_id in existing or source_record_digest in existing or record_path in existing:
        return {
            "ok": True,
            "verdict": "CANONICAL_MEMORY_ALREADY_PROMOTED",
            "state": "CANONICAL_MEMORY_ALREADY_PROMOTED",
            "canonical_memory_file": str(scribe_path.relative_to(root)),
            "record_path": record_path,
            "entry_id": entry_id,
            "already_promoted": True,
            "canonical_memory_updated": False,
        }
    entry_block = _canonical_entry_block(
        entry_id,
        payload,
        record_path,
        normalized_policy,
        scope,
        source_record_digest,
    )
    new_content = _append_canonical_entry_text(existing, entry_block)
    tmp = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(scribe_path.parent),
        delete=False,
        prefix=f".{scribe_path.name}.",
        suffix=".tmp",
    )
    before_hash = hashlib.sha256(existing.encode("utf-8")).hexdigest() if existing else ""
    try:
        with tmp as fh:
            fh.write(new_content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp.name, scribe_path)
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise
    after_hash = hashlib.sha256(scribe_path.read_bytes()).hexdigest()
    if agent_id and task_id:
        scribe_path_str = str(scribe_path.relative_to(root)) if scribe_path.is_relative_to(root) else scribe_path.name
        direct_fs_tripwire.record_authorized_mutation(
            task_id=task_id,
            agent_id=agent_id,
            resource=scribe_path_str,
            tool="scribe_promote_record",
            before_hash=before_hash,
            after_hash=after_hash,
            project_root=root,
        )
    return {
        "ok": True,
        "verdict": "CANONICAL_MEMORY_PROMOTED",
        "state": "CANONICAL_MEMORY_PROMOTED",
        "canonical_memory_file": str(scribe_path.relative_to(root)),
        "record_path": record_path,
        "entry_id": entry_id,
        "already_promoted": False,
        "canonical_memory_updated": True,
        "source_record_digest": source_record_digest,
    }
