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
    ".next/",
    ".venv/",
    "build/",
    "coverage/",
    "dist/",
    "node_modules/",
    "target/",
    "vendor/",
    "venv/",
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
              authorization_cutoff_rowid INTEGER NOT NULL DEFAULT 0,
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
        columns = {
            str(row["name"])
            for row in con.execute("PRAGMA table_info(direct_fs_tripwire_snapshots_v1)").fetchall()
        }
        if "authorization_cutoff_rowid" not in columns:
            con.execute(
                "ALTER TABLE direct_fs_tripwire_snapshots_v1 "
                "ADD COLUMN authorization_cutoff_rowid INTEGER NOT NULL DEFAULT 0"
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


def _git_status(root: Path) -> list[dict[str, str]] | None:
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=str(root), text=False, capture_output=True, timeout=15,
    )
    if proc.returncode != 0:
        return None
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


def _filesystem_status(root: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = list(os.scandir(current))
        except OSError:
            continue
        for child in children:
            path = Path(child.path)
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if _ignored(relative):
                continue
            try:
                if child.is_dir(follow_symlinks=False):
                    stack.append(path)
                    continue
                if not child.is_file(follow_symlinks=False) and not child.is_symlink():
                    continue
            except OSError:
                continue
            entries.append({"path": relative, "status": "FS", "hash": _file_hash(root, relative)})
    entries.sort(key=lambda item: item["path"])
    return entries


def _workspace_status(root: Path) -> list[dict[str, str]]:
    git_status = _git_status(root)
    return git_status if git_status is not None else _filesystem_status(root)


def _load_snapshot(con: Any, task_id: str, agent_id: str) -> dict[str, Any] | None:
    row = con.execute(
        "SELECT * FROM direct_fs_tripwire_snapshots_v1 WHERE task_id=? AND agent_id=?",
        (task_id, agent_id),
    ).fetchone()
    return dict(row) if row else None


def _authorization_cutoff(con: Any) -> int:
    row = con.execute(
        "SELECT COALESCE(MAX(rowid),0) AS cutoff FROM direct_fs_authorized_mutations_v1"
    ).fetchone()
    return int(row["cutoff"] if row else 0)


def _receipt_is_committed(con: Any, item: dict[str, str]) -> bool:
    tool = str(item.get("tool") or "")
    patch_id = str(item.get("patch_id") or "")
    resource = str(item.get("resource") or "")
    agent_id = str(item.get("agent_id") or "")
    task_id = str(item.get("task_id") or "")
    if not patch_id or not resource or not agent_id:
        return False
    if tool in {"apply_patch", "delete_resource"}:
        row = con.execute(
            """
            SELECT agent_id,target_path,status,metadata_json
            FROM patches_v2
            WHERE patch_id=?
            """,
            (patch_id,),
        ).fetchone()
        if not row or row["agent_id"] != agent_id or row["target_path"] != resource:
            return False
        expected_status = "applied" if tool == "apply_patch" else "deleted"
        if row["status"] != expected_status:
            return False
        if tool == "delete_resource":
            return True
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            return False
        return _normalize_hash_value(str(metadata.get("applied_hash") or "")) == _normalize_hash_value(
            str(item.get("after_hash") or "")
        )
    if tool == "tenor_apply_changeset":
        row = con.execute(
            """
            SELECT tx.task_id,tx.agent_id,tx.status,files.resource,files.new_hash
            FROM tenor_changesets_v1 AS tx
            JOIN tenor_changeset_files_v1 AS files ON files.changeset_id=tx.changeset_id
            WHERE tx.changeset_id=? AND files.resource=?
            """,
            (patch_id, resource),
        ).fetchone()
        if not row:
            return False
        return (
            row["task_id"] == task_id
            and row["agent_id"] == agent_id
            and row["status"] == "committed"
            and _normalize_hash_value(str(row["new_hash"] or ""))
            == _normalize_hash_value(str(item.get("after_hash") or ""))
        )
    return False


def _verified_authorized_since(con: Any, cutoff_rowid: int) -> list[dict[str, str]]:
    rows = con.execute(
        """
        SELECT rowid,task_id,agent_id,resource,tool,patch_id,before_hash,after_hash
        FROM direct_fs_authorized_mutations_v1
        WHERE rowid>?
        ORDER BY rowid
        """,
        (max(0, int(cutoff_rowid)),),
    ).fetchall()
    verified: list[dict[str, str]] = []
    for row in rows:
        item = dict(row)
        try:
            if _receipt_is_committed(con, item):
                verified.append(item)
        except Exception:
            continue
    return verified


def _status_key(entries: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {entry["path"]: entry for entry in entries}


def _changed_since_baseline(entry: dict[str, str], baseline_map: dict[str, dict[str, str]]) -> bool:
    base = baseline_map.get(entry["path"])
    if not base:
        return True
    return base.get("status") != entry.get("status") or base.get("hash") != entry.get("hash")


def workspace_snapshot(
    project_root: Path | None,
    task_id: str,
    agent_id: str,
    resource: str = "",
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    root = _project_root(project_root)
    if not task_id or not agent_id:
        raise ValueError("task_id and agent_id are required")
    _ensure_schema(root)
    safe_resource = _safe_resource(resource)
    with db.connect(root) as con:
        authorization_cutoff = _authorization_cutoff(con)
    baseline = _workspace_status(root)
    with db.connect(root) as con:
        existing = _load_snapshot(con, task_id, agent_id)
        if existing:
            if refresh:
                con.execute(
                    "UPDATE direct_fs_tripwire_snapshots_v1 SET resource=?,baseline_status_json=?,authorization_cutoff_rowid=?,created_at=? WHERE task_id=? AND agent_id=?",
                    (
                        safe_resource,
                        json.dumps(baseline, ensure_ascii=False, sort_keys=True),
                        authorization_cutoff,
                        _now(),
                        task_id,
                        agent_id,
                    ),
                )
                db.add_event(
                    con,
                    "direct_fs_tripwire.snapshot_refreshed",
                    {"task_id": task_id, "resource": safe_resource, "count": len(baseline)},
                    agent_id,
                )
                return {
                    "verdict": "DIRECT_FS_TRIPWIRE_SNAPSHOT_REFRESHED",
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "baseline": baseline,
                }
            return {"verdict": "DIRECT_FS_TRIPWIRE_SNAPSHOT_EXISTS", "task_id": task_id, "agent_id": agent_id, "baseline": json.loads(existing["baseline_status_json"])}
        con.execute(
            "INSERT INTO direct_fs_tripwire_snapshots_v1(task_id,agent_id,resource,baseline_status_json,authorization_cutoff_rowid,created_at) VALUES(?,?,?,?,?,?)",
            (
                task_id,
                agent_id,
                safe_resource,
                json.dumps(baseline, ensure_ascii=False, sort_keys=True),
                authorization_cutoff,
                _now(),
            ),
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
        if after_hash == patch_queue.NEW_FILE_HASH and not entry.get("hash"):
            return True
        if item.get("tool") == "delete_resource" and not entry.get("hash"):
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
            current = _workspace_status(root)
            return {"verdict": "DIRECT_FS_TRIPWIRE_NO_SNAPSHOT", "task_id": task_id, "agent_id": agent_id, "suspects": [], "git_status": current}
        baseline = json.loads(snapshot["baseline_status_json"])
        verified_authorized = _verified_authorized_since(
            con,
            int(snapshot.get("authorization_cutoff_rowid") or 0),
        )
    current = _workspace_status(root)
    baseline_map = _status_key(baseline)
    current_map = _status_key(current)
    for path in sorted(set(baseline_map).difference(current_map)):
        current.append({"path": path, "status": "ABSENT", "hash": ""})
    current.sort(key=lambda item: item["path"])
    suspects: list[dict[str, str]] = []
    wanted_resource = _safe_resource(resource)
    for entry in current:
        path = entry["path"]
        if wanted_resource and path != wanted_resource:
            continue
        base = baseline_map.get(path)
        if base and base.get("status") == entry.get("status") and base.get("hash") == entry.get("hash"):
            continue
        if _is_authorized_change(entry, base, verified_authorized):
            continue
        suspects.append(entry)
    for entry in current:
        if entry["path"] == MEMOIRE_FILE and entry not in suspects:
            if wanted_resource and entry["path"] != wanted_resource:
                continue
            if not _changed_since_baseline(entry, baseline_map):
                continue
            auth_paths = {a.get("resource") for a in verified_authorized}
            if MEMOIRE_FILE not in auth_paths:
                suspects.append(entry)

    verdict = DIRECT_WRITE_BYPASS_DETECTED if suspects else TRIPWIRE_CLEAN
    with db.connect(root) as con:
        event = "direct_fs_tripwire.bypass_detected" if suspects else "direct_fs_tripwire.clean"
        db.add_event(con, event, {"task_id": task_id, "resource": wanted_resource, "suspects": suspects, "authorized": verified_authorized}, agent_id)
    return {"verdict": verdict, "task_id": task_id, "agent_id": agent_id, "resource": wanted_resource, "suspects": suspects, "git_status": current, "authorized_mutations": verified_authorized}


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
