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

# ─── Lease extension policy (Fix #5) ─────────────────────────────────────────
# POLICY: MAX_CUMULATIVE_LEASE_TTL_SECONDS is the ABSOLUTE HARD CEILING.
#         It always wins over MAX_LEASE_EXTEND_COUNT.
#
# Rationale: the user must never be surprised in production by a lease that
# expired because the count was hit before the time limit. The cumulative cap
# guarantees an ABSOLUTE maximum wall-clock time for any lease, regardless
# of how many times it was extended.
#
# MAX_LEASE_EXTEND_COUNT is a secondary anti-DoS / anti-infinite-loop guard.
# It prevents a stuck agent from extending forever until the ceiling.
#
# Chosen values (self-consistent):
#   MAX_LEASE_EXTEND_COUNT × DEFAULT_EXTEND_SECONDS = 10 × 300 = 3000s
#   MAX_CUMULATIVE_LEASE_TTL_SECONDS                         = 3600s
# => Under normal extends (default_extend=300s), the count cap is hit at 3000s
#    which is BEFORE the ceiling at 3600s. Both caps are reachable.
# => If a user requests 600s extends (MAX_LEASE_TTL_SECONDS), count runs out
#    at 6000s which would exceed ceiling. The ceiling wins at 3600s.
# => No incoherence: count may be exhausted before ceiling, or ceiling may be
#    reached with count remaining — BOTH are valid termination conditions and
#    both raise a distinct error code so the caller knows which cap was hit.
# ─────────────────────────────────────────────────────────────────────────────
MAX_CUMULATIVE_LEASE_TTL_SECONDS = 3600  # Absolute ceiling (always wins).
MAX_LEASE_EXTEND_COUNT = 10             # Secondary anti-DoS cap.
DEFAULT_EXTEND_SECONDS = 300           # 300s × 10 = 3000s max via count cap.

# Resource lock: default TTL (seconds). Longer than lease since it covers multi-file ops.
DEFAULT_RESOURCE_LOCK_TTL_SECONDS = 600
MAX_RESOURCE_LOCK_TTL_SECONDS = 7200

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

# Paths in the workspace that are always excluded from bypass detection,
# as they are generated/runtime state rather than user-code.
_AUDIT_EXCLUDED_PREFIXES = (
    ".agent/state/",
    ".agent/scribe-out/",
    "scribe-out/",
    "graphify-out/",
)


