from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from runtime import db, patch_queue


NEW_FILE_HASH = patch_queue.NEW_FILE_HASH
MAX_FILES = 64
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_CHANGESET_BYTES = 16 * 1024 * 1024
MAX_VALIDATORS = 12
MAX_VALIDATOR_OUTPUT_BYTES = 32 * 1024
MAX_VALIDATOR_TIMEOUT_SECONDS = 600
LOCK_TTL_SECONDS = 1800
STALE_TRANSACTION_SECONDS = LOCK_TTL_SECONDS + MAX_VALIDATOR_TIMEOUT_SECONDS
LOCK_TABLE = "resource_exclusive_locks"
TRANSACTION_TABLE = "tenor_changesets_v1"
FILE_TABLE = "tenor_changeset_files_v1"


class ChangesetError(RuntimeError):
    def __init__(self, verdict: str, details: dict[str, Any] | None = None):
        super().__init__(verdict)
        self.verdict = verdict
        self.details = details or {}


def _now() -> int:
    return int(time.time())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(root: Path, raw: Any) -> tuple[str, Path]:
    if not isinstance(raw, str) or not raw.strip():
        raise ChangesetError("TENOR_CHANGESET_INVALID_PATH", {"path": raw})
    value = raw.strip().replace("\\", "/")
    if value.startswith("/") or value.startswith("//"):
        raise ChangesetError("TENOR_CHANGESET_INVALID_PATH", {"path": value})
    if len(value) >= 3 and value[1] == ":" and value[2] == "/":
        raise ChangesetError("TENOR_CHANGESET_INVALID_PATH", {"path": value})
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ChangesetError("TENOR_CHANGESET_INVALID_PATH", {"path": value})
    normalized = path.as_posix()
    target = root / path
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ChangesetError("TENOR_CHANGESET_SYMLINK_FORBIDDEN", {"path": normalized})
        if not current.exists():
            break
    nearest = target.parent
    while not nearest.exists() and nearest != nearest.parent:
        nearest = nearest.parent
    try:
        nearest.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise ChangesetError("TENOR_CHANGESET_INVALID_PATH", {"path": normalized}) from exc
    return normalized, target


def _path_in_scope(path: str, allowed_resources: list[str]) -> bool:
    for allowed in allowed_resources:
        value = str(allowed or "").strip().replace("\\", "/").rstrip("/")
        if value in {".", "(whole repo)", "whole repo", "project"}:
            return True
        if not value:
            continue
        if path == value or path.startswith(value + "/"):
            return True
    return False


def _current_hash(path: Path) -> str:
    if path.is_symlink():
        raise ChangesetError("TENOR_CHANGESET_SYMLINK_FORBIDDEN", {"path": str(path)})
    if not path.exists():
        return NEW_FILE_HASH
    if not path.is_file():
        raise ChangesetError("TENOR_CHANGESET_NOT_A_FILE", {"path": str(path)})
    return _sha256_file(path)


