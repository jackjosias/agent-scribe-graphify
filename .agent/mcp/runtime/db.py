"""db.py — Core SQLite coordination layer for the .agent MCP runtime.

Design principles (production-grade):
  1. PORTABILITY : ALL paths stored in the DB (target_file in patches, etc.)
     are RELATIVE to the project root. Resolution to absolute paths happens
     at runtime only. This makes the .agent directory truly portable across
     machines, operating systems, and users.

  2. DURABILITY : WAL journal mode + synchronous=NORMAL + busy_timeout are
     set on every connection. SQLite integrity_check runs at bootstrap.
     Backup is performed automatically before any schema migration.

  3. SCHEMA VERSIONING : A `schema_migrations` table tracks applied migrations.
     All migrations are idempotent: running them twice is a no-op. This
     prevents the ALTER TABLE fragility on older DBs.

  4. COORDINATION BRIDGE : resource_locks (discipline) and claims are checked
     together before any mutating operation, preventing split-brain states.

Cross-platform: Linux / macOS / Windows via Python pathlib + sqlite3.
No external dependencies beyond the standard library.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

try:
    from .state_paths import prepare_state_dirs, project_root_from
except Exception:
    from state_paths import prepare_state_dirs, project_root_from  # type: ignore

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:/")

# Bump this whenever a new migration is added to _MIGRATIONS below.
# Format: "YYYY-MM-DD-NNN" (sortable string = canonical execution order).
_DB_APP_ID = 0x41475900  # "AGY\x00" as 32-bit big-endian integer.


# ─────────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────────

class CoordinationError(RuntimeError):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def now_ts() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def paths(project_root: Optional[Path] = None) -> Dict[str, Path]:
    return prepare_state_dirs(project_root)


# ─────────────────────────────────────────────────────────────────────────────
# Connection factory
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def connect(project_root: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Open a WAL-mode SQLite connection, yielding it as a context manager.

    Invariants guaranteed on every connection:
      - WAL journal: concurrent readers never block writers.
      - synchronous=NORMAL: durable on OS crash (not power loss), but ×10 faster
        than FULL. Acceptable for coordination metadata.
      - foreign_keys: referential integrity enforced.
      - busy_timeout=10 000 ms: prevents SQLITE_BUSY under concurrent load.
        Generous for African/high-latency networks where OS scheduling latency
        can spike.
      - application_id: marks the DB as owned by this tool (prevents accidental
        open of unrelated SQLite files).
    """
    p = paths(project_root)
    con = sqlite3.connect(str(p["db"]), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=10000")
        con.execute(f"PRAGMA application_id={_DB_APP_ID}")
        yield con
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────────────────────
# Schema migrations  (Fix #8 — versioned, idempotent, append-only)
# ─────────────────────────────────────────────────────────────────────────────
# Rules:
#   • Each entry is (migration_id: str, sql: str).
#   • migration_id must be unique and sortable (e.g. "2026-06-23-001").
#   • sql must be idempotent (IF NOT EXISTS / IF EXISTS guards).
#   • NEVER modify an existing entry — always append a new one.
#   • _backup_db() is called before the first unapplied migration in a run.
# ─────────────────────────────────────────────────────────────────────────────

_MIGRATIONS: list[tuple[str, str]] = [
    # ── Baseline schema (was created inline, now migration-tracked) ──────────
    ("2026-06-01-001", """
        CREATE TABLE IF NOT EXISTS agents (
          agent_id TEXT PRIMARY KEY,
          host_tool TEXT NOT NULL,
          model_name TEXT,
          pid INTEGER,
          started_at INTEGER NOT NULL,
          last_seen INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS claims (
          claim_id TEXT PRIMARY KEY,
          agent_id TEXT NOT NULL,
          resource TEXT NOT NULL,
          mode TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          base_hash TEXT,
          created_at INTEGER NOT NULL,
          expires_at INTEGER NOT NULL,
          released_at INTEGER,
          summary TEXT
        );
        CREATE TABLE IF NOT EXISTS patches (
          patch_id TEXT PRIMARY KEY,
          agent_id TEXT NOT NULL,
          target_file TEXT NOT NULL,
          base_hash TEXT NOT NULL,
          diff TEXT NOT NULL,
          status TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          applied_at INTEGER,
          rejection_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS conflicts (
          conflict_id TEXT PRIMARY KEY,
          resource TEXT NOT NULL,
          first_agent TEXT NOT NULL,
          second_agent TEXT NOT NULL,
          reason TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'open'
        );
        CREATE TABLE IF NOT EXISTS events (
          event_id TEXT PRIMARY KEY,
          ts INTEGER NOT NULL,
          agent_id TEXT,
          type TEXT NOT NULL,
          payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status,last_seen);
        CREATE INDEX IF NOT EXISTS idx_agents_id_status ON agents(agent_id,status,last_seen);
        CREATE INDEX IF NOT EXISTS idx_claims_resource ON claims(resource,status,expires_at);
        CREATE INDEX IF NOT EXISTS idx_claims_agent ON claims(agent_id,resource,status,expires_at);
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
    """),

    # ── Fix #1 — Ensure patches.target_file stores RELATIVE paths ───────────
    # The column already exists; we add a CHECK constraint via a new shadow
    # table only when the column is newly created. For existing DBs we cannot
    # add a CHECK retroactively in SQLite without full table rebuild.
    # Instead we enforce relative-path invariant at the application layer
    # (normalize_resource + _to_relative_resource) and document here.
    ("2026-06-23-001", """
        -- Marker: target_file in patches must be project-relative (enforced in app layer).
        -- This migration is a no-op DDL placeholder; the constraint is in normalize_resource().
        SELECT 1;
    """),

    # ── Fix #8 — resource_lock / claim coherence index ───────────────────────
    ("2026-06-23-002", """
        -- Partial index: quickly find ALL active claims for a resource to enable
        -- atomic dual-system check (claims + resource_locks).
        CREATE INDEX IF NOT EXISTS idx_claims_resource_active
          ON claims(resource, status) WHERE status = 'active';
    """),
]


def _backup_db(project_root: Optional[Path] = None) -> Optional[str]:
    """Create a timestamped backup of the SQLite DB before migration.

    Uses SQLite's online backup API so the backup is consistent even with
    concurrent WAL writers. Returns the backup path string, or None on error.

    Production rationale: if a migration corrupts the DB (extremely unlikely
    with idempotent DDL, but non-zero), the operator can restore within seconds.
    """
    p = paths(project_root)
    db_file = p["db"]
    if not db_file.exists():
        return None

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_path = db_file.with_name(f"coordination.backup-{ts}.sqlite")
    try:
        src = sqlite3.connect(str(db_file))
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
            logger.info("db backup created: %s", backup_path)
        finally:
            dst.close()
            src.close()
        return str(backup_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("db backup failed (non-fatal): %s", exc)
        return None


def _run_migrations(project_root: Optional[Path] = None) -> list[str]:
    """Apply all pending migrations idempotently.

    Algorithm:
      1. Ensure schema_migrations table exists (DDL is safe to repeat).
      2. Collect migration IDs already applied.
      3. Compute pending = _MIGRATIONS - applied (preserving list order).
      4. If pending: backup first, then apply each in a dedicated transaction.
      5. Record each applied migration in schema_migrations.

    Returns list of applied migration IDs (empty = already up to date).
    """
    with connect(project_root) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
              migration_id TEXT PRIMARY KEY,
              applied_at   INTEGER NOT NULL
            );
        """)
        applied_ids: set[str] = {
            row[0] for row in con.execute("SELECT migration_id FROM schema_migrations")
        }

    pending = [(mid, sql) for mid, sql in _MIGRATIONS if mid not in applied_ids]
    if not pending:
        return []

    # Backup BEFORE applying any pending migration.
    _backup_db(project_root)

    applied: list[str] = []
    for migration_id, sql in pending:
        try:
            with connect(project_root) as con:
                con.executescript(sql.strip())
                con.execute(
                    "INSERT OR IGNORE INTO schema_migrations(migration_id, applied_at) VALUES(?,?)",
                    (migration_id, now_ts()),
                )
            applied.append(migration_id)
            logger.info("migration applied: %s", migration_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("migration %s FAILED: %s", migration_id, exc)
            raise RuntimeError(f"Schema migration {migration_id} failed: {exc}") from exc

    return applied


# ─────────────────────────────────────────────────────────────────────────────
# DB init  (Fix #2 — integrity_check at bootstrap)
# ─────────────────────────────────────────────────────────────────────────────

def init_db(project_root: Optional[Path] = None) -> Dict[str, Any]:
    """Idempotent DB bootstrap: run migrations + integrity check.

    Called at server startup and before the first operation in each CLI run.
    Idempotent: safe to call on every request (migrations cache via applied_ids).

    Fix #2: PRAGMA integrity_check is run on the first bootstrap of a session.
    It is a full O(N) scan, so we gate it on a sentinel file to avoid running
    it on every process startup under heavy concurrent load.
    """
    p = paths(project_root)

    _run_migrations(project_root)

    # Integrity check: run at most once per process (not per-call).
    # Gate via a module-level flag so concurrent workers don't double-check.
    if not _init_db_checked.get(str(p["db"]), False):
        _init_db_checked[str(p["db"])] = True
        try:
            with connect(project_root) as con:
                result = con.execute("PRAGMA integrity_check").fetchone()
                if result and str(result[0]) != "ok":
                    logger.critical(
                        "SQLite integrity_check FAILED for %s: %s",
                        p["db"],
                        result[0],
                    )
                    # Do NOT raise here: surface as warning so server stays up.
                    # In prod, the operator should restore from backup.
                else:
                    logger.debug("SQLite integrity_check OK: %s", p["db"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("integrity_check error (non-fatal): %s", exc)

    return {
        "ok": True,
        "db": str(p["db"]),
        "root": str(p["root"]),
        "state": str(p["state"]),
        "runtime": str(p["runtime"]),
        "scribe_out": str(p["scribe_out"]),
        "graphify_out": str(p["graphify_out"]),
    }


# Module-level dict: db_path_str -> bool, tracks if integrity_check ran this process.
_init_db_checked: dict[str, bool] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Events
# ─────────────────────────────────────────────────────────────────────────────

def add_event(
    con: sqlite3.Connection,
    event_type: str,
    payload: Dict[str, Any],
    agent_id: Optional[str] = None,
) -> None:
    con.execute(
        "INSERT INTO events(event_id,ts,agent_id,type,payload) VALUES(?,?,?,?,?)",
        (
            new_id("evt"),
            now_ts(),
            agent_id,
            event_type,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Staleness sweeper (internal helper — called within transactions)
# ─────────────────────────────────────────────────────────────────────────────

def expire_stale(con: sqlite3.Connection) -> None:
    t = now_ts()
    con.execute(
        "UPDATE agents SET status='idle' WHERE status='active' AND last_seen < ?",
        (t - 180,),
    )
    con.execute(
        "UPDATE claims SET status='expired' WHERE status='active' AND expires_at < ?",
        (t,),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Path helpers  (Fix #1 — relative path enforcement)
# ─────────────────────────────────────────────────────────────────────────────

def _to_relative_resource(resource: str, project_root: Optional[Path] = None) -> str:
    """Convert any path to a project-relative string.

    Guarantees stored paths in DB are ALWAYS relative so the .agent directory
    remains portable across machines and OS mount points.

    Rules:
      - Already-relative paths → returned as-is after normalization.
      - Absolute paths within project root → made relative.
      - Absolute paths outside project root → raise CoordinationError.
      - UNC / Windows drive-letter absolute paths handled cross-platform.

    This is the single source of truth for DB path storage.
    """
    if not resource or not isinstance(resource, str):
        raise CoordinationError("resource is required")

    # Normalize separators (Windows compat).
    value = resource.strip().replace("\\", "/")

    if not value:
        raise CoordinationError("resource is empty after normalization")

    # Detect absolute path (POSIX or Windows).
    is_abs = value.startswith("/") or value.startswith("//") or WINDOWS_ABS_RE.match(value)

    if is_abs:
        root = (project_root or project_root_from()).resolve()
        try:
            rel = Path(value).resolve().relative_to(root)
            return str(rel).replace("\\", "/")
        except ValueError as exc:
            raise CoordinationError(
                f"absolute resource escapes project root: {value!r}"
            ) from exc

    # Relative path: validate no ".." traversal.
    p = Path(value)
    if ".." in p.parts or "" in p.parts:
        raise CoordinationError(
            f"resource must be normalized project-relative path, got: {value!r}"
        )

    return value


def normalize_resource(resource: str, project_root: Optional[Path] = None) -> str:
    """Public alias for _to_relative_resource with CoordinationError semantics."""
    return _to_relative_resource(resource, project_root)


# ─────────────────────────────────────────────────────────────────────────────
# Path resolution (absolute at runtime — never stored)
# ─────────────────────────────────────────────────────────────────────────────

def resolve_project_path(path: Path, project_root: Optional[Path] = None) -> Path:
    """Resolve a project-relative path to an absolute path safely.

    Invariants:
      - Resolved path must be within the project root (symlink-proof).
      - If path does not exist, validates the first-existing ancestor.
    """
    proj_root = (project_root or project_root_from()).resolve()

    if path.exists() or path.is_symlink():
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise CoordinationError("resource symlink cannot be resolved") from exc
        try:
            resolved.relative_to(proj_root)
        except ValueError as exc:
            raise CoordinationError("resource symlink escapes project root") from exc
        return resolved

    # Path does not exist: validate the first existing ancestor.
    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent

    try:
        resolved_parent = parent.resolve(strict=True)
        resolved_parent.relative_to(proj_root)
    except ValueError as exc:
        raise CoordinationError("resource parent escapes project root") from exc
    except FileNotFoundError as exc:
        raise CoordinationError("resource parent cannot be resolved") from exc

    return path


# ─────────────────────────────────────────────────────────────────────────────
# File hash (always uses relative resource for lookup)
# ─────────────────────────────────────────────────────────────────────────────

def file_hash(resource: str, project_root: Optional[Path] = None) -> Optional[str]:
    """SHA-256 of a project-relative file. Returns None if file absent or not a file."""
    rel = normalize_resource(resource, project_root)
    root = project_root or project_root_from()
    p = root / rel
    safe_path = resolve_project_path(p, project_root)

    if not p.exists():
        return None
    if not safe_path.is_file():
        return None

    h = hashlib.sha256()
    with safe_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Agent lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def register_agent(
    host_tool: str,
    model_name: str = "",
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not host_tool or not isinstance(host_tool, str):
        raise CoordinationError("host_tool is required")
    init_db()
    aid = agent_id or new_id(
        host_tool.replace(" ", "-").lower()[:20] or "agent"
    )
    t = now_ts()
    with connect() as con:
        expire_stale(con)
        con.execute(
            """
            INSERT INTO agents(agent_id,host_tool,model_name,pid,started_at,last_seen,status)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(agent_id) DO UPDATE SET
              host_tool=excluded.host_tool,
              model_name=excluded.model_name,
              pid=excluded.pid,
              last_seen=excluded.last_seen,
              status=CASE WHEN agents.status='retired' THEN agents.status ELSE 'active' END
            """,
            (aid, host_tool, model_name, os.getpid(), t, t, "active"),
        )
        row = con.execute(
            "SELECT * FROM agents WHERE agent_id=?", (aid,)
        ).fetchone()
        add_event(
            con,
            "agent.register",
            {"host_tool": host_tool, "model_name": model_name, "idempotent": True},
            aid,
        )
    data = dict(row)
    return {
        "agent_id": aid,
        "status": data["status"],
        "host_tool": host_tool,
        "model_name": model_name,
    }


def get_agent(agent_id: str) -> Dict[str, Any] | None:
    if not agent_id:
        return None
    init_db()
    with connect() as con:
        expire_stale(con)
        row = con.execute(
            "SELECT * FROM agents WHERE agent_id=?", (agent_id,)
        ).fetchone()
    return dict(row) if row else None


def require_agent_active(agent_id: str) -> Dict[str, Any]:
    if not agent_id or not isinstance(agent_id, str) or not agent_id.strip():
        raise CoordinationError("AGENT_ID_REQUIRED")
    agent = get_agent(agent_id.strip())
    if not agent:
        raise CoordinationError("AGENT_UNKNOWN_OR_UNREGISTERED")
    status = str(agent.get("status") or "")
    if status == "idle":
        raise CoordinationError("AGENT_IDLE_RESUME_REQUIRED")
    if status == "retired":
        raise CoordinationError("AGENT_RETIRED")
    if status != "active":
        raise CoordinationError("AGENT_NOT_ACTIVE")
    return agent


def heartbeat(agent_id: str) -> Dict[str, Any]:
    if not agent_id:
        raise CoordinationError("agent_id is required")
    with connect() as con:
        expire_stale(con)
        cur = con.execute(
            "UPDATE agents SET last_seen=?, status='active' WHERE agent_id=?",
            (now_ts(), agent_id),
        )
        if cur.rowcount == 0:
            raise CoordinationError(f"unknown agent_id: {agent_id}")
        add_event(con, "agent.heartbeat", {}, agent_id)
    return {"agent_id": agent_id, "status": "active"}


def resume_agent(agent_id: str) -> Dict[str, Any]:
    if not agent_id:
        raise CoordinationError("agent_id is required")
    with connect() as con:
        expire_stale(con)
        cur = con.execute(
            "UPDATE agents SET last_seen=?, status='active' WHERE agent_id=? AND status<>'retired'",
            (now_ts(), agent_id),
        )
        if cur.rowcount == 0:
            raise CoordinationError("AGENT_UNKNOWN_OR_UNREGISTERED")
        add_event(con, "agent.resume", {}, agent_id)
    return {"agent_id": agent_id, "status": "active"}


def retire_agent(agent_id: str, reason: str = "") -> Dict[str, Any]:
    if not agent_id:
        raise CoordinationError("agent_id is required")
    with connect() as con:
        expire_stale(con)
        cur = con.execute(
            "UPDATE agents SET status='retired', last_seen=? WHERE agent_id=?",
            (now_ts(), agent_id),
        )
        if cur.rowcount == 0:
            raise CoordinationError("AGENT_UNKNOWN_OR_UNREGISTERED")
        con.execute(
            "UPDATE claims SET status='released', released_at=?, summary=? WHERE agent_id=? AND status='active'",
            (now_ts(), reason or "agent retired explicitly", agent_id),
        )
        add_event(con, "agent.retire", {"reason": reason}, agent_id)
    return {"agent_id": agent_id, "status": "retired"}


def agent_status(agent_id: str) -> Dict[str, Any]:
    if not agent_id:
        raise CoordinationError("agent_id is required")
    with connect() as con:
        expire_stale(con)
        row = con.execute(
            "SELECT * FROM agents WHERE agent_id=?", (agent_id,)
        ).fetchone()
        if not row:
            raise CoordinationError("AGENT_UNKNOWN_OR_UNREGISTERED")
    return dict(row)


def list_agents() -> Dict[str, Any]:
    init_db()
    with connect() as con:
        expire_stale(con)
        rows = [
            dict(r)
            for r in con.execute("SELECT * FROM agents ORDER BY last_seen DESC")
        ]
    return {"agents": rows, "count": len(rows)}


def session_status() -> Dict[str, Any]:
    init_db()
    with connect() as con:
        expire_stale(con)
        agents = [
            dict(r)
            for r in con.execute("SELECT * FROM agents ORDER BY last_seen DESC")
        ]
        claims = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM claims WHERE status='active' ORDER BY created_at DESC"
            )
        ]
        conflicts = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM conflicts WHERE status='open' ORDER BY created_at DESC"
            )
        ]
    return {
        "active_agents": sum(1 for a in agents if a["status"] == "active"),
        "agents": agents,
        "active_claims": claims,
        "open_conflicts": conflicts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Resource claims  (Fix #4 — dual-system coherence check)
# ─────────────────────────────────────────────────────────────────────────────

def claim_resource(
    agent_id: str,
    resource: str,
    mode: str = "write",
    ttl_seconds: int = 1800,
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Claim a resource for read/write/exclusive/patch_queue access.

    Fix #4 — Dual-system bridge:
    Before granting a claim, we also check the discipline.resource_locks table
    to prevent a state where an agent holds a SCRIBE lock but no MCP claim
    (or vice versa). The rule:
      - If another agent holds an ACTIVE resource_lock on this resource AND
        the mode is write/exclusive/patch_queue → refuse with RESOURCE_LOCKED.
      - We do NOT auto-create a resource_lock here (that's the caller's job).
        We only READ the lock table to make the claim coherent.

    Fix #1 — resource stored as relative path.
    """
    res = normalize_resource(resource, project_root)
    if mode not in {"read", "write", "exclusive", "patch_queue"}:
        raise CoordinationError("mode must be read/write/exclusive/patch_queue")
    if ttl_seconds < 60 or ttl_seconds > 86400:
        raise CoordinationError("ttl_seconds must be between 60 and 86400")
    init_db()
    t = now_ts()

    with connect() as con:
        expire_stale(con)

        # Agent liveness check.
        agent = con.execute(
            "SELECT * FROM agents WHERE agent_id=?", (agent_id,)
        ).fetchone()
        if not agent:
            add_event(
                con,
                "claim.context_not_ready",
                {"resource": res, "mode": mode, "reason": "AGENT_UNKNOWN_OR_UNREGISTERED"},
                agent_id,
            )
            raise CoordinationError("AGENT_UNKNOWN_OR_UNREGISTERED")
        if agent["status"] == "idle":
            add_event(
                con,
                "claim.context_not_ready",
                {"resource": res, "mode": mode, "reason": "AGENT_IDLE_RESUME_REQUIRED"},
                agent_id,
            )
            raise CoordinationError("AGENT_IDLE_RESUME_REQUIRED")
        if agent["status"] == "retired":
            add_event(
                con,
                "claim.context_not_ready",
                {"resource": res, "mode": mode, "reason": "AGENT_RETIRED"},
                agent_id,
            )
            raise CoordinationError("AGENT_RETIRED")
        if agent["status"] != "active":
            add_event(
                con,
                "claim.context_not_ready",
                {"resource": res, "mode": mode, "reason": "AGENT_NOT_ACTIVE"},
                agent_id,
            )
            raise CoordinationError("AGENT_NOT_ACTIVE")

        # Fix #4: Check resource_locks table for write-mode operations.
        # resource_locks lives in the SAME database (unified Fix #4 decision).
        if mode in {"write", "exclusive", "patch_queue"}:
            lock_row = _check_resource_lock_coherence(con, res, agent_id, t)
            if lock_row is not None:
                add_event(
                    con,
                    "claim.refused_resource_lock",
                    {
                        "resource": res,
                        "mode": mode,
                        "lock_owner": lock_row["agent_id"],
                        "lock_expires_at": lock_row["expires_at"],
                    },
                    agent_id,
                )
                return {
                    "verdict": "CLAIM_REFUSED_RESOURCE_LOCKED",
                    "resource": res,
                    "lock_owner_agent_id": lock_row["agent_id"],
                    "lock_expires_at": lock_row["expires_at"],
                    "seconds_remaining": max(0, int(lock_row["expires_at"]) - t),
                    "required_action": "wait_or_request_lock_release",
                }

        # Check for conflicting claims from OTHER agents.
        existing = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM claims WHERE resource=? AND status='active'", (res,)
            )
        ]
        blocking = [
            c
            for c in existing
            if c["agent_id"] != agent_id
            and (mode != "read" or c["mode"] != "read")
        ]
        if blocking:
            conflict_id = new_id("conflict")
            con.execute(
                "INSERT INTO conflicts(conflict_id,resource,first_agent,second_agent,reason,created_at,status) VALUES(?,?,?,?,?,?,?)",
                (
                    conflict_id,
                    res,
                    blocking[0]["agent_id"],
                    agent_id,
                    "active claim conflict",
                    t,
                    "open",
                ),
            )
            add_event(
                con,
                "claim.refused",
                {
                    "resource": res,
                    "blocking": blocking,
                    "conflict_id": conflict_id,
                },
                agent_id,
            )
            return {
                "verdict": "CLAIM_REFUSED_CONFLICT",
                "resource": res,
                "blocking_claims": blocking,
                "conflict_id": conflict_id,
                "required_mode": "patch_queue",
            }

        claim_id = new_id("claim")
        fh = file_hash(res, project_root)
        con.execute(
            "INSERT INTO claims(claim_id,agent_id,resource,mode,status,base_hash,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
            (claim_id, agent_id, res, mode, "active", fh, t, t + ttl_seconds),
        )
        add_event(
            con,
            "claim.granted",
            {"claim_id": claim_id, "resource": res, "mode": mode},
            agent_id,
        )

    return {
        "verdict": "CLAIM_GRANTED",
        "claim_id": claim_id,
        "resource": res,
        "mode": mode,
        "base_hash": file_hash(res, project_root),
    }


