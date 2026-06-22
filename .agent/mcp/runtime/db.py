from __future__ import annotations

import hashlib
import json
import os
import re
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


WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:/")


class CoordinationError(RuntimeError):
    pass


def now_ts() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def paths(project_root: Optional[Path] = None) -> Dict[str, Path]:
    return prepare_state_dirs(project_root)


@contextmanager
def connect(project_root: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    p = paths(project_root)
    con = sqlite3.connect(str(p["db"]), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        yield con
    finally:
        con.close()


def init_db(project_root: Optional[Path] = None) -> Dict[str, Any]:
    p = paths(project_root)
    with connect(project_root) as con:
        con.executescript(
            """
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
            """
        )
    return {"ok": True, "db": str(p["db"]), "root": str(p["root"]), "state": str(p["state"]), "runtime": str(p["runtime"]), "scribe_out": str(p["scribe_out"]), "graphify_out": str(p["graphify_out"])}


def add_event(con: sqlite3.Connection, event_type: str, payload: Dict[str, Any], agent_id: Optional[str] = None) -> None:
    con.execute(
        "INSERT INTO events(event_id,ts,agent_id,type,payload) VALUES(?,?,?,?,?)",
        (new_id("evt"), now_ts(), agent_id, event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )


def expire_stale(con: sqlite3.Connection) -> None:
    t = now_ts()
    con.execute("UPDATE agents SET status='idle' WHERE status='active' AND last_seen < ?", (t - 180,))
    con.execute("UPDATE claims SET status='expired' WHERE status='active' AND expires_at < ?", (t,))


def register_agent(host_tool: str, model_name: str = "", agent_id: Optional[str] = None) -> Dict[str, Any]:
    if not host_tool or not isinstance(host_tool, str):
        raise CoordinationError("host_tool is required")
    init_db()
    aid = agent_id or new_id(host_tool.replace(" ", "-").lower()[:20] or "agent")
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
        row = con.execute("SELECT * FROM agents WHERE agent_id=?", (aid,)).fetchone()
        add_event(con, "agent.register", {"host_tool": host_tool, "model_name": model_name, "idempotent": True}, aid)
    data = dict(row)
    return {"agent_id": aid, "status": data["status"], "host_tool": host_tool, "model_name": model_name}


def get_agent(agent_id: str) -> Dict[str, Any] | None:
    if not agent_id:
        return None
    init_db()
    with connect() as con:
        expire_stale(con)
        row = con.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
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
        cur = con.execute("UPDATE agents SET last_seen=?, status='active' WHERE agent_id=?", (now_ts(), agent_id))
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
        row = con.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if not row:
            raise CoordinationError("AGENT_UNKNOWN_OR_UNREGISTERED")
    return dict(row)


def list_agents() -> Dict[str, Any]:
    init_db()
    with connect() as con:
        expire_stale(con)
        rows = [dict(r) for r in con.execute("SELECT * FROM agents ORDER BY last_seen DESC")]
    return {"agents": rows, "count": len(rows)}

def session_status() -> Dict[str, Any]:
    init_db()
    with connect() as con:
        expire_stale(con)
        agents = [dict(r) for r in con.execute("SELECT * FROM agents ORDER BY last_seen DESC")]
        claims = [dict(r) for r in con.execute("SELECT * FROM claims WHERE status='active' ORDER BY created_at DESC")]
        conflicts = [dict(r) for r in con.execute("SELECT * FROM conflicts WHERE status='open' ORDER BY created_at DESC")]
    return {"active_agents": sum(1 for a in agents if a["status"] == "active"), "agents": agents, "active_claims": claims, "open_conflicts": conflicts}


def normalize_resource(resource: str) -> str:
    if not resource or not isinstance(resource, str):
        raise CoordinationError("resource is required")
    value = resource.strip().replace("\\", "/")
    if (
        not value
        or value.startswith("/")
        or value.startswith("//")
        or WINDOWS_ABS_RE.match(value)
        or ".." in Path(value).parts
    ):
        raise CoordinationError("resource must be a safe project-relative path or semantic name")
    return value


def resolve_project_path(path: Path) -> Path:
    project_root = project_root_from().resolve()

    if path.exists() or path.is_symlink():
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise CoordinationError("resource symlink cannot be resolved") from exc
        try:
            resolved.relative_to(project_root)
        except ValueError as exc:
            raise CoordinationError("resource symlink escapes project root") from exc
        return resolved

    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent

    try:
        resolved_parent = parent.resolve(strict=True)
        resolved_parent.relative_to(project_root)
    except ValueError as exc:
        raise CoordinationError("resource parent escapes project root") from exc
    except FileNotFoundError as exc:
        raise CoordinationError("resource parent cannot be resolved") from exc

    return path


def file_hash(resource: str) -> Optional[str]:
    p = project_root_from() / resource
    safe_path = resolve_project_path(p)

    if not p.exists():
        return None
    if not safe_path.is_file():
        return None

    h = hashlib.sha256()
    with safe_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def claim_resource(agent_id: str, resource: str, mode: str = "write", ttl_seconds: int = 1800) -> Dict[str, Any]:
    res = normalize_resource(resource)
    if mode not in {"read", "write", "exclusive", "patch_queue"}:
        raise CoordinationError("mode must be read/write/exclusive/patch_queue")
    if ttl_seconds < 60 or ttl_seconds > 86400:
        raise CoordinationError("ttl_seconds must be between 60 and 86400")
    init_db()
    t = now_ts()
    with connect() as con:
        expire_stale(con)
        agent = con.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if not agent:
            add_event(con, "claim.context_not_ready", {"resource": res, "mode": mode, "reason": "AGENT_UNKNOWN_OR_UNREGISTERED"}, agent_id)
            raise CoordinationError("AGENT_UNKNOWN_OR_UNREGISTERED")
        if agent["status"] == "idle":
            add_event(con, "claim.context_not_ready", {"resource": res, "mode": mode, "reason": "AGENT_IDLE_RESUME_REQUIRED"}, agent_id)
            raise CoordinationError("AGENT_IDLE_RESUME_REQUIRED")
        if agent["status"] == "retired":
            add_event(con, "claim.context_not_ready", {"resource": res, "mode": mode, "reason": "AGENT_RETIRED"}, agent_id)
            raise CoordinationError("AGENT_RETIRED")
        if agent["status"] != "active":
            add_event(con, "claim.context_not_ready", {"resource": res, "mode": mode, "reason": "AGENT_NOT_ACTIVE"}, agent_id)
            raise CoordinationError("AGENT_NOT_ACTIVE")
        existing = [dict(r) for r in con.execute("SELECT * FROM claims WHERE resource=? AND status='active'", (res,))]
        blocking = [c for c in existing if c["agent_id"] != agent_id and (mode != "read" or c["mode"] != "read")]
        if blocking:
            conflict_id = new_id("conflict")
            con.execute(
                "INSERT INTO conflicts(conflict_id,resource,first_agent,second_agent,reason,created_at,status) VALUES(?,?,?,?,?,?,?)",
                (conflict_id, res, blocking[0]["agent_id"], agent_id, "active claim conflict", t, "open"),
            )
            add_event(con, "claim.refused", {"resource": res, "blocking": blocking, "conflict_id": conflict_id}, agent_id)
            return {"verdict": "CLAIM_REFUSED_CONFLICT", "resource": res, "blocking_claims": blocking, "conflict_id": conflict_id, "required_mode": "patch_queue"}
        claim_id = new_id("claim")
        con.execute(
            "INSERT INTO claims(claim_id,agent_id,resource,mode,status,base_hash,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
            (claim_id, agent_id, res, mode, "active", file_hash(res), t, t + ttl_seconds),
        )
        add_event(con, "claim.granted", {"claim_id": claim_id, "resource": res, "mode": mode}, agent_id)
    return {"verdict": "CLAIM_GRANTED", "claim_id": claim_id, "resource": res, "mode": mode, "base_hash": file_hash(res)}


def release_claim(agent_id: str, claim_id: str, summary: str = "") -> Dict[str, Any]:
    if not agent_id or not claim_id:
        raise CoordinationError("agent_id and claim_id are required")
    with connect() as con:
        cur = con.execute("UPDATE claims SET status='released', released_at=?, summary=? WHERE claim_id=? AND agent_id=? AND status='active'", (now_ts(), summary, claim_id, agent_id))
        if cur.rowcount == 0:
            raise CoordinationError("active claim not found for this agent")
        add_event(con, "claim.released", {"claim_id": claim_id, "summary": summary}, agent_id)
    return {"verdict": "CLAIM_RELEASED", "claim_id": claim_id}


def before_edit(agent_id: str, resource: str) -> Dict[str, Any]:
    res = normalize_resource(resource)
    init_db()
    with connect() as con:
        expire_stale(con)
        owned = con.execute("SELECT * FROM claims WHERE agent_id=? AND resource=? AND status='active'", (agent_id, res)).fetchone()
        active_other = [dict(r) for r in con.execute("SELECT * FROM claims WHERE resource=? AND status='active' AND agent_id<>?", (res, agent_id))]
        if active_other:
            add_event(con, "edit.refused", {"resource": res, "reason": "same resource active elsewhere", "others": active_other}, agent_id)
            return {"verdict": "DIRECT_EDIT_REFUSED", "resource": res, "required_mode": "PATCH_QUEUE_REQUIRED", "blocking_claims": active_other}
        if not owned:
            add_event(con, "edit.refused", {"resource": res, "reason": "missing claim"}, agent_id)
            return {"verdict": "DIRECT_EDIT_REFUSED", "resource": res, "reason": "MISSING_CLAIM", "required_action": "claim_resource"}
        add_event(con, "edit.allowed", {"resource": res}, agent_id)
    return {"verdict": "DIRECT_EDIT_ALLOWED", "resource": res, "base_hash": file_hash(res)}


def finish_task(agent_id: str, summary: str = "") -> Dict[str, Any]:
    if not agent_id:
        raise CoordinationError("agent_id is required")
    with connect() as con:
        expire_stale(con)
        open_claims = [dict(r) for r in con.execute("SELECT * FROM claims WHERE agent_id=? AND status='active'", (agent_id,))]
        if open_claims:
            return {"verdict": "FINISH_REFUSED_OPEN_CLAIMS", "open_claims": open_claims}
        agent = con.execute("SELECT agent_id FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if not agent:
            raise CoordinationError("AGENT_UNKNOWN_OR_UNREGISTERED")
        con.execute("UPDATE agents SET last_seen=? WHERE agent_id=?", (now_ts(), agent_id))
        add_event(con, "task.finished", {"summary": summary}, agent_id)
    return {"verdict": "TASK_FINISHED_OK", "agent_id": agent_id, "summary": summary}