def _canonical_changes(
    root: Path,
    changes: list[dict[str, Any]],
    allowed_resources: list[str],
    confirm_deletions: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(changes, list) or not changes or len(changes) > MAX_FILES:
        raise ChangesetError(
            "TENOR_CHANGESET_INVALID_FILE_COUNT",
            {"count": len(changes) if isinstance(changes, list) else -1, "maximum": MAX_FILES},
        )
    confirmed = {
        str(path).strip().replace("\\", "/")
        for path in (confirm_deletions or [])
        if str(path).strip()
    }
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    total_bytes = 0
    delete_paths: set[str] = set()
    for raw in changes:
        if not isinstance(raw, dict):
            raise ChangesetError("TENOR_CHANGESET_INVALID_CHANGE")
        resource, target = _safe_relative_path(root, raw.get("path"))
        if resource in seen:
            raise ChangesetError("TENOR_CHANGESET_DUPLICATE_RESOURCE", {"path": resource})
        seen.add(resource)
        if not _path_in_scope(resource, allowed_resources):
            raise ChangesetError("TENOR_CHANGESET_RESOURCE_OUT_OF_SCOPE", {"path": resource})
        operation = str(raw.get("operation") or "patch").strip().lower()
        if operation not in {"patch", "replace", "create", "delete"}:
            raise ChangesetError("TENOR_CHANGESET_INVALID_OPERATION", {"path": resource, "operation": operation})
        base_hash = str(raw.get("base_hash") or "").strip()
        if not base_hash:
            raise ChangesetError("TENOR_CHANGESET_BASE_HASH_REQUIRED", {"path": resource})
        current_hash = _current_hash(target)
        if current_hash != base_hash:
            raise ChangesetError(
                "TENOR_CHANGESET_BASE_STALE",
                {"path": resource, "expected_hash": base_hash, "current_hash": current_hash},
            )
        if operation == "create" and current_hash != NEW_FILE_HASH:
            raise ChangesetError("TENOR_CHANGESET_CREATE_TARGET_EXISTS", {"path": resource})
        if operation in {"patch", "replace", "delete"} and current_hash == NEW_FILE_HASH:
            raise ChangesetError("TENOR_CHANGESET_TARGET_MISSING", {"path": resource})
        if operation == "delete":
            delete_paths.add(resource)
            content_bytes = b""
        elif operation == "patch":
            diff_text = raw.get("diff_text")
            if not isinstance(diff_text, str) or not diff_text:
                raise ChangesetError("TENOR_CHANGESET_DIFF_REQUIRED", {"path": resource})
            if len(diff_text.encode("utf-8")) > patch_queue.MAX_DIFF_BYTES:
                raise ChangesetError("TENOR_CHANGESET_DIFF_TOO_LARGE", {"path": resource})
            try:
                original = target.read_text(encoding="utf-8")
                content_bytes = patch_queue.apply_unified_diff(original, diff_text).encode("utf-8")
            except (UnicodeDecodeError, patch_queue.PatchQueueError) as exc:
                raise ChangesetError("TENOR_CHANGESET_PATCH_INVALID", {"path": resource, "reason": str(exc)}) from exc
        else:
            content = raw.get("content")
            if not isinstance(content, str):
                raise ChangesetError("TENOR_CHANGESET_CONTENT_REQUIRED", {"path": resource})
            content_bytes = content.encode("utf-8")
        if len(content_bytes) > MAX_FILE_BYTES:
            raise ChangesetError("TENOR_CHANGESET_FILE_TOO_LARGE", {"path": resource, "maximum": MAX_FILE_BYTES})
        total_bytes += len(content_bytes)
        if total_bytes > MAX_CHANGESET_BYTES:
            raise ChangesetError("TENOR_CHANGESET_TOO_LARGE", {"maximum": MAX_CHANGESET_BYTES})
        result.append({
            "path": resource,
            "target": target,
            "operation": operation,
            "base_hash": base_hash,
            "content": content_bytes,
            "new_hash": NEW_FILE_HASH if operation == "delete" else _sha256_bytes(content_bytes),
        })
    if delete_paths != confirmed:
        raise ChangesetError(
            "TENOR_CHANGESET_DELETE_CONFIRMATION_REQUIRED",
            {"required": sorted(delete_paths), "provided": sorted(confirmed)},
        )
    return sorted(result, key=lambda item: item["path"])


def _canonical_validators(root: Path, validators: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(validators, list) or len(validators) > MAX_VALIDATORS:
        raise ChangesetError("TENOR_CHANGESET_INVALID_VALIDATORS", {"maximum": MAX_VALIDATORS})
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(validators):
        if not isinstance(raw, dict):
            raise ChangesetError("TENOR_CHANGESET_INVALID_VALIDATOR", {"index": index})
        argv = raw.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or len(argv) > 64
            or any(not isinstance(arg, str) or "\x00" in arg for arg in argv)
        ):
            raise ChangesetError("TENOR_CHANGESET_INVALID_VALIDATOR_ARGV", {"index": index})
        cwd_raw = str(raw.get("cwd") or ".")
        if cwd_raw == ".":
            cwd = root
        else:
            _, cwd = _safe_relative_path(root, cwd_raw)
        if not cwd.is_dir():
            raise ChangesetError("TENOR_CHANGESET_VALIDATOR_CWD_INVALID", {"index": index, "cwd": cwd_raw})
        try:
            timeout = int(raw.get("timeout_seconds") or 120)
        except (TypeError, ValueError) as exc:
            raise ChangesetError("TENOR_CHANGESET_VALIDATOR_TIMEOUT_INVALID", {"index": index}) from exc
        if timeout < 1 or timeout > MAX_VALIDATOR_TIMEOUT_SECONDS:
            raise ChangesetError(
                "TENOR_CHANGESET_VALIDATOR_TIMEOUT_INVALID",
                {"index": index, "maximum": MAX_VALIDATOR_TIMEOUT_SECONDS},
            )
        result.append({"argv": list(argv), "cwd": cwd, "cwd_display": cwd_raw, "timeout_seconds": timeout})
    return result


def ensure_schema(project_root: Path) -> None:
    db.init_db(project_root)
    with db.connect(project_root) as con:
        con.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {TRANSACTION_TABLE}(
              changeset_id TEXT PRIMARY KEY,
              request_id TEXT NOT NULL,
              request_fingerprint TEXT NOT NULL,
              task_id TEXT NOT NULL,
              agent_id TEXT NOT NULL,
              owner_pid INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              error_json TEXT NOT NULL DEFAULT '{{}}',
              result_json TEXT NOT NULL DEFAULT '{{}}'
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_{TRANSACTION_TABLE}_agent_request
              ON {TRANSACTION_TABLE}(agent_id,request_id);
            CREATE TABLE IF NOT EXISTS {FILE_TABLE}(
              changeset_id TEXT NOT NULL,
              ordinal INTEGER NOT NULL,
              resource TEXT NOT NULL,
              operation TEXT NOT NULL,
              base_hash TEXT NOT NULL,
              new_hash TEXT NOT NULL,
              backup_path TEXT NOT NULL,
              staged_path TEXT NOT NULL,
              applied INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(changeset_id,resource)
            );
            CREATE INDEX IF NOT EXISTS idx_{FILE_TABLE}_transaction
              ON {FILE_TABLE}(changeset_id,ordinal);
            CREATE TABLE IF NOT EXISTS {LOCK_TABLE}(
              lock_id TEXT PRIMARY KEY,
              resource TEXT NOT NULL UNIQUE,
              agent_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              mode TEXT NOT NULL DEFAULT 'exclusive',
              created_at REAL NOT NULL,
              expires_at REAL NOT NULL,
              heartbeat_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_{LOCK_TABLE}_resource
              ON {LOCK_TABLE}(resource,expires_at);
            """
        )
        columns = {
            str(row["name"])
            for row in con.execute(f"PRAGMA table_info({TRANSACTION_TABLE})").fetchall()
        }
        if "owner_pid" not in columns:
            con.execute(
                f"ALTER TABLE {TRANSACTION_TABLE} ADD COLUMN owner_pid INTEGER NOT NULL DEFAULT 0"
            )


def _transaction_root(project_root: Path, changeset_id: str) -> Path:
    return project_root / ".agent" / "state" / "runtime" / "tenor-changesets" / changeset_id


def _write_durable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)


def _fsync_parent(path: Path) -> None:
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        descriptor = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_file(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tenor-tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
        _fsync_parent(target.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)


def _acquire_locks(
    project_root: Path,
    changeset_id: str,
    agent_id: str,
    task_id: str,
    resources: list[str],
) -> None:
    now = time.time()
    with db.connect(project_root) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(f"DELETE FROM {LOCK_TABLE} WHERE expires_at<=?", (now,))
            for resource in sorted(resources):
                exclusive = con.execute(
                    f"SELECT lock_id,agent_id,task_id,expires_at FROM {LOCK_TABLE} WHERE resource=?",
                    (resource,),
                ).fetchone()
                if exclusive:
                    raise ChangesetError(
                        "TENOR_CHANGESET_RESOURCE_BUSY",
                        {
                            "path": resource,
                            "owner_agent_id": exclusive["agent_id"],
                            "owner_task_id": exclusive["task_id"],
                            "owner_lock_id": exclusive["lock_id"],
                        },
                    )
                for table, query in (
                    (
                        "claims",
                        "SELECT agent_id FROM claims WHERE resource=? AND agent_id<>? AND status='active' AND expires_at>? LIMIT 1",
                    ),
                    (
                        "resource_locks",
                        "SELECT agent_id FROM resource_locks WHERE resource=? AND agent_id<>? AND status='active' AND expires_at>? LIMIT 1",
                    ),
                ):
                    try:
                        blocker = con.execute(query, (resource, agent_id, int(now))).fetchone()
                    except Exception as exc:
                        if "no such table" in str(exc).lower():
                            blocker = None
                        else:
                            raise
                    if blocker:
                        raise ChangesetError(
                            "TENOR_CHANGESET_RESOURCE_BUSY",
                            {"path": resource, "owner_agent_id": blocker["agent_id"], "lock_source": table},
                        )
            for resource in sorted(resources):
                con.execute(
                    f"INSERT INTO {LOCK_TABLE}(lock_id,resource,agent_id,task_id,mode,created_at,expires_at,heartbeat_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        f"changeset-{changeset_id}-{uuid.uuid4().hex[:8]}",
                        resource,
                        agent_id,
                        task_id,
                        "exclusive",
                        now,
                        now + LOCK_TTL_SECONDS,
                        now,
                    ),
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise


def _release_locks(project_root: Path, changeset_id: str) -> None:
    with db.connect(project_root) as con:
        con.execute(
            f"DELETE FROM {LOCK_TABLE} WHERE lock_id LIKE ?",
            (f"changeset-{changeset_id}-%",),
        )


def _update_transaction(
    project_root: Path,
    changeset_id: str,
    status: str,
    *,
    error: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    with db.connect(project_root) as con:
        con.execute(
            f"UPDATE {TRANSACTION_TABLE} SET status=?,updated_at=?,error_json=?,result_json=? WHERE changeset_id=?",
            (status, _now(), _json(error or {}), _json(result or {}), changeset_id),
        )


def _rollback(project_root: Path, changeset_id: str) -> list[str]:
    restored: list[str] = []
    with db.connect(project_root) as con:
        rows = con.execute(
            f"SELECT * FROM {FILE_TABLE} WHERE changeset_id=? ORDER BY ordinal DESC",
            (changeset_id,),
        ).fetchall()
    for row in rows:
        target = project_root / row["resource"]
        backup = Path(row["backup_path"])
        if row["base_hash"] == NEW_FILE_HASH:
            if target.exists() and not target.is_symlink():
                target.unlink()
                _fsync_parent(target.parent)
        elif backup.is_file():
            _replace_file(target, backup.read_bytes())
        restored.append(row["resource"])
    return sorted(restored)


def recover_incomplete(project_root: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    ensure_schema(project_root)
    recovered: list[str] = []
    with db.connect(project_root) as con:
        rows = con.execute(
            f"SELECT changeset_id,owner_pid,updated_at FROM {TRANSACTION_TABLE} WHERE status IN ('staging','applying','validating','rollback_required') ORDER BY created_at",
        ).fetchall()
    for row in rows:
        age_seconds = max(0, _now() - int(row["updated_at"] or 0))
        if (
            int(row["owner_pid"] or 0) > 0
            and db.process_is_alive(row["owner_pid"])
            and age_seconds <= STALE_TRANSACTION_SECONDS
        ):
            continue
        changeset_id = row["changeset_id"]
        restored = _rollback(project_root, changeset_id)
        _update_transaction(
            project_root,
            changeset_id,
            "rolled_back_recovered",
            error={"verdict": "TENOR_CHANGESET_RECOVERED_AFTER_INTERRUPTION", "restored": restored},
        )
        _release_locks(project_root, changeset_id)
        recovered.append(changeset_id)
    return {"ok": True, "verdict": "TENOR_CHANGESET_RECOVERY_COMPLETE", "recovered": recovered}


def _run_validators(validators: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for validator in validators:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                validator["argv"],
                cwd=str(validator["cwd"]),
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=validator["timeout_seconds"],
                check=False,
            )
            returncode = completed.returncode
            stdout = completed.stdout[-MAX_VALIDATOR_OUTPUT_BYTES:]
            stderr = completed.stderr[-MAX_VALIDATOR_OUTPUT_BYTES:]
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            returncode = 124
            stdout = str(exc.stdout or "")[-MAX_VALIDATOR_OUTPUT_BYTES:]
            stderr = str(exc.stderr or "")[-MAX_VALIDATOR_OUTPUT_BYTES:]
            timed_out = True
        except (FileNotFoundError, OSError) as exc:
            returncode = 127
            stdout = ""
            stderr = f"{type(exc).__name__}: {exc}"
            timed_out = False
        results.append({
            "argv": validator["argv"],
            "cwd": validator["cwd_display"],
            "timeout_seconds": validator["timeout_seconds"],
            "returncode": returncode,
            "ok": returncode == 0,
            "timed_out": timed_out,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "stdout": stdout,
            "stderr": stderr,
        })
        if returncode != 0:
            break
    return results


def _request_fingerprint(
    task_id: str,
    changes: list[dict[str, Any]],
    validators: list[dict[str, Any]],
    confirm_deletions: list[str],
) -> str:
    payload = {
        "task_id": task_id,
        "changes": changes,
        "validators": validators,
        "confirm_deletions": sorted(confirm_deletions or []),
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def apply_changeset(
    *,
    project_root: Path,
    agent_id: str,
    task_id: str,
    changes: list[dict[str, Any]],
    validators: list[dict[str, Any]] | None = None,
    allowed_resources: list[str] | None = None,
    confirm_deletions: list[str] | None = None,
    request_id: str = "",
    _test_fail_after_replaces: int | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    validators = validators or []
    allowed_resources = allowed_resources or []
    confirm_deletions = confirm_deletions or []
    if not agent_id or not task_id:
        return {"ok": False, "verdict": "TENOR_CHANGESET_TASK_IDENTITY_REQUIRED"}
    request_id = (request_id or uuid.uuid4().hex).strip()
    if len(request_id) > 200 or not request_id:
        return {"ok": False, "verdict": "TENOR_CHANGESET_REQUEST_ID_INVALID"}
    ensure_schema(root)
    recover_incomplete(root)
    fingerprint = _request_fingerprint(task_id, changes, validators, confirm_deletions)
    with db.connect(root) as con:
        existing = con.execute(
            f"SELECT * FROM {TRANSACTION_TABLE} WHERE agent_id=? AND request_id=?",
            (agent_id, request_id),
        ).fetchone()
    if existing:
        if existing["request_fingerprint"] != fingerprint:
            return {
                "ok": False,
                "verdict": "TENOR_CHANGESET_IDEMPOTENCY_CONFLICT",
                "changeset_id": existing["changeset_id"],
            }
        if existing["status"] == "committed":
            previous = json.loads(existing["result_json"] or "{}")
            previous.update({"ok": True, "verdict": "TENOR_CHANGESET_ALREADY_COMMITTED"})
            return previous
        return {
            "ok": False,
            "verdict": "TENOR_CHANGESET_REQUEST_ALREADY_FINALIZED",
            "changeset_id": existing["changeset_id"],
            "status": existing["status"],
            "error": json.loads(existing["error_json"] or "{}"),
        }
    try:
        canonical = _canonical_changes(root, changes, allowed_resources, confirm_deletions)
        canonical_validators = _canonical_validators(root, validators)
    except ChangesetError as exc:
        return {"ok": False, "verdict": exc.verdict, **exc.details}

    changeset_id = f"cs-{uuid.uuid4().hex[:20]}"
    transaction_dir = _transaction_root(root, changeset_id)
    created = _now()
    with db.connect(root) as con:
        inserted = con.execute(
            f"INSERT OR IGNORE INTO {TRANSACTION_TABLE}(changeset_id,request_id,request_fingerprint,task_id,agent_id,owner_pid,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (changeset_id, request_id, fingerprint, task_id, agent_id, os.getpid(), "staging", created, created),
        ).rowcount
        if not inserted:
            raced = con.execute(
                f"SELECT * FROM {TRANSACTION_TABLE} WHERE agent_id=? AND request_id=?",
                (agent_id, request_id),
            ).fetchone()
            if not raced:
                return {"ok": False, "verdict": "TENOR_CHANGESET_IDEMPOTENCY_RACE"}
            if raced["request_fingerprint"] != fingerprint:
                return {
                    "ok": False,
                    "verdict": "TENOR_CHANGESET_IDEMPOTENCY_CONFLICT",
                    "changeset_id": raced["changeset_id"],
                }
            if raced["status"] == "committed":
                previous = json.loads(raced["result_json"] or "{}")
                previous.update({"ok": True, "verdict": "TENOR_CHANGESET_ALREADY_COMMITTED"})
                return previous
            return {
                "ok": False,
                "verdict": "TENOR_CHANGESET_REQUEST_IN_PROGRESS",
                "changeset_id": raced["changeset_id"],
                "status": raced["status"],
            }
    try:
        _acquire_locks(root, changeset_id, agent_id, task_id, [item["path"] for item in canonical])
        for ordinal, item in enumerate(canonical):
            if _current_hash(item["target"]) != item["base_hash"]:
                raise ChangesetError("TENOR_CHANGESET_BASE_STALE", {"path": item["path"]})
            backup = transaction_dir / "backup" / f"{ordinal:04d}.bin"
            staged = transaction_dir / "staged" / f"{ordinal:04d}.bin"
            if item["base_hash"] != NEW_FILE_HASH:
                _write_durable(backup, item["target"].read_bytes())
            if item["operation"] != "delete":
                _write_durable(staged, item["content"])
            with db.connect(root) as con:
                con.execute(
                    f"INSERT INTO {FILE_TABLE}(changeset_id,ordinal,resource,operation,base_hash,new_hash,backup_path,staged_path,applied) VALUES(?,?,?,?,?,?,?,?,0)",
                    (
                        changeset_id,
                        ordinal,
                        item["path"],
                        item["operation"],
                        item["base_hash"],
                        item["new_hash"],
                        str(backup),
                        str(staged),
                    ),
                )
        _update_transaction(root, changeset_id, "applying")
        replaced = 0
        for item in canonical:
            if _current_hash(item["target"]) != item["base_hash"]:
                raise ChangesetError("TENOR_CHANGESET_BASE_STALE", {"path": item["path"]})
            if item["operation"] == "delete":
                item["target"].unlink()
                _fsync_parent(item["target"].parent)
            else:
                _replace_file(item["target"], item["content"])
            replaced += 1
            with db.connect(root) as con:
                con.execute(
                    f"UPDATE {FILE_TABLE} SET applied=1 WHERE changeset_id=? AND resource=?",
                    (changeset_id, item["path"]),
                )
            if _test_fail_after_replaces is not None and replaced >= _test_fail_after_replaces:
                raise ChangesetError("TENOR_CHANGESET_TEST_INJECTED_FAILURE")

        _update_transaction(root, changeset_id, "validating")
        validation_results = _run_validators(canonical_validators)
        if any(not result["ok"] for result in validation_results):
            restored = _rollback(root, changeset_id)
            error = {
                "verdict": "TENOR_CHANGESET_VALIDATION_FAILED_ROLLED_BACK",
                "restored": restored,
                "validators": validation_results,
            }
            _update_transaction(root, changeset_id, "rolled_back", error=error)
            return {
                "ok": False,
                "verdict": error["verdict"],
                "changeset_id": changeset_id,
                "restored": restored,
                "validators": validation_results,
            }
        files = [
            {
                "path": item["path"],
                "operation": item["operation"],
                "base_hash": item["base_hash"],
                "new_hash": item["new_hash"],
            }
            for item in canonical
        ]
        result = {
            "ok": True,
            "verdict": "TENOR_CHANGESET_COMMITTED",
            "changeset_id": changeset_id,
            "request_id": request_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "files": files,
            "validators": validation_results,
            "committed_at": _now(),
        }
        _update_transaction(root, changeset_id, "committed", result=result)
        return result
    except ChangesetError as exc:
        restored = _rollback(root, changeset_id)
        error = {"verdict": exc.verdict, "details": exc.details, "restored": restored}
        _update_transaction(root, changeset_id, "rolled_back", error=error)
        verdict = (
            "TENOR_CHANGESET_BASE_STALE"
            if exc.verdict == "TENOR_CHANGESET_BASE_STALE"
            else "TENOR_CHANGESET_APPLY_FAILED_ROLLED_BACK"
        )
        return {
            "ok": False,
            "verdict": verdict,
            "changeset_id": changeset_id,
            "cause": exc.verdict,
            "restored": restored,
            **exc.details,
        }
    except Exception as exc:
        restored = _rollback(root, changeset_id)
        error = {
            "verdict": "TENOR_CHANGESET_APPLY_FAILED_ROLLED_BACK",
            "reason": f"{type(exc).__name__}: {exc}",
            "restored": restored,
        }
        _update_transaction(root, changeset_id, "rolled_back", error=error)
        return {"ok": False, "changeset_id": changeset_id, **error}
    finally:
        _release_locks(root, changeset_id)
        if transaction_dir.exists():
            shutil.rmtree(transaction_dir, ignore_errors=True)