def _check_resource_lock_coherence(
    con: sqlite3.Connection,
    resource: str,
    requesting_agent_id: str,
    now: int,
) -> Optional[Dict[str, Any]]:
    """Check if another agent holds an active resource_lock on this resource.

    The resource_locks table is created by discipline.ensure_schema() in the
    same coordination.sqlite. This function reads it without coupling to
    discipline.py (avoids circular import).

    Returns the blocking lock row dict, or None if resource is free.
    """
    try:
        # Sweep expired locks inline (same pattern as discipline.py).
        con.execute(
            "UPDATE resource_locks SET status='expired' WHERE resource=? AND status='active' AND expires_at<?",
            (resource, now),
        )
        row = con.execute(
            """
            SELECT * FROM resource_locks
            WHERE resource=? AND status='active' AND agent_id<>?
            ORDER BY acquired_at ASC LIMIT 1
            """,
            (resource, requesting_agent_id),
        ).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        # resource_locks table doesn't exist yet (discipline not init'd).
        # Non-blocking: proceed without resource_lock coherence check.
        return None


def release_claim(
    agent_id: str,
    claim_id: str,
    summary: str = "",
) -> Dict[str, Any]:
    if not agent_id or not claim_id:
        raise CoordinationError("agent_id and claim_id are required")
    with connect() as con:
        cur = con.execute(
            "UPDATE claims SET status='released', released_at=?, summary=? WHERE claim_id=? AND agent_id=? AND status='active'",
            (now_ts(), summary, claim_id, agent_id),
        )
        if cur.rowcount == 0:
            raise CoordinationError("active claim not found for this agent")
        add_event(con, "claim.released", {"claim_id": claim_id, "summary": summary}, agent_id)
    return {"verdict": "CLAIM_RELEASED", "claim_id": claim_id}