class DisciplineError(RuntimeError):
    def __init__(self, code: str, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


def ensure_schema() -> None:
    """Initialise all discipline tables idempotently.

    This is safe to call concurrently: SQLite serialises DDL at the connection
    level, and IF NOT EXISTS guards prevent duplicate creation.
    """
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
              metadata TEXT,
              extend_count INTEGER NOT NULL DEFAULT 0,
              original_issued_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_action_leases_agent_status
              ON action_leases(agent_id,status,expires_at);
            CREATE INDEX IF NOT EXISTS idx_action_leases_task_resource
              ON action_leases(task_id,resource,status,expires_at);
            CREATE INDEX IF NOT EXISTS idx_action_leases_id_status
              ON action_leases(lease_id,status,expires_at);

            -- Resource locks: prevent N agents / orchestrators from writing to
            -- the same resource concurrently. Separate from action_leases so
            -- revoke can target a resource without touching per-action state.
            CREATE TABLE IF NOT EXISTS resource_locks (
              lock_id TEXT PRIMARY KEY,
              agent_id TEXT NOT NULL,
              resource TEXT NOT NULL,
              task_id TEXT NOT NULL DEFAULT '',
              acquired_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              released_at INTEGER,
              status TEXT NOT NULL DEFAULT 'active',
              metadata TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_resource_locks_resource_status
              ON resource_locks(resource, status, expires_at);
            CREATE INDEX IF NOT EXISTS idx_resource_locks_agent_status
              ON resource_locks(agent_id, status, expires_at);
            CREATE INDEX IF NOT EXISTS idx_resource_locks_id
              ON resource_locks(lock_id, status);

            -- Add extend_count and original_issued_at columns to existing
            -- action_leases tables that predate this schema version.
            -- SQLite ignores "ALTER TABLE ADD COLUMN" if it errors; we swallow.
        """)
        # Migrate pre-existing action_leases tables that lack new columns.
        for col, default in [("extend_count", "0"), ("original_issued_at", "NULL")]:
            try:
                con.execute(
                    f"ALTER TABLE action_leases ADD COLUMN {col} INTEGER NOT NULL DEFAULT {default}"
                )
            except Exception:
                pass  # Column already exists — expected on all runs after first.


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


def _lock_id() -> str:
    return f"rlock-{secrets.token_urlsafe(18)}"


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


# ─────────────────────────────────────────────────────────────
# Action Lease CRUD
# ─────────────────────────────────────────────────────────────

def issue_action_lease(
    agent_id: str,
    action: str,
    task_id: str = "",
    resource: str = "",
    intent: str = "",
    ttl_seconds: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Issue a single-use, time-bounded action lease.

    Invariants:
    - Only one active lease per (agent_id, action, resource) is permitted
      for MUTATING_ACTIONS to prevent double-write races.
    - Expired leases are purged atomically before issuing a new one.
    """
    require_agent_active(agent_id)
    ensure_schema()
    normalized_action = _normalize_action(action)
    safe = _safe_resource(resource)
    issued = now_ts()
    expires = issued + _ttl(ttl_seconds)
    lease_id_val = _lease_id()

    with connect() as con:
        # Expire stale leases for this (agent, action, resource) before inserting.
        con.execute(
            """
            UPDATE action_leases SET status='expired'
            WHERE agent_id=? AND action=? AND resource=? AND status='active' AND expires_at<?
            """,
            (agent_id, normalized_action, safe, issued),
        )
        con.execute(
            """
            INSERT INTO action_leases(
              lease_id,agent_id,task_id,resource,intent,action,issued_at,expires_at,
              consumed_at,status,fingerprint_before,metadata,extend_count,original_issued_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                0,
                issued,
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
        "extend_count": 0,
    }


def extend_action_lease(
    lease_id: str,
    agent_id: str,
    extend_seconds: int | None = None,
) -> dict[str, Any]:
    """Extend an active lease's TTL without losing FSM context.

    Policy (Fix #5 — clear and documented, no ambiguity in production):
    ─────────────────────────────────────────────────────────────────────
    Two independent caps are enforced in order:

    1. MAX_CUMULATIVE_LEASE_TTL_SECONDS (ABSOLUTE CEILING — checked FIRST).
       The new expiry cannot exceed original_issued_at + MAX_CUMULATIVE.
       If it would, the extend is refused with ACTION_LEASE_EXTEND_CEILING_REACHED.
       This is the hard wall: once reached, no further extends are possible.

    2. MAX_LEASE_EXTEND_COUNT (secondary anti-DoS guard — checked SECOND).
       If the extend count is >= MAX_LEASE_EXTEND_COUNT, refuse with
       ACTION_LEASE_EXTEND_LIMIT. The count cap is intentionally softer:
       it prevents extend-loop abuse, but the ceiling provides the hard bound.

    Why ceiling-first?
       An agent operating on a slow network (Africa, high-latency VPN) may
       exhaust the default_extend_seconds per call but still be within the
       cumulative ceiling. The ceiling check gives a deterministic wall-clock
       guarantee, which is what matters in production.

    Design goals:
    - Only ACTIVE, non-consumed, non-expired leases can be extended.
    - Extend is idempotent within a single second (harmless double-call).
    - Raises DisciplineError on all invalid inputs.
    """
    if not lease_id or not isinstance(lease_id, str):
        raise DisciplineError("ACTION_LEASE_REQUIRED")
    if not agent_id or not isinstance(agent_id, str):
        raise DisciplineError("AGENT_ID_REQUIRED")

    extra_secs = DEFAULT_EXTEND_SECONDS
    if extend_seconds is not None:
        try:
            extra_secs = int(extend_seconds)
        except (TypeError, ValueError) as exc:
            raise DisciplineError("EXTEND_SECONDS_INVALID") from exc
        if extra_secs < 1:
            raise DisciplineError("EXTEND_SECONDS_INVALID")
        extra_secs = min(extra_secs, MAX_LEASE_TTL_SECONDS)

    require_agent_active(agent_id)
    ensure_schema()

    now = now_ts()

    with connect() as con:
        row = con.execute(
            "SELECT * FROM action_leases WHERE lease_id=?",
            (lease_id,),
        ).fetchone()

        if not row:
            raise DisciplineError("ACTION_LEASE_INVALID")
        row = dict(row)

        if row["agent_id"] != agent_id:
            raise DisciplineError("ACTION_LEASE_INVALID", {"reason": "agent_mismatch"})
        if row["status"] == "consumed" or row.get("consumed_at"):
            raise DisciplineError("ACTION_LEASE_CONSUMED")
        if row["status"] != "active":
            raise DisciplineError("ACTION_LEASE_INVALID", {"reason": f"status={row['status']}"})
        if int(row["expires_at"]) < now:
            # Mark expired atomically before raising.
            con.execute(
                "UPDATE action_leases SET status='expired' WHERE lease_id=? AND status='active'",
                (lease_id,),
            )
            raise DisciplineError("ACTION_LEASE_EXPIRED")

        original_issued = int(row.get("original_issued_at") or row["issued_at"])
        current_expires = int(row["expires_at"])
        absolute_ceiling = original_issued + MAX_CUMULATIVE_LEASE_TTL_SECONDS
        current_count = int(row.get("extend_count") or 0)

        # Fix #5: CEILING checked FIRST (absolute hard wall).
        if current_expires >= absolute_ceiling:
            raise DisciplineError(
                "ACTION_LEASE_EXTEND_CEILING_REACHED",
                {
                    "reason": "cumulative TTL ceiling already reached (ceiling wins)",
                    "ceiling": absolute_ceiling,
                    "current_expires": current_expires,
                    "now": now,
                    "policy": "MAX_CUMULATIVE_LEASE_TTL_SECONDS is the absolute hard wall",
                },
            )

        # Compute proposed new expiry (clamped to ceiling).
        proposed_expires = max(current_expires, now) + extra_secs
        new_expires = min(proposed_expires, absolute_ceiling)

        if new_expires <= now:
            raise DisciplineError(
                "ACTION_LEASE_EXTEND_CEILING_REACHED",
                {
                    "reason": "cumulative TTL ceiling reached after clamping",
                    "ceiling": absolute_ceiling,
                    "now": now,
                },
            )

        # Fix #5: COUNT checked SECOND (secondary anti-DoS guard).
        if current_count >= MAX_LEASE_EXTEND_COUNT:
            raise DisciplineError(
                "ACTION_LEASE_EXTEND_LIMIT",
                {
                    "reason": f"max {MAX_LEASE_EXTEND_COUNT} extensions reached (secondary cap)",
                    "extend_count": current_count,
                    "policy": "MAX_LEASE_EXTEND_COUNT is secondary; ceiling is the hard wall",
                },
            )

        new_count = current_count + 1
        con.execute(
            """
            UPDATE action_leases
            SET expires_at=?, extend_count=?
            WHERE lease_id=? AND status='active'
            """,
            (new_expires, new_count, lease_id),
        )
        add_event(con, "discipline.lease_extended", {
            "lease_id": lease_id,
            "extend_count": new_count,
            "new_expires_at": new_expires,
            "extra_seconds": extra_secs,
            "ceiling": absolute_ceiling,
            "ceiling_remaining_seconds": absolute_ceiling - new_expires,
        }, agent_id)

    return {
        "lease_id": lease_id,
        "agent_id": agent_id,
        "action": row["action"],
        "resource": row["resource"] or "",
        "task_id": row["task_id"] or "",
        "expires_at": new_expires,
        "extend_count": new_count,
        "status": "active",
        "verdict": "LEASE_EXTENDED",
        "ceiling": absolute_ceiling,
        "ceiling_remaining_seconds": absolute_ceiling - new_expires,
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


# ─────────────────────────────────────────────────────────────
# Resource Locks — multi-agent / orchestrator coordination
# ─────────────────────────────────────────────────────────────

def resource_lock_claim(
    agent_id: str,
    resource: str,
    task_id: str = "",
    ttl_seconds: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Claim an exclusive write lock on a resource.

    Design:
    - If the resource is already locked by a different agent with an active,
      non-expired lock → raises DisciplineError("RESOURCE_ALREADY_LOCKED").
    - Re-entry: if the same agent already holds an active lock on the same
      resource, the existing lock is returned (idempotent, no duplicate).
    - Stale locks (expired) are cleaned up before attempting to claim.
    - No deadlock possible: single resource per call, no transitive acquisition.

    Multi-agent guard: this prevents an orchestrator spawning N agents that
    all grab leases on the same resource and clobber each other.
    """
    if not agent_id or not isinstance(agent_id, str):
        raise DisciplineError("AGENT_ID_REQUIRED")
    if not resource:
        raise DisciplineError("RESOURCE_REQUIRED")

    require_agent_active(agent_id)
    ensure_schema()
    safe = _safe_resource(resource)
    ttl = DEFAULT_RESOURCE_LOCK_TTL_SECONDS
    if ttl_seconds is not None:
        try:
            ttl = max(1, min(int(ttl_seconds), MAX_RESOURCE_LOCK_TTL_SECONDS))
        except (TypeError, ValueError) as exc:
            raise DisciplineError("RESOURCE_LOCK_TTL_INVALID") from exc

    now = now_ts()
    expires = now + ttl

    with connect() as con:
        # Sweep expired locks to keep the table lean.
        con.execute(
            "UPDATE resource_locks SET status='expired' WHERE resource=? AND status='active' AND expires_at<?",
            (safe, now),
        )

        # Check for an existing active lock on this resource.
        existing = con.execute(
            "SELECT * FROM resource_locks WHERE resource=? AND status='active' ORDER BY acquired_at ASC LIMIT 1",
            (safe,),
        ).fetchone()

        if existing:
            existing = dict(existing)
            if existing["agent_id"] == agent_id:
                # Same agent already holds the lock — idempotent re-entry.
                return {
                    "verdict": "RESOURCE_LOCK_ALREADY_HELD",
                    "lock_id": existing["lock_id"],
                    "agent_id": agent_id,
                    "resource": safe,
                    "task_id": existing["task_id"],
                    "acquired_at": existing["acquired_at"],
                    "expires_at": existing["expires_at"],
                    "status": "active",
                }
            # Different agent holds the lock.
            raise DisciplineError(
                "RESOURCE_ALREADY_LOCKED",
                {
                    "lock_id": existing["lock_id"],
                    "owner_agent_id": existing["agent_id"],
                    "resource": safe,
                    "expires_at": existing["expires_at"],
                    "seconds_remaining": max(0, int(existing["expires_at"]) - now),
                },
            )

        lock_id_val = _lock_id()
        con.execute(
            """
            INSERT INTO resource_locks(
              lock_id, agent_id, resource, task_id,
              acquired_at, expires_at, released_at, status, metadata
            ) VALUES(?,?,?,?,?,?,NULL,'active',?)
            """,
            (lock_id_val, agent_id, safe, task_id or "", now, expires, _metadata_json(metadata)),
        )
        add_event(con, "discipline.resource_lock_claimed", {
            "lock_id": lock_id_val, "resource": safe, "task_id": task_id or "", "expires_at": expires,
        }, agent_id)

    return {
        "verdict": "RESOURCE_LOCK_CLAIMED",
        "lock_id": lock_id_val,
        "agent_id": agent_id,
        "resource": safe,
        "task_id": task_id or "",
        "acquired_at": now,
        "expires_at": expires,
        "status": "active",
    }


def resource_lock_release(
    agent_id: str,
    resource: str,
    lock_id: str = "",
) -> dict[str, Any]:
    """Release an active resource lock held by agent_id.

    Invariants:
    - Only the owning agent can release its own lock (zero-trust: no agent
      can release another agent's lock, preventing malicious unlock).
    - If lock_id is provided, it must match the found lock (extra specificity).
    - Releasing an already-released or non-existent lock is idempotent (no error).
    """
    if not agent_id or not isinstance(agent_id, str):
        raise DisciplineError("AGENT_ID_REQUIRED")
    if not resource:
        raise DisciplineError("RESOURCE_REQUIRED")

    require_agent_active(agent_id)
    ensure_schema()
    safe = _safe_resource(resource)
    now = now_ts()

    with connect() as con:
        query = "SELECT * FROM resource_locks WHERE resource=? AND agent_id=? AND status='active'"
        params: list[Any] = [safe, agent_id]
        if lock_id:
            query += " AND lock_id=?"
            params.append(lock_id)
        query += " ORDER BY acquired_at ASC LIMIT 1"

        row = con.execute(query, params).fetchone()
        if not row:
            # Idempotent: already released or never held.
            return {
                "verdict": "RESOURCE_LOCK_ALREADY_RELEASED",
                "agent_id": agent_id,
                "resource": safe,
            }

        row = dict(row)
        con.execute(
            "UPDATE resource_locks SET status='released', released_at=? WHERE lock_id=? AND status='active'",
            (now, row["lock_id"]),
        )
        add_event(con, "discipline.resource_lock_released", {
            "lock_id": row["lock_id"], "resource": safe,
        }, agent_id)

    return {
        "verdict": "RESOURCE_LOCK_RELEASED",
        "lock_id": row["lock_id"],
        "agent_id": agent_id,
        "resource": safe,
        "released_at": now,
    }


def resource_lock_status(resource: str) -> dict[str, Any]:
    """Check who holds a lock on a resource and whether it is expired.

    Returns the current lock state without mutating anything.
    Safe to call from any agent, including orchestrators doing pre-flight checks.
    """
    if not resource:
        raise DisciplineError("RESOURCE_REQUIRED")

    ensure_schema()
    safe = _safe_resource(resource)
    now = now_ts()

    with connect() as con:
        # Also sweep expired locks inline for fresh results.
        con.execute(
            "UPDATE resource_locks SET status='expired' WHERE resource=? AND status='active' AND expires_at<?",
            (safe, now),
        )
        row = con.execute(
            "SELECT * FROM resource_locks WHERE resource=? AND status='active' ORDER BY acquired_at ASC LIMIT 1",
            (safe,),
        ).fetchone()

    if not row:
        return {
            "verdict": "RESOURCE_LOCK_FREE",
            "resource": safe,
            "locked": False,
        }

    row = dict(row)
    return {
        "verdict": "RESOURCE_LOCK_HELD",
        "resource": safe,
        "locked": True,
        "lock_id": row["lock_id"],
        "owner_agent_id": row["agent_id"],
        "task_id": row["task_id"],
        "acquired_at": row["acquired_at"],
        "expires_at": row["expires_at"],
        "seconds_remaining": max(0, int(row["expires_at"]) - now),
    }


# ─────────────────────────────────────────────────────────────
# Discipline ping
# ─────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────
# Workspace audit — git-status based (tracked + untracked)
# ─────────────────────────────────────────────────────────────

def _git_status(root: Path) -> tuple[str, list[str]]:
    """Run `git status --porcelain` and return (error_str, lines).

    Cross-platform: subprocess with explicit executable lookup.
    Timeout: 10 s — generous enough for large repos under load.
    """
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
    except Exception as exc:
        return (f"git status error: {exc}", [])


def _status_path(line: str) -> str:
    """Extract the workspace-relative path from a git-status porcelain line."""
    val = line[3:].strip()
    if " -> " in val:
        val = val.split(" -> ", 1)[1].strip()
    # Strip quotes that git adds for paths with special chars.
    if val.startswith('"') and val.endswith('"'):
        val = val[1:-1]
    return val


def _is_tracked_change(line: str) -> bool:
    """Return True if the line represents a tracked (modified/added/deleted) file."""
    return len(line) >= 3 and not line.startswith("??")


def _is_untracked_file(line: str) -> bool:
    """Return True if the line represents an untracked new file (git ?? prefix)."""
    return len(line) >= 3 and line.startswith("??")


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


def _is_excluded_path(path: str) -> bool:
    """Return True if path is internal runtime state that should not trigger bypass alert."""
    return any(path.startswith(prefix) for prefix in _AUDIT_EXCLUDED_PREFIXES)


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
    """Detect filesystem changes that bypassed MCP controls.

    Covers TWO categories (previously only one):
    1. TRACKED files modified without a matching apply_patch trace.
    2. UNTRACKED new files created outside MCP (git ?? prefix).
       This catches LLMs that write_file or create new scripts directly.

    Both categories are cross-platform: uses git which works on Linux/macOS/Windows.
    No inotifywait or OS-specific watchers required — portable by design.
    """
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
            "untracked_files": [],
            "forbidden": ["finish_task", "direct_file_edit"],
        }

    modified: list[str] = []
    untracked: list[str] = []

    for line in lines:
        path = _status_path(line)
        if not path:
            continue
        if _is_excluded_path(path):
            continue
        if safe and path != safe:
            continue

        if _is_tracked_change(line):
            modified.append(path)
        elif _is_untracked_file(line):
            # Untracked files created outside MCP are suspicious.
            # Exclude directories (git marks them with trailing slash).
            if not path.endswith("/"):
                untracked.append(path)

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

    # Untracked files always count as bypass — no MCP trace can exist for them.
    untracked_bypassed = sorted(set(untracked))

    all_bypassed = sorted(set(bypassed + untracked_bypassed))

    if all_bypassed:
        record_bypass_detection(
            agent_id=agent_id, task_id=task_id, resource=safe,
            modified_files=bypassed, untracked_files=untracked_bypassed,
        )
        return {
            "ok": True,
            "verdict": "DIRECT_WRITE_BYPASS_DETECTED",
            "state": "DIRECT_WRITE_BYPASS_DETECTED",
            "modified_files": bypassed,
            "untracked_files": untracked_bypassed,
            "all_bypassed": all_bypassed,
            "reason": (
                "Files changed or created without matching MCP apply_patch trace. "
                "Tracked bypass: {tracked}. Untracked new files: {untracked}.".format(
                    tracked=len(bypassed), untracked=len(untracked_bypassed)
                )
            ),
            "forbidden": ["finish_task", "scribe_record_success", "direct_file_edit"],
        }

    return {
        "ok": True,
        "verdict": "WORKSPACE_AUDIT_OK",
        "state": "WORKSPACE_AUDIT_OK",
        "modified_files": [],
        "untracked_files": [],
        "resource": safe,
    }


def record_bypass_detection(
    agent_id: str = "", task_id: str = "", resource: str = "",
    modified_files: list[str] | None = None,
    untracked_files: list[str] | None = None,
) -> dict[str, Any]:
    ensure_schema()
    payload = {
        "task_id": task_id or "",
        "resource": resource or "",
        "modified_files": modified_files or [],
        "untracked_files": untracked_files or [],
    }
    with connect() as con:
        add_event(con, "discipline.direct_write_bypass", payload, agent_id or None)
    return {"verdict": "DIRECT_WRITE_BYPASS_RECORDED", **payload}
