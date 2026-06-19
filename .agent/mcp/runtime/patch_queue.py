#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

NEW_FILE_HASH = "__new_file__"
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
MAX_DIFF_BYTES = 2_000_000

class PatchQueueError(RuntimeError):
    pass

def now_ts() -> int:
    return int(time.time())

def root() -> Path:
    current = Path.cwd().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".agent").is_dir():
            return candidate
    return current

def db_path() -> Path:
    path = root() / ".agent" / "runtime" / "coordination.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path()), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con

def ensure_schema() -> None:
    with connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS patches_v2(
              patch_id TEXT PRIMARY KEY,
              agent_id TEXT NOT NULL,
              target_path TEXT NOT NULL,
              base_hash TEXT NOT NULL,
              diff_text TEXT NOT NULL,
              changed_ranges_json TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              reason TEXT,
              metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_patches_v2_target ON patches_v2(target_path,status);
            CREATE TABLE IF NOT EXISTS conflicts_v2(
              conflict_id TEXT PRIMARY KEY,
              resource TEXT NOT NULL,
              status TEXT NOT NULL,
              detected_at INTEGER NOT NULL,
              agents_json TEXT NOT NULL,
              reason TEXT NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_conflicts_v2_resource ON conflicts_v2(resource,status);
            """
        )

def safe_resource(resource: str) -> str:
    if not isinstance(resource, str) or not resource.strip():
        raise PatchQueueError("resource is required")
    value = resource.strip().replace("\\", "/")
    path = Path(value)
    if path.is_absolute():
        try:
            return str(path.resolve().relative_to(root())).replace("\\", "/")
        except ValueError as exc:
            raise PatchQueueError("absolute resource escapes project root") from exc
    if any(part in {"", ".."} for part in path.parts):
        raise PatchQueueError("resource must be normalized and project-relative")
    return value

def file_hash(resource: str) -> dict[str, Any]:
    res = safe_resource(resource)
    path = root() / res
    if not path.exists():
        return {"resource": res, "exists": False, "hash": NEW_FILE_HASH, "size_bytes": 0}
    if not path.is_file():
        raise PatchQueueError("resource is not a file")
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return {"resource": res, "exists": True, "hash": h.hexdigest(), "size_bytes": path.stat().st_size}

def parse_ranges(diff_text: str) -> list[dict[str, int]]:
    ranges: list[dict[str, int]] = []
    for line in diff_text.splitlines():
        if line.startswith("@@ "):
            match = HUNK_RE.match(line)
            if not match:
                raise PatchQueueError("invalid unified diff hunk header")
            old_start = int(match.group(1))
            old_count = int(match.group(2) or "1")
            new_start = int(match.group(3))
            new_count = int(match.group(4) or "1")
            ranges.append({"old_start": old_start, "old_end": old_start + max(old_count, 1) - 1, "new_start": new_start, "new_end": new_start + max(new_count, 1) - 1})
    if not ranges:
        raise PatchQueueError("diff must contain at least one unified hunk")
    return ranges

def overlaps(left: list[dict[str, int]], right: list[dict[str, int]]) -> bool:
    for a in left:
        for b in right:
            if max(a["old_start"], b["old_start"]) <= min(a["old_end"], b["old_end"]):
                return True
    return False

def require_claim(con: sqlite3.Connection, agent_id: str, resource: str) -> None:
    rows = con.execute("SELECT mode FROM claims WHERE agent_id=? AND resource=? AND status='active'", (agent_id, resource)).fetchall()
    modes = {row["mode"] for row in rows}
    if not modes.intersection({"write", "exclusive", "patch_queue"}):
        raise PatchQueueError("write/exclusive/patch_queue claim required before proposing a patch")

def propose_patch(agent_id: str, target: str, base_hash: str, diff_text: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_schema()
    if not agent_id:
        raise PatchQueueError("agent_id is required")
    resource = safe_resource(target)
    if not base_hash:
        raise PatchQueueError("base_hash is required")
    if not diff_text or len(diff_text.encode("utf-8")) > MAX_DIFF_BYTES:
        raise PatchQueueError("diff_text missing or too large")
    current = file_hash(resource)["hash"]
    if current != base_hash:
        return {"ok": False, "status": "PATCH_REJECTED_BASE_HASH_MISMATCH", "target_path": resource, "provided_base_hash": base_hash, "current_hash": current}
    ranges = parse_ranges(diff_text)
    patch_id = f"patch-{now_ts()}-{hashlib.sha256((agent_id + resource + diff_text).encode()).hexdigest()[:10]}"
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            require_claim(con, agent_id, resource)
            active = con.execute("SELECT * FROM patches_v2 WHERE target_path=? AND status='proposed'", (resource,)).fetchall()
            overlapping = []
            for row in active:
                old_ranges = json.loads(row["changed_ranges_json"] or "[]")
                if row["base_hash"] == base_hash and overlaps(ranges, old_ranges):
                    overlapping.append({"patch_id": row["patch_id"], "agent_id": row["agent_id"], "changed_ranges": old_ranges})
            status = "conflict" if overlapping else "proposed"
            reason = "overlaps existing proposed patch" if overlapping else None
            ts = now_ts()
            con.execute("INSERT INTO patches_v2(patch_id,agent_id,target_path,base_hash,diff_text,changed_ranges_json,status,created_at,updated_at,reason,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (patch_id, agent_id, resource, base_hash, diff_text, json.dumps(ranges), status, ts, ts, reason, json.dumps(metadata or {})))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return {"ok": status == "proposed", "status": "PATCH_PROPOSED" if status == "proposed" else "PATCH_CONFLICT", "patch_id": patch_id, "target_path": resource, "base_hash": base_hash, "changed_ranges": ranges, "overlapping_patches": overlapping}

def list_patches(target: str | None = None, status: str | None = None) -> dict[str, Any]:
    ensure_schema()
    where = []
    params: list[Any] = []
    if target:
        where.append("target_path=?")
        params.append(safe_resource(target))
    if status:
        where.append("status=?")
        params.append(status)
    query = "SELECT patch_id,agent_id,target_path,base_hash,changed_ranges_json,status,created_at,updated_at,reason,metadata_json FROM patches_v2"
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY created_at ASC"
    with connect() as con:
        rows = con.execute(query, tuple(params)).fetchall()
    patches = []
    for row in rows:
        patches.append({"patch_id": row["patch_id"], "agent_id": row["agent_id"], "target_path": row["target_path"], "base_hash": row["base_hash"], "changed_ranges": json.loads(row["changed_ranges_json"] or "[]"), "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"], "reason": row["reason"], "metadata": json.loads(row["metadata_json"] or "{}")})
    return {"ok": True, "status": "PATCHES_LISTED", "count": len(patches), "patches": patches}
