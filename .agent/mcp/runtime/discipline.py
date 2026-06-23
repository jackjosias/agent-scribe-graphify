from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any

try:
    from . import patch_queue
    from .db import CoordinationError, add_event, connect, init_db, now_ts, require_agent_active
except Exception:
    import patch_queue  # type: ignore
    from db import CoordinationError, add_event, connect, init_db, now_ts, require_agent_active  # type: ignore


DEFAULT_LEASE_TTL_SECONDS = 120
MAX_LEASE_TTL_SECONDS = 600
LEASED_ACTIONS = {
    "claim_resource",
    "file_hash",
    "propose_patch",
    "apply_patch",
    "delete_resource",
    "finish_task",
}
MUTATING_ACTIONS = {"claim_resource", "propose_patch", "apply_patch", "delete_resource", "finish_task"}
WRITE_INTENTS = {"write", "edit", "patch", "modify", "code", "fix", "refactor", "test", "create", "delete", "remove"}


class DisciplineError(RuntimeError):
    def __init__(self, code: str, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


def ensure_schema() -> None:
    init_db()
    with connect() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS action_leases (
              lease_id TEXT PRIMARY KEY,
              agent_id TEXT NOT NULL,
              task_id TEXT,
              resource TEXT,
              intent TEXT,
              action TEXT NOT NULL,
              issued_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              consumed_at INTEGER,
              status TEXT NOT NULL DEFAULT 'active',
              fingerprint_before TEXT,
              metadata TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_action_leases_agent_status
              ON action_leases(agent_id,status,expires_at);
            CREATE INDEX IF NOT EXISTS idx_action_leases_task_resource
              ON action_leases(task_id,resource,status,expires_at);
            CREATE INDEX IF NOT EXISTS idx_action_leases_id_status
              ON action_leases(lease_id,status,expires_at);
        """)


def _safe_resource(resource: str) -> str:
    return patch_queue.safe_resource(resource) if resource else ""


def _normalize_action(action: str) -> str:
    val = (action or "").strip().lower()
    if not val:
        raise DisciplineError("ACTION_REQUIRED")
    aliases = {"edit": "propose_patch", "write": "propose_patch", "delete": "delete_resource", "finish": "finish_task"}
    val = aliases.get(val, val)
    if val not in LEASED_ACTIONS and val not in {"read", "analyze", "shell", "test"}:
        raise DisciplineError("ACTION_NOT_SUPPORTED")
    return val


def _normalize_intent(intent: str) -> str:
    return (intent or "").strip().lower()


def _ttl(ttl_seconds: int | None) -> int:
    if ttl_seconds is None:
        return DEFAULT_LEASE_TTL_SECONDS
    try:
        ttl = int(ttl_seconds)
    except (TypeError, ValueError) as exc:
        raise DisciplineError("ACTION_LEASE_TTL_INVALID") from exc
    if ttl < 1:
        raise DisciplineError("ACTION_LEASE_TTL_INVALID")
    return min(ttl, MAX_LEASE_TTL_SECONDS)


def _lease_id() -> str:
    return f"lease-{secrets.token_urlsafe(18)}"


def _metadata_json(metadata: dict[str, Any] | None) -> str:
    return json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)


def _load_lease(lease_id: str) -> dict[str, Any]:
    if not lease_id or not isinstance(lease_id, str):
        raise DisciplineError("ACTION_LEASE_REQUIRED")
    ensure_schema()
    with connect() as con:
        row = con.execute(
            "SELECT * FROM action_leases WHERE lease_id=?", (lease_id,)
        ).fetchone()
    if not row:
        raise DisciplineError("ACTION_LEASE_INVALID")
    return dict(row)


def _compatible_resource(expected: str, actual: str) -> bool:
    return not expected or not actual or expected == actual


def issue_action_lease(
    agent_id: str,
    action: str,
    task_id: str = "",
    resource: str = "",
    intent: str = "",
    ttl_seconds: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require_agent_active(agent_id)
    ensure_schema()
    normalized_action = _normalize_action(action)
    safe = _safe_resource(resource)
    issued = now_ts()
    expires = issued + _ttl(ttl_seconds)
    lease_id_val = _lease_id()
    with connect() as con:
        con.execute(
            """
            INSERT INTO action_leases(
              lease_id,agent_id,task_id,resource,intent,action,issued_at,expires_at,
              consumed_at,status,fingerprint_before,metadata
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                lease_id_val,
                agent_id,
                task_id or "",
                safe,
                _normalize_intent(intent),
                normalized_action,
                issued,
                expires,
                None,
                "active",
                "",
                _metadata_json(metadata),
            ),
        )
        add_event(con, "discipline.lease_issued", {
            "lease_id": lease_id_val, "task_id": task_id, "resource": safe, "action": normalized_action
        }, agent_id)
    return {
        "lease_id": lease_id_val,
        "agent_id": agent_id,
        "task_id": task_id or "",
        "resource": safe,
        "intent": _normalize_intent(intent),
        "action": normalized_action,
        "issued_at": issued,
        "expires_at": expires,
        "status": "active",
    }


def validate_action_lease(
    lease_id: str,
    agent_id: str,
    action: str,
    task_id: str = "",
    resource: str = "",
    intent: str = "",
) -> dict[str, Any]:
    require_agent_active(agent_id)
    row = _load_lease(lease_id)
    exp = int(row["expires_at"])
    expected_action = _normalize_action(action)
    safe = _safe_resource(resource)

    if row["agent_id"] != agent_id:
        raise DisciplineError("ACTION_LEASE_INVALID", {"reason": "agent_mismatch"})
    if row["action"] != expected_action:
        raise DisciplineError("ACTION_LEASE_INVALID", {"reason": "action_mismatch"})
    if row["status"] == "consumed" or row.get("consumed_at"):
        raise DisciplineError("ACTION_LEASE_CONSUMED")
    if row["status"] != "active":
        raise DisciplineError("ACTION_LEASE_INVALID")
    if exp < now_ts():
        with connect() as con:
            con.execute(
                "UPDATE action_leases SET status='expired' WHERE lease_id=? AND status='active'",
                (lease_id,),
            )
        raise DisciplineError("ACTION_LEASE_EXPIRED")
    if row["task_id"] and task_id and row["task_id"] != task_id:
        raise DisciplineError("ACTION_LEASE_INVALID", {"reason": "task_mismatch"})
    if row["task_id"] and not task_id and expected_action in MUTATING_ACTIONS:
        raise DisciplineError("ACTION_LEASE_INVALID", {"reason": "task_required"})
    if not _compatible_resource(row["resource"] or "", safe):
        raise DisciplineError("ACTION_LEASE_INVALID", {"reason": "resource_mismatch"})
    ri = row["intent"] or ""
    ni = _normalize_intent(intent)
    if ri and ni and ri != ni:
        raise DisciplineError("ACTION_LEASE_INVALID", {"reason": "intent_mismatch"})
    return row


def consume_action_lease(
    lease_id: str,
    agent_id: str,
    action: str,
    task_id: str = "",
    resource: str = "",
    intent: str = "",
) -> dict[str, Any]:
    row = validate_action_lease(
        lease_id, agent_id=agent_id, action=action,
        task_id=task_id, resource=resource, intent=intent,
    )
    consumed = now_ts()
    with connect() as con:
        cur = con.execute(
            """
            UPDATE action_leases
            SET status='consumed', consumed_at=?
            WHERE lease_id=? AND status='active' AND consumed_at IS NULL AND expires_at>=?
            """,
            (consumed, lease_id, consumed),
        )
        if cur.rowcount != 1:
            raise DisciplineError("ACTION_LEASE_INVALID")
        add_event(con, "discipline.lease_consumed", {
            "lease_id": lease_id, "task_id": task_id or row["task_id"],
            "resource": resource or row["resource"], "action": action,
        }, agent_id)
    row["status"] = "consumed"
    row["consumed_at"] = consumed
    return row


def record_guard_ping(agent_id: str, phase: str = "", resource: str = "") -> dict[str, Any]:
    agent = require_agent_active(agent_id)
    ensure_schema()
    safe = _safe_resource(resource)
    with connect() as con:
        add_event(con, "discipline.ping", {"phase": phase or "", "resource": safe}, agent_id)
    return {
        "agent_id": agent_id,
        "agent_status": agent.get("status", "active"),
        "phase": phase or "",
        "resource": safe,
        "timestamp": now_ts(),
    }


def _git_status(root: Path) -> tuple[str, list[str]]:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root), text=True, capture_output=True, timeout=10,
        )
        if proc.returncode != 0:
            return (proc.stderr.strip() or "git status failed", [])
        return ("", [line for line in proc.stdout.splitlines() if line.strip()])
    except subprocess.TimeoutExpired:
        return ("git status timed out", [])
    except FileNotFoundError:
        return ("git executable unavailable", [])


