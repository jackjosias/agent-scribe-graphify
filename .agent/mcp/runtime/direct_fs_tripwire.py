from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from . import db, patch_queue
except Exception:
    import db  # type: ignore
    import patch_queue  # type: ignore

DIRECT_WRITE_BYPASS_DETECTED = "DIRECT_WRITE_BYPASS_DETECTED"
TRIPWIRE_CLEAN = "DIRECT_FS_TRIPWIRE_CLEAN"
MUTATING_INTENTS = {"write", "edit", "patch", "modify", "code", "fix", "refactor", "test", "create", "delete", "remove", "decision"}
MEMOIRE_FILE = "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
IGNORED_PREFIXES = (
    ".git/",
    ".agent/state/",
    ".pytest_cache/",
)
IGNORED_PARTS = {"__pycache__"}
IGNORED_SUFFIXES = (".pyc", ".pyo")


def _now() -> int:
    return int(time.time())


def is_mutating_intent(intent: str) -> bool:
    return (intent or "").strip().lower() in MUTATING_INTENTS


def _ensure_schema(project_root: Path | None = None) -> None:
    db.init_db(project_root)
    with db.connect(project_root) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS direct_fs_tripwire_snapshots_v1(
              task_id TEXT PRIMARY KEY,
              agent_id TEXT NOT NULL,
              resource TEXT,
              baseline_status_json TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS direct_fs_authorized_mutations_v1(
              id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              agent_id TEXT NOT NULL,
              resource TEXT NOT NULL,
              tool TEXT NOT NULL,
              patch_id TEXT,
              before_hash TEXT,
              after_hash TEXT,
              created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_direct_fs_authorized_task
              ON direct_fs_authorized_mutations_v1(task_id,agent_id,resource);
            """
        )


def _project_root(project_root: Path | None = None) -> Path:
    return (project_root or Path.cwd()).resolve()


def _safe_resource(resource: str) -> str:
    return patch_queue.safe_resource(resource) if resource else ""


def _normalize_path(path: str) -> str:
    normalized = path.replace(os.sep, "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _ignored(path: str) -> bool:
    normalized = _normalize_path(path)
    if not normalized:
        return True
    if any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in IGNORED_PREFIXES):
        return True
    parts = set(normalized.split("/"))
    if parts.intersection(IGNORED_PARTS):
        return True
    return normalized.endswith(IGNORED_SUFFIXES)


def _normalize_hash_value(value: str) -> str:
    if not value:
        return ""
    if value.startswith("sha256:") or value.startswith("symlink:") or value == "__new_file__":
        return value
    return "sha256:" + value


def _file_hash(root: Path, rel_path: str) -> str:
    path = root / rel_path
    if path.is_symlink():
        return "symlink:" + os.readlink(path)
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return ""
    if not resolved.is_file():
        return ""
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _git_status(root: Path) -> list[dict[str, str]]:
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=str(root), text=False, capture_output=True, timeout=15,
    )
    if proc.returncode != 0:
        return []
    entries: list[dict[str, str]] = []
    parts = [part for part in proc.stdout.split(b"\0") if part]
    index = 0
    while index < len(parts):
        raw = parts[index].decode("utf-8", "replace")
        status = raw[:2]
        path = raw[3:]
        if status.startswith("R") or status.startswith("C"):
            index += 1
        normalized = path.replace(os.sep, "/")
        if not _ignored(normalized):
            entries.append({"path": normalized, "status": status, "hash": _file_hash(root, normalized)})
        index += 1
    entries.sort(key=lambda item: item["path"])
    return entries


def _load_snapshot(con: Any, task_id: str, agent_id: str) -> dict[str, Any] | None:
    row = con.execute(
        "SELECT * FROM direct_fs_tripwire_snapshots_v1 WHERE task_id=? AND agent_id=?",
        (task_id, agent_id),
    ).fetchone()
    return dict(row) if row else None


def _authorized(con: Any, task_id: str, agent_id: str) -> list[dict[str, str]]:
    rows = con.execute(
        "SELECT resource,tool,patch_id,before_hash,after_hash FROM direct_fs_authorized_mutations_v1 WHERE task_id=? AND agent_id=?",
        (task_id, agent_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _all_authorized(con: Any) -> list[dict[str, str]]:
    rows = con.execute(
        "SELECT task_id,agent_id,resource,tool,patch_id,before_hash,after_hash FROM direct_fs_authorized_mutations_v1"
    ).fetchall()
    return [dict(row) for row in rows]


def _status_key(entries: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {entry["path"]: entry for entry in entries}


def _changed_since_baseline(entry: dict[str, str], baseline_map: dict[str, dict[str, str]]) -> bool:
    base = baseline_map.get(entry["path"])
    if not base:
        return True
    return base.get("status") != entry.get("status") or base.get("hash") != entry.get("hash")


def workspace_snapshot(project_root: Path | None, task_id: str, agent_id: str, resource: str = "") -> dict[str, Any]:
    root = _project_root(project_root)
    if not task_id or not agent_id:
        raise ValueError("task_id and agent_id are required")
    _ensure_schema(root)
    safe_resource = _safe_resource(resource)
    baseline = _git_status(root)
    with db.connect(root) as con:
        existing = _load_snapshot(con, task_id, agent_id)
        if existing:
            return {"verdict": "DIRECT_FS_TRIPWIRE_SNAPSHOT_EXISTS", "task_id": task_id, "agent_id": agent_id, "baseline": json.loads(existing["baseline_status_json"])}
        con.execute(
            "INSERT INTO direct_fs_tripwire_snapshots_v1(task_id,agent_id,resource,baseline_status_json,created_at) VALUES(?,?,?,?,?)",
            (task_id, agent_id, safe_resource, json.dumps(baseline, ensure_ascii=False, sort_keys=True), _now()),
        )
        db.add_event(con, "direct_fs_tripwire.snapshot", {"task_id": task_id, "resource": safe_resource, "count": len(baseline)}, agent_id)
    return {"verdict": "DIRECT_FS_TRIPWIRE_SNAPSHOT_CREATED", "task_id": task_id, "agent_id": agent_id, "baseline": baseline}


def record_authorized_mutation(task_id: str, agent_id: str, resource: str, tool: str, patch_id: str = "", before_hash: str = "", after_hash: str = "", project_root: Path | None = None) -> dict[str, Any]:
    root = _project_root(project_root)
    if not task_id or not agent_id or not resource or not tool:
        raise ValueError("task_id, agent_id, resource and tool are required")
    _ensure_schema(root)
    safe_resource = _safe_resource(resource)
    mutation_id = f"dfm-{uuid.uuid4().hex[:12]}"
    with db.connect(root) as con:
        con.execute(
            "INSERT INTO direct_fs_authorized_mutations_v1(id,task_id,agent_id,resource,tool,patch_id,before_hash,after_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (mutation_id, task_id, agent_id, safe_resource, tool, patch_id or "", _normalize_hash_value(before_hash or ""), _normalize_hash_value(after_hash or ""), _now()),
        )
        db.add_event(con, "direct_fs_tripwire.authorized_mutation", {"task_id": task_id, "resource": safe_resource, "tool": tool, "patch_id": patch_id or ""}, agent_id)
    return {"verdict": "DIRECT_FS_AUTHORIZED_MUTATION_RECORDED", "id": mutation_id, "resource": safe_resource}


def _is_authorized_change(entry: dict[str, str], baseline_entry: dict[str, str] | None, authorized: list[dict[str, str]]) -> bool:
    path = entry["path"]
    for item in authorized:
        if item.get("resource") != path:
            continue
        after_hash = _normalize_hash_value(item.get("after_hash") or "")
        if after_hash and after_hash == entry.get("hash", ""):
            return True
        if item.get("tool") == "delete_resource" and entry.get("status") == " D":
            return True
        if baseline_entry is None and after_hash and after_hash == entry.get("hash", ""):
            return True
    return False


def detect_unauthorized_mutations(project_root: Path | None, task_id: str, agent_id: str, resource: str = "") -> dict[str, Any]:
    root = _project_root(project_root)
    _ensure_schema(root)
    with db.connect(root) as con:
        snapshot = _load_snapshot(con, task_id, agent_id)
        if not snapshot:
            return {"verdict": "DIRECT_FS_TRIPWIRE_NO_SNAPSHOT", "task_id": task_id, "agent_id": agent_id, "suspects": [], "git_status": _git_status(root)}
        baseline = json.loads(snapshot["baseline_status_json"])
        all_auth = _all_authorized(con)
    current = _git_status(root)
    baseline_map = _status_key(baseline)
    suspects: list[dict[str, str]] = []
    wanted_resource = _safe_resource(resource)
    for entry in current:
        path = entry["path"]
        if wanted_resource and path != wanted_resource:
            continue
        base = baseline_map.get(path)
        if base and base.get("status") == entry.get("status") and base.get("hash") == entry.get("hash"):
            continue
        if _is_authorized_change(entry, base, all_auth):
            continue
        suspects.append(entry)
    for entry in current:
        if entry["path"] == MEMOIRE_FILE and entry not in suspects:
            if wanted_resource and entry["path"] != wanted_resource:
                continue
            if not _changed_since_baseline(entry, baseline_map):
                continue
            auth_paths = {a.get("resource") for a in all_auth}
            if MEMOIRE_FILE not in auth_paths:
                suspects.append(entry)

    verdict = DIRECT_WRITE_BYPASS_DETECTED if suspects else TRIPWIRE_CLEAN
    with db.connect(root) as con:
        event = "direct_fs_tripwire.bypass_detected" if suspects else "direct_fs_tripwire.clean"
        db.add_event(con, event, {"task_id": task_id, "resource": wanted_resource, "suspects": suspects, "authorized": all_auth}, agent_id)
    return {"verdict": verdict, "task_id": task_id, "agent_id": agent_id, "resource": wanted_resource, "suspects": suspects, "git_status": current, "authorized_mutations": all_auth}


def assert_no_unauthorized_mutations(project_root: Path | None, task_id: str, agent_id: str, resource: str = "") -> dict[str, Any]:
    result = detect_unauthorized_mutations(project_root, task_id, agent_id, resource=resource)
    if result["verdict"] == DIRECT_WRITE_BYPASS_DETECTED:
        return result
    return result


def applied_patch_ids(project_root: Path | None, task_id: str, agent_id: str, resource: str = "") -> list[str]:
    root = _project_root(project_root)
    safe = _safe_resource(resource)
    _ensure_schema(root)
    with db.connect(root) as con:
        if safe:
            rows = con.execute(
                "SELECT patch_id FROM direct_fs_authorized_mutations_v1 WHERE task_id=? AND agent_id=? AND resource=? AND patch_id != ''",
                (task_id, agent_id, safe),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT patch_id FROM direct_fs_authorized_mutations_v1 WHERE task_id=? AND agent_id=? AND patch_id != ''",
                (task_id, agent_id),
            ).fetchall()
    return [row["patch_id"] for row in rows if row["patch_id"]]