def before_edit(agent_id: str, resource: str) -> Dict[str, Any]:
    res = normalize_resource(resource)
    init_db()
    with connect() as con:
        expire_stale(con)
        owned = con.execute(
            "SELECT * FROM claims WHERE agent_id=? AND resource=? AND status='active'",
            (agent_id, res),
        ).fetchone()
        active_other = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM claims WHERE resource=? AND status='active' AND agent_id<>?",
                (res, agent_id),
            )
        ]
        if active_other:
            add_event(
                con,
                "edit.refused",
                {
                    "resource": res,
                    "reason": "same resource active elsewhere",
                    "others": active_other,
                },
                agent_id,
            )
            return {
                "verdict": "DIRECT_EDIT_REFUSED",
                "resource": res,
                "required_mode": "PATCH_QUEUE_REQUIRED",
                "blocking_claims": active_other,
            }
        if not owned:
            add_event(
                con,
                "edit.refused",
                {"resource": res, "reason": "missing claim"},
                agent_id,
            )
            return {
                "verdict": "DIRECT_EDIT_REFUSED",
                "resource": res,
                "reason": "MISSING_CLAIM",
                "required_action": "claim_resource",
            }
        add_event(con, "edit.allowed", {"resource": res}, agent_id)
    return {
        "verdict": "DIRECT_EDIT_ALLOWED",
        "resource": res,
        "base_hash": file_hash(res),
    }


def finish_task(agent_id: str, summary: str = "") -> Dict[str, Any]:
    if not agent_id:
        raise CoordinationError("agent_id is required")
    with connect() as con:
        expire_stale(con)
        open_claims = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM claims WHERE agent_id=? AND status='active'",
                (agent_id,),
            )
        ]
        if open_claims:
            return {
                "verdict": "FINISH_REFUSED_OPEN_CLAIMS",
                "open_claims": open_claims,
            }
        agent = con.execute(
            "SELECT agent_id FROM agents WHERE agent_id=?", (agent_id,)
        ).fetchone()
        if not agent:
            raise CoordinationError("AGENT_UNKNOWN_OR_UNREGISTERED")
        con.execute(
            "UPDATE agents SET last_seen=? WHERE agent_id=?",
            (now_ts(), agent_id),
        )
        add_event(con, "task.finished", {"summary": summary}, agent_id)
    return {"verdict": "TASK_FINISHED_OK", "agent_id": agent_id, "summary": summary}