def _status_path(line: str) -> str:
    val = line[3:].strip()
    if " -> " in val:
        val = val.split(" -> ", 1)[1].strip()
    return val


def _is_tracked_change(line: str) -> bool:
    return len(line) >= 3 and not line.startswith("??")


def _mcp_applied_hashes() -> dict[str, str]:
    patch_queue.ensure_schema()
    with patch_queue.connect() as con:
        rows = con.execute(
            """
            SELECT target_path,updated_at,metadata_json FROM patches_v2
            WHERE status='applied'
            ORDER BY updated_at ASC
            """
        ).fetchall()
    applied: dict[str, str] = {}
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        applied_hash = str(metadata.get("applied_hash") or "")
        if applied_hash:
            applied[str(row["target_path"])] = applied_hash
    return applied


def workspace_fingerprint(resource: str = "") -> dict[str, Any]:
    root = patch_queue.root()
    safe = _safe_resource(resource)
    error, lines = _git_status(root)
    if error:
        return {
            "resource": safe,
            "digest": "",
            "status": [],
            "captured_at": now_ts(),
            "error": error,
        }
    filtered: list[str] = []
    for line in lines:
        path = _status_path(line)
        if safe and path != safe:
            continue
        filtered.append(line)
    payload = "\n".join(sorted(filtered))
    return {
        "resource": safe,
        "digest": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "status": sorted(filtered),
        "captured_at": now_ts(),
    }


def detect_direct_write_bypass(
    agent_id: str = "", task_id: str = "", resource: str = "",
) -> dict[str, Any]:
    if agent_id:
        require_agent_active(agent_id)
    ensure_schema()
    root = patch_queue.root()
    safe = _safe_resource(resource) if resource else ""
    error, lines = _git_status(root)
    if error:
        return {
            "ok": True,
            "verdict": "WORKSPACE_AUDIT_UNAVAILABLE",
            "state": "DEGRADED",
            "reason": error,
            "modified_files": [],
            "forbidden": ["finish_task", "direct_file_edit"],
        }
    modified: list[str] = []
    for line in lines:
        if not _is_tracked_change(line):
            continue
        path = _status_path(line)
        if not path:
            continue
        if any(
            path.startswith(prefix)
            for prefix in (".agent/state/", "scribe-out/", "graphify-out/")
        ):
            continue
        if safe and path != safe:
            continue
        modified.append(path)
    applied_hashes = _mcp_applied_hashes()
    bypassed: list[str] = []
    for path in sorted(set(modified)):
        applied_hash = applied_hashes.get(path)
        if not applied_hash:
            bypassed.append(path)
            continue
        try:
            current_hash = patch_queue.file_hash(path)["hash"]
        except Exception:
            bypassed.append(path)
            continue
        if current_hash != applied_hash:
            bypassed.append(path)
    if bypassed:
        record_bypass_detection(
            agent_id=agent_id, task_id=task_id, resource=safe, modified_files=bypassed,
        )
        return {
            "ok": True,
            "verdict": "DIRECT_WRITE_BYPASS_DETECTED",
            "state": "DIRECT_WRITE_BYPASS_DETECTED",
            "modified_files": bypassed,
            "reason": "Tracked files changed without matching MCP apply_patch trace.",
            "forbidden": ["finish_task", "scribe_record_success", "direct_file_edit"],
        }
    return {
        "ok": True,
        "verdict": "WORKSPACE_AUDIT_OK",
        "state": "WORKSPACE_AUDIT_OK",
        "modified_files": [],
        "resource": safe,
    }

def record_bypass_detection(
    agent_id: str = "", task_id: str = "", resource: str = "",
    modified_files: list[str] | None = None,
) -> dict[str, Any]:
    ensure_schema()
    payload = {
        "task_id": task_id or "",
        "resource": resource or "",
        "modified_files": modified_files or [],
    }
    with connect() as con:
        add_event(con, "discipline.direct_write_bypass", payload, agent_id or None)
    return {"verdict": "DIRECT_WRITE_BYPASS_RECORDED", **payload}
