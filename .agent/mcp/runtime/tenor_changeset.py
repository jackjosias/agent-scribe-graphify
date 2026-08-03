from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from runtime import bounded_process, db, patch_queue


NEW_FILE_HASH = patch_queue.NEW_FILE_HASH
MAX_FILES = 64
MAX_EDITS_PER_FILE = 128
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
ROLLBACK_LOCK_TABLE = "tenor_changeset_rollback_locks_v1"
RECOVERABLE_TRANSACTION_STATUSES = frozenset(
    {"staging", "applying", "validating", "guarding", "rollback_required"}
)
PrecommitGuard = Callable[[dict[str, Any]], dict[str, Any]]


class ChangesetError(RuntimeError):
    def __init__(self, verdict: str, details: dict[str, Any] | None = None):
        super().__init__(verdict)
        self.verdict = verdict
        self.details = details or {}


@dataclass(frozen=True)
class ExecutionFence:
    job_id: str
    worker_instance_id: str
    fence_token: int


@dataclass(frozen=True)
class RollbackResult:
    ok: bool
    restored: tuple[str, ...] = ()
    conflicts: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "restored": list(self.restored),
            "conflicts": [dict(item) for item in self.conflicts],
        }


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


def _line_count(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _replacement_risk(original: bytes, replacement: bytes) -> dict[str, Any] | None:
    before_lines = _line_count(original)
    after_lines = _line_count(replacement)
    before_bytes = len(original)
    after_bytes = len(replacement)
    line_collapse = before_lines >= 100 and after_lines < max(10, before_lines // 2)
    byte_collapse = before_bytes >= 4096 and after_bytes < before_bytes // 2
    if not (line_collapse or byte_collapse):
        return None
    return {
        "before_lines": before_lines,
        "after_lines": after_lines,
        "before_bytes": before_bytes,
        "after_bytes": after_bytes,
    }


def _apply_structured_edits(resource: str, original: str, edits: Any) -> str:
    if not isinstance(edits, list) or not edits or len(edits) > MAX_EDITS_PER_FILE:
        raise ChangesetError(
            "TENOR_CHANGESET_INVALID_EDITS",
            {"path": resource, "count": len(edits) if isinstance(edits, list) else -1, "maximum": MAX_EDITS_PER_FILE},
        )
    content = original
    for index, raw in enumerate(edits):
        if not isinstance(raw, dict):
            raise ChangesetError("TENOR_CHANGESET_INVALID_EDIT", {"path": resource, "edit_index": index})
        old_text = raw.get("old_text")
        new_text = raw.get("new_text")
        if not isinstance(old_text, str) or not old_text:
            raise ChangesetError(
                "TENOR_CHANGESET_EDIT_ANCHOR_REQUIRED",
                {"path": resource, "edit_index": index},
            )
        if not isinstance(new_text, str):
            raise ChangesetError(
                "TENOR_CHANGESET_EDIT_REPLACEMENT_REQUIRED",
                {"path": resource, "edit_index": index},
            )
        try:
            expected = int(raw.get("expected_occurrences", 1))
        except (TypeError, ValueError) as exc:
            raise ChangesetError(
                "TENOR_CHANGESET_EDIT_OCCURRENCES_INVALID",
                {"path": resource, "edit_index": index},
            ) from exc
        if expected < 1 or expected > 1024:
            raise ChangesetError(
                "TENOR_CHANGESET_EDIT_OCCURRENCES_INVALID",
                {"path": resource, "edit_index": index, "expected_occurrences": expected},
            )
        actual = content.count(old_text)
        if actual != expected:
            raise ChangesetError(
                "TENOR_CHANGESET_EDIT_ANCHOR_MISMATCH",
                {
                    "path": resource,
                    "edit_index": index,
                    "expected_occurrences": expected,
                    "actual_occurrences": actual,
                },
            )
        content = content.replace(old_text, new_text, expected)
    if content == original:
        raise ChangesetError("TENOR_CHANGESET_NO_OP", {"path": resource})
    return content


def _full_replace_confirmations(raw_confirmations: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    if not isinstance(raw_confirmations, list):
        raise ChangesetError("TENOR_CHANGESET_REPLACE_CONFIRMATION_INVALID")
    result: dict[str, tuple[str, str]] = {}
    for index, raw in enumerate(raw_confirmations):
        if not isinstance(raw, dict):
            raise ChangesetError("TENOR_CHANGESET_REPLACE_CONFIRMATION_INVALID", {"index": index})
        path = str(raw.get("path") or "").strip().replace("\\", "/")
        base_hash = str(raw.get("base_hash") or "").strip()
        new_hash = str(raw.get("new_hash") or "").strip()
        if not path or len(base_hash) != 64 or len(new_hash) != 64 or path in result:
            raise ChangesetError("TENOR_CHANGESET_REPLACE_CONFIRMATION_INVALID", {"index": index, "path": path})
        result[path] = (base_hash, new_hash)
    return result


def _canonical_changes(
    root: Path,
    changes: list[dict[str, Any]],
    allowed_resources: list[str],
    confirm_deletions: list[str],
    confirm_full_replacements: list[dict[str, Any]],
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
    replace_confirmations = _full_replace_confirmations(confirm_full_replacements)
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
        if operation not in {"patch", "edit", "replace", "create", "delete"}:
            raise ChangesetError("TENOR_CHANGESET_INVALID_OPERATION", {"path": resource, "operation": operation})
        base_hash = str(raw.get("base_hash") or "").strip()
        if operation == "create" and not base_hash:
            base_hash = NEW_FILE_HASH
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
        if operation in {"patch", "edit", "replace", "delete"} and current_hash == NEW_FILE_HASH:
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
        elif operation == "edit":
            try:
                original = target.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ChangesetError("TENOR_CHANGESET_EDIT_REQUIRES_UTF8", {"path": resource}) from exc
            content_bytes = _apply_structured_edits(resource, original, raw.get("edits")).encode("utf-8")
        else:
            content = raw.get("content")
            if not isinstance(content, str):
                raise ChangesetError("TENOR_CHANGESET_CONTENT_REQUIRED", {"path": resource})
            content_bytes = content.encode("utf-8")
            if operation == "replace":
                original_bytes = target.read_bytes()
                if content_bytes == original_bytes:
                    raise ChangesetError("TENOR_CHANGESET_NO_OP", {"path": resource})
                risk = _replacement_risk(original_bytes, content_bytes)
                if risk is not None:
                    confirmation = replace_confirmations.get(resource)
                    expected = (base_hash, _sha256_bytes(content_bytes))
                    if confirmation != expected:
                        raise ChangesetError(
                            "TENOR_CHANGESET_DESTRUCTIVE_REPLACE_REJECTED",
                            {
                                "path": resource,
                                **risk,
                                "required_confirmation": {
                                    "path": resource,
                                    "base_hash": base_hash,
                                    "new_hash": expected[1],
                                },
                            },
                        )
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
    if not validators:
        raise ChangesetError("TENOR_CHANGESET_VALIDATORS_REQUIRED")
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
              execution_job_id TEXT NOT NULL DEFAULT '',
              worker_instance_id TEXT NOT NULL DEFAULT '',
              fence_token INTEGER NOT NULL DEFAULT 0,
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
              changeset_id TEXT NOT NULL DEFAULT '',
              execution_job_id TEXT NOT NULL DEFAULT '',
              worker_instance_id TEXT NOT NULL DEFAULT '',
              fence_token INTEGER NOT NULL DEFAULT 0,
              mode TEXT NOT NULL DEFAULT 'exclusive',
              created_at REAL NOT NULL,
              expires_at REAL NOT NULL,
              heartbeat_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_{LOCK_TABLE}_resource
              ON {LOCK_TABLE}(resource,expires_at);
            CREATE TABLE IF NOT EXISTS {ROLLBACK_LOCK_TABLE}(
              changeset_id TEXT PRIMARY KEY,
              execution_job_id TEXT NOT NULL DEFAULT '',
              worker_instance_id TEXT NOT NULL,
              fence_token INTEGER NOT NULL,
              acquired_at INTEGER NOT NULL
            );
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
        transaction_migrations = {
            "execution_job_id": "TEXT NOT NULL DEFAULT ''",
            "worker_instance_id": "TEXT NOT NULL DEFAULT ''",
            "fence_token": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, declaration in transaction_migrations.items():
            if name not in columns:
                con.execute(
                    f"ALTER TABLE {TRANSACTION_TABLE} ADD COLUMN {name} {declaration}"
                )
        lock_columns = {
            str(row["name"])
            for row in con.execute(f"PRAGMA table_info({LOCK_TABLE})").fetchall()
        }
        lock_migrations = {
            "changeset_id": "TEXT NOT NULL DEFAULT ''",
            "execution_job_id": "TEXT NOT NULL DEFAULT ''",
            "worker_instance_id": "TEXT NOT NULL DEFAULT ''",
            "fence_token": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, declaration in lock_migrations.items():
            if name not in lock_columns:
                con.execute(
                    f"ALTER TABLE {LOCK_TABLE} ADD COLUMN {name} {declaration}"
                )
        rollback_columns = {
            str(row["name"])
            for row in con.execute(
                f"PRAGMA table_info({ROLLBACK_LOCK_TABLE})"
            ).fetchall()
        }
        if "execution_job_id" not in rollback_columns:
            con.execute(
                f"""
                ALTER TABLE {ROLLBACK_LOCK_TABLE}
                ADD COLUMN execution_job_id TEXT NOT NULL DEFAULT ''
                """
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
    target_mode = (
        stat.S_IMODE(target.stat().st_mode)
        if target.exists() and not target.is_symlink()
        else 0o644
    )
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tenor-tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), target_mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
        _fsync_parent(target.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)


def _assert_execution_fence(
    project_root: Path,
    execution_fence: ExecutionFence | None,
) -> None:
    if execution_fence is None:
        return
    from runtime import tenor_jobs

    proof = tenor_jobs.assert_worker_fence(
        project_root,
        job_id=execution_fence.job_id,
        worker_instance_id=execution_fence.worker_instance_id,
        fence_token=execution_fence.fence_token,
        allowed_statuses=frozenset({"running", "recovering"}),
    )
    if not proof.get("ok"):
        raise ChangesetError(
            "TENOR_CHANGESET_EXECUTION_FENCE_LOST",
            {"job_id": execution_fence.job_id},
        )


def _acquire_locks(
    project_root: Path,
    changeset_id: str,
    agent_id: str,
    task_id: str,
    resources: list[str],
    execution_fence: ExecutionFence | None = None,
) -> None:
    _assert_execution_fence(project_root, execution_fence)
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
                    f"""
                    INSERT INTO {LOCK_TABLE}(
                      lock_id,resource,agent_id,task_id,changeset_id,
                      execution_job_id,worker_instance_id,fence_token,
                      mode,created_at,expires_at,heartbeat_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"changeset-{changeset_id}-{uuid.uuid4().hex[:8]}",
                        resource,
                        agent_id,
                        task_id,
                        changeset_id,
                        execution_fence.job_id if execution_fence else "",
                        execution_fence.worker_instance_id if execution_fence else "",
                        execution_fence.fence_token if execution_fence else 0,
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
    _assert_execution_fence(project_root, execution_fence)


def _release_locks(
    project_root: Path,
    changeset_id: str,
    execution_fence: ExecutionFence | None = None,
) -> None:
    if execution_fence is not None:
        try:
            _assert_execution_fence(project_root, execution_fence)
        except ChangesetError:
            return
    with db.connect(project_root) as con:
        if execution_fence is None:
            con.execute(
                f"""
                DELETE FROM {LOCK_TABLE}
                WHERE execution_job_id=''
                  AND (
                    changeset_id=?
                    OR (
                      changeset_id=''
                      AND lock_id LIKE ?
                    )
                  )
                """,
                (changeset_id, f"changeset-{changeset_id}-%"),
            )
            return
        transaction = con.execute(
            f"""
            SELECT 1 FROM {TRANSACTION_TABLE}
            WHERE changeset_id=? AND execution_job_id=?
              AND worker_instance_id=? AND fence_token=?
            """,
            (
                changeset_id,
                execution_fence.job_id,
                execution_fence.worker_instance_id,
                execution_fence.fence_token,
            ),
        ).fetchone()
        if transaction:
            con.execute(
                f"DELETE FROM {LOCK_TABLE} WHERE changeset_id=?",
                (changeset_id,),
            )


def heartbeat_execution_locks(
    project_root: Path,
    execution_fence: ExecutionFence,
) -> bool:
    _assert_execution_fence(project_root, execution_fence)
    now = time.time()
    with db.connect(project_root) as con:
        con.execute(
            f"""
            UPDATE {LOCK_TABLE}
            SET heartbeat_at=?,expires_at=?
            WHERE execution_job_id=? AND worker_instance_id=? AND fence_token=?
            """,
            (
                now,
                now + LOCK_TTL_SECONDS,
                execution_fence.job_id,
                execution_fence.worker_instance_id,
                execution_fence.fence_token,
            ),
        )
        transaction = con.execute(
            f"""
            SELECT 1 FROM {TRANSACTION_TABLE}
            WHERE execution_job_id=? AND worker_instance_id=? AND fence_token=?
              AND status IN ('staging','applying','validating','guarding','rollback_required')
            LIMIT 1
            """,
            (
                execution_fence.job_id,
                execution_fence.worker_instance_id,
                execution_fence.fence_token,
            ),
        ).fetchone()
    return bool(transaction)


def _update_transaction(
    project_root: Path,
    changeset_id: str,
    status: str,
    *,
    error: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    execution_fence: ExecutionFence | None = None,
    expected_statuses: frozenset[str] | None = None,
) -> None:
    clauses = ["changeset_id=?"]
    params: list[Any] = [
        status,
        _now(),
        _json(error or {}),
        _json(result or {}),
        changeset_id,
    ]
    if execution_fence is None:
        clauses.append("execution_job_id=''")
    else:
        clauses.extend(
            [
                "execution_job_id=?",
                "worker_instance_id=?",
                "fence_token=?",
            ]
        )
        params.extend(
            [
                execution_fence.job_id,
                execution_fence.worker_instance_id,
                execution_fence.fence_token,
            ]
        )
    if expected_statuses:
        placeholders = ",".join("?" for _ in expected_statuses)
        clauses.append(f"status IN ({placeholders})")
        params.extend(sorted(expected_statuses))
    with db.connect(project_root) as con:
        updated = con.execute(
            f"""
            UPDATE {TRANSACTION_TABLE}
            SET status=?,updated_at=?,error_json=?,result_json=?
            WHERE {' AND '.join(clauses)}
            """,
            tuple(params),
        ).rowcount
    if updated:
        return
    _assert_execution_fence(project_root, execution_fence)
    raise ChangesetError(
        "TENOR_CHANGESET_STATE_TRANSITION_REJECTED",
        {"changeset_id": changeset_id, "target_status": status},
    )


def _rollback(
    project_root: Path,
    changeset_id: str,
    execution_fence: ExecutionFence | None = None,
) -> RollbackResult:
    _assert_execution_fence(project_root, execution_fence)
    owner = (
        execution_fence.worker_instance_id
        if execution_fence is not None
        else f"legacy-{os.getpid()}-{uuid.uuid4().hex}"
    )
    execution_job_id = execution_fence.job_id if execution_fence is not None else ""
    token = execution_fence.fence_token if execution_fence is not None else 0
    with db.connect(project_root) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            existing = con.execute(
                f"SELECT * FROM {ROLLBACK_LOCK_TABLE} WHERE changeset_id=?",
                (changeset_id,),
            ).fetchone()
            if existing:
                now = _now()
                can_transfer = bool(
                    execution_fence is not None
                    and str(existing["execution_job_id"] or "") == execution_job_id
                    and int(existing["fence_token"] or 0) < token
                )
                stale_legacy_lock = bool(
                    str(existing["execution_job_id"] or "") == ""
                    and int(existing["acquired_at"] or 0)
                    <= now - STALE_TRANSACTION_SECONDS
                )
                same_owner = bool(
                    str(existing["execution_job_id"] or "") == execution_job_id
                    and str(existing["worker_instance_id"] or "") == owner
                    and int(existing["fence_token"] or 0) == token
                )
                if can_transfer or stale_legacy_lock:
                    con.execute(
                        f"""
                        UPDATE {ROLLBACK_LOCK_TABLE}
                        SET execution_job_id=?,worker_instance_id=?,
                            fence_token=?,acquired_at=?
                        WHERE changeset_id=?
                          AND (
                            (execution_job_id=? AND fence_token<?)
                            OR (
                              execution_job_id=''
                              AND acquired_at<=?
                            )
                          )
                        """,
                        (
                            execution_job_id,
                            owner,
                            token,
                            now,
                            changeset_id,
                            execution_job_id,
                            token,
                            now - STALE_TRANSACTION_SECONDS,
                        ),
                    )
                elif not same_owner:
                    con.execute("ROLLBACK")
                    return RollbackResult(
                        False,
                        conflicts=({
                            "path": "<rollback-lock>",
                            "expected_hash": f"{execution_job_id}:{owner}:{token}",
                            "observed_hash": (
                                f"{existing['execution_job_id']}:"
                                f"{existing['worker_instance_id']}:"
                                f"{existing['fence_token']}"
                            ),
                        },),
                    )
            else:
                con.execute(
                    f"""
                    INSERT INTO {ROLLBACK_LOCK_TABLE}(
                      changeset_id,execution_job_id,worker_instance_id,
                      fence_token,acquired_at
                    ) VALUES(?,?,?,?,?)
                    """,
                    (changeset_id, execution_job_id, owner, token, _now()),
                )
            rows = con.execute(
                f"SELECT * FROM {FILE_TABLE} WHERE changeset_id=? ORDER BY ordinal DESC",
                (changeset_id,),
            ).fetchall()
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    restored: list[str] = []
    conflicts: list[dict[str, str]] = []
    actions: list[Any] = []
    try:
        _assert_execution_fence(project_root, execution_fence)
        for row in rows:
            target = project_root / row["resource"]
            try:
                observed = _current_hash(target)
            except ChangesetError:
                observed = "__invalid_target__"
            base_hash = str(row["base_hash"])
            new_hash = str(row["new_hash"])
            if observed == base_hash:
                continue
            if observed != new_hash:
                conflicts.append({
                    "path": str(row["resource"]),
                    "expected_hash": new_hash,
                    "observed_hash": observed,
                })
                continue
            if base_hash != NEW_FILE_HASH and not Path(row["backup_path"]).is_file():
                conflicts.append({
                    "path": str(row["resource"]),
                    "expected_hash": base_hash,
                    "observed_hash": "backup_missing",
                })
                continue
            actions.append(row)
        if conflicts:
            return RollbackResult(False, conflicts=tuple(conflicts))

        for row in actions:
            _assert_execution_fence(project_root, execution_fence)
            target = project_root / row["resource"]
            backup = Path(row["backup_path"])
            observed = _current_hash(target)
            if observed != str(row["new_hash"]):
                return RollbackResult(
                    False,
                    restored=tuple(sorted(restored)),
                    conflicts=({
                        "path": str(row["resource"]),
                        "expected_hash": str(row["new_hash"]),
                        "observed_hash": observed,
                    },),
                )
            if row["base_hash"] == NEW_FILE_HASH:
                if target.exists() and not target.is_symlink():
                    target.unlink()
                    _fsync_parent(target.parent)
            else:
                _replace_file(target, backup.read_bytes())
            restored.append(str(row["resource"]))
        return RollbackResult(True, restored=tuple(sorted(restored)))
    finally:
        with db.connect(project_root) as con:
            con.execute(
                f"""
                DELETE FROM {ROLLBACK_LOCK_TABLE}
                WHERE changeset_id=? AND execution_job_id=?
                  AND worker_instance_id=? AND fence_token=?
                """,
                (changeset_id, execution_job_id, owner, token),
            )


def recover_incomplete(
    project_root: Path,
    *,
    recovery_fence: ExecutionFence | None = None,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    ensure_schema(project_root)
    recovered: list[str] = []
    conflicts: list[dict[str, Any]] = []
    with db.connect(project_root) as con:
        rows = con.execute(
            f"""
            SELECT * FROM {TRANSACTION_TABLE}
            WHERE status IN ('staging','applying','validating','guarding','rollback_required')
            ORDER BY created_at
            """,
        ).fetchall()
    for row in rows:
        age_seconds = max(0, _now() - int(row["updated_at"] or 0))
        execution_job_id = str(row["execution_job_id"] or "")
        changeset_id = str(row["changeset_id"])
        effective_fence: ExecutionFence | None = None
        if execution_job_id:
            if recovery_fence is None or recovery_fence.job_id != execution_job_id:
                continue
            _assert_execution_fence(project_root, recovery_fence)
            with db.connect(project_root) as con:
                con.execute("BEGIN IMMEDIATE")
                try:
                    updated = con.execute(
                        f"""
                        UPDATE {TRANSACTION_TABLE}
                        SET worker_instance_id=?,fence_token=?,owner_pid=?,updated_at=?
                        WHERE changeset_id=? AND execution_job_id=?
                          AND fence_token<?
                          AND status IN ('staging','applying','validating','guarding','rollback_required')
                        """,
                        (
                            recovery_fence.worker_instance_id,
                            recovery_fence.fence_token,
                            os.getpid(),
                            _now(),
                            changeset_id,
                            recovery_fence.job_id,
                            recovery_fence.fence_token,
                        ),
                    ).rowcount
                    if updated:
                        con.execute(
                            f"""
                            UPDATE {LOCK_TABLE}
                            SET worker_instance_id=?,fence_token=?
                            WHERE changeset_id=? AND execution_job_id=?
                            """,
                            (
                                recovery_fence.worker_instance_id,
                                recovery_fence.fence_token,
                                changeset_id,
                                recovery_fence.job_id,
                            ),
                        )
                    con.execute("COMMIT")
                except Exception:
                    con.execute("ROLLBACK")
                    raise
            if not updated:
                continue
            effective_fence = recovery_fence
        else:
            if recovery_fence is not None or age_seconds <= STALE_TRANSACTION_SECONDS:
                continue

        rollback = _rollback(project_root, changeset_id, effective_fence)
        if not rollback.ok:
            error = {
                "verdict": "TENOR_CHANGESET_ROLLBACK_CONFLICT",
                "conflicts": list(rollback.conflicts),
                "restored": list(rollback.restored),
            }
            _update_transaction(
                project_root,
                changeset_id,
                "rollback_conflict",
                error=error,
                execution_fence=effective_fence,
                expected_statuses=RECOVERABLE_TRANSACTION_STATUSES,
            )
            _release_locks(project_root, changeset_id, effective_fence)
            conflicts.append({"changeset_id": changeset_id, **error})
            continue
        _update_transaction(
            project_root,
            changeset_id,
            "rolled_back_recovered",
            error={
                "verdict": "TENOR_CHANGESET_RECOVERED_AFTER_INTERRUPTION",
                "restored": list(rollback.restored),
            },
            execution_fence=effective_fence,
            expected_statuses=RECOVERABLE_TRANSACTION_STATUSES,
        )
        _release_locks(project_root, changeset_id, effective_fence)
        recovered.append(changeset_id)
    return {
        "ok": not conflicts,
        "verdict": (
            "TENOR_CHANGESET_RECOVERY_COMPLETE"
            if not conflicts
            else "TENOR_CHANGESET_RECOVERY_ROLLBACK_CONFLICT"
        ),
        "recovered": recovered,
        "conflicts": conflicts,
    }


def _rollback_failure(
    project_root: Path,
    changeset_id: str,
    *,
    execution_fence: ExecutionFence | None,
    verdict: str,
    response_fields: dict[str, Any] | None = None,
    cause: str = "",
) -> dict[str, Any]:
    fields = dict(response_fields or {})
    rollback = _rollback(project_root, changeset_id, execution_fence)
    restored = list(rollback.restored)
    if not rollback.ok:
        error = {
            "verdict": "TENOR_CHANGESET_ROLLBACK_CONFLICT",
            "cause": cause or verdict,
            "restored": restored,
            "conflicts": [dict(item) for item in rollback.conflicts],
            **fields,
        }
        _update_transaction(
            project_root,
            changeset_id,
            "rollback_conflict",
            error=error,
            execution_fence=execution_fence,
            expected_statuses=RECOVERABLE_TRANSACTION_STATUSES,
        )
        return {
            "ok": False,
            "changeset_id": changeset_id,
            **error,
        }
    error = {
        "verdict": verdict,
        "restored": restored,
        **fields,
    }
    if cause:
        error["cause"] = cause
    _update_transaction(
        project_root,
        changeset_id,
        "rolled_back",
        error=error,
        execution_fence=execution_fence,
        expected_statuses=RECOVERABLE_TRANSACTION_STATUSES,
    )
    return {
        "ok": False,
        "changeset_id": changeset_id,
        **error,
    }


def _run_validators(validators: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for validator in validators:
        started = time.monotonic()
        try:
            completed = bounded_process.run_bounded(
                validator["argv"],
                cwd=validator["cwd"],
                timeout_seconds=validator["timeout_seconds"],
                output_limit_bytes=MAX_VALIDATOR_OUTPUT_BYTES,
            )
            returncode = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
            timed_out = completed.timed_out
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
    confirm_full_replacements: list[dict[str, Any]],
) -> str:
    payload = {
        "task_id": task_id,
        "changes": changes,
        "validators": validators,
        "confirm_deletions": sorted(confirm_deletions or []),
        "confirm_full_replacements": sorted(
            confirm_full_replacements or [],
            key=lambda item: str(item.get("path") or "") if isinstance(item, dict) else "",
        ),
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
    confirm_full_replacements: list[dict[str, Any]] | None = None,
    request_id: str = "",
    precommit_guard: PrecommitGuard | None = None,
    execution_fence: ExecutionFence | None = None,
    _test_fail_after_replaces: int | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    validators = validators or []
    allowed_resources = allowed_resources or []
    confirm_deletions = confirm_deletions or []
    confirm_full_replacements = confirm_full_replacements or []
    if not agent_id or not task_id:
        return {"ok": False, "verdict": "TENOR_CHANGESET_TASK_IDENTITY_REQUIRED"}
    request_id = (request_id or uuid.uuid4().hex).strip()
    if len(request_id) > 200 or not request_id:
        return {"ok": False, "verdict": "TENOR_CHANGESET_REQUEST_ID_INVALID"}
    ensure_schema(root)
    try:
        _assert_execution_fence(root, execution_fence)
    except ChangesetError as exc:
        return {"ok": False, "verdict": exc.verdict, **exc.details}
    recover_incomplete(root)
    fingerprint = _request_fingerprint(
        task_id,
        changes,
        validators,
        confirm_deletions,
        confirm_full_replacements,
    )
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
        canonical = _canonical_changes(
            root,
            changes,
            allowed_resources,
            confirm_deletions,
            confirm_full_replacements,
        )
        canonical_validators = _canonical_validators(root, validators)
    except ChangesetError as exc:
        return {"ok": False, "verdict": exc.verdict, **exc.details}

    changeset_id = f"cs-{uuid.uuid4().hex[:20]}"
    transaction_dir = _transaction_root(root, changeset_id)
    created = _now()
    try:
        _assert_execution_fence(root, execution_fence)
    except ChangesetError as exc:
        return {"ok": False, "verdict": exc.verdict, **exc.details}
    with db.connect(root) as con:
        inserted = con.execute(
            f"""
            INSERT OR IGNORE INTO {TRANSACTION_TABLE}(
              changeset_id,request_id,request_fingerprint,task_id,agent_id,
              owner_pid,execution_job_id,worker_instance_id,fence_token,
              status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                changeset_id,
                request_id,
                fingerprint,
                task_id,
                agent_id,
                os.getpid(),
                execution_fence.job_id if execution_fence else "",
                execution_fence.worker_instance_id if execution_fence else "",
                execution_fence.fence_token if execution_fence else 0,
                "staging",
                created,
                created,
            ),
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
        _assert_execution_fence(root, execution_fence)
        _acquire_locks(
            root,
            changeset_id,
            agent_id,
            task_id,
            [item["path"] for item in canonical],
            execution_fence,
        )
        for ordinal, item in enumerate(canonical):
            _assert_execution_fence(root, execution_fence)
            if _current_hash(item["target"]) != item["base_hash"]:
                raise ChangesetError("TENOR_CHANGESET_BASE_STALE", {"path": item["path"]})
            backup = transaction_dir / "backup" / f"{ordinal:04d}.bin"
            staged = transaction_dir / "staged" / f"{ordinal:04d}.bin"
            if item["base_hash"] != NEW_FILE_HASH:
                _write_durable(backup, item["target"].read_bytes())
            if item["operation"] != "delete":
                _write_durable(staged, item["content"])
            with db.connect(root) as con:
                inserted_file = con.execute(
                    f"""
                    INSERT INTO {FILE_TABLE}(
                      changeset_id,ordinal,resource,operation,base_hash,new_hash,
                      backup_path,staged_path,applied
                    )
                    SELECT ?,?,?,?,?,?,?,?,0
                    WHERE EXISTS(
                      SELECT 1 FROM {TRANSACTION_TABLE}
                      WHERE changeset_id=? AND execution_job_id=?
                        AND worker_instance_id=? AND fence_token=?
                        AND status='staging'
                    )
                    """,
                    (
                        changeset_id,
                        ordinal,
                        item["path"],
                        item["operation"],
                        item["base_hash"],
                        item["new_hash"],
                        str(backup),
                        str(staged),
                        changeset_id,
                        execution_fence.job_id if execution_fence else "",
                        execution_fence.worker_instance_id if execution_fence else "",
                        execution_fence.fence_token if execution_fence else 0,
                    ),
                ).rowcount
            if not inserted_file:
                _assert_execution_fence(root, execution_fence)
                raise ChangesetError(
                    "TENOR_CHANGESET_STATE_TRANSITION_REJECTED",
                    {"changeset_id": changeset_id, "target_status": "staging"},
                )
        _update_transaction(
            root,
            changeset_id,
            "applying",
            execution_fence=execution_fence,
            expected_statuses=frozenset({"staging"}),
        )
        replaced = 0
        for item in canonical:
            _assert_execution_fence(root, execution_fence)
            if _current_hash(item["target"]) != item["base_hash"]:
                raise ChangesetError("TENOR_CHANGESET_BASE_STALE", {"path": item["path"]})
            with db.connect(root) as con:
                write_intent = con.execute(
                    f"""
                    UPDATE {FILE_TABLE}
                    SET applied=-1
                    WHERE changeset_id=? AND resource=? AND applied=0
                      AND EXISTS(
                        SELECT 1 FROM {TRANSACTION_TABLE}
                        WHERE changeset_id=? AND execution_job_id=?
                          AND worker_instance_id=? AND fence_token=?
                          AND status='applying'
                      )
                    """,
                    (
                        changeset_id,
                        item["path"],
                        changeset_id,
                        execution_fence.job_id if execution_fence else "",
                        execution_fence.worker_instance_id if execution_fence else "",
                        execution_fence.fence_token if execution_fence else 0,
                    ),
                ).rowcount
            if not write_intent:
                _assert_execution_fence(root, execution_fence)
                raise ChangesetError(
                    "TENOR_CHANGESET_STATE_TRANSITION_REJECTED",
                    {"changeset_id": changeset_id, "target_status": "write_intent"},
                )
            _assert_execution_fence(root, execution_fence)
            if item["operation"] == "delete":
                item["target"].unlink()
                _fsync_parent(item["target"].parent)
            else:
                _replace_file(item["target"], item["content"])
            replaced += 1
            _assert_execution_fence(root, execution_fence)
            with db.connect(root) as con:
                marked = con.execute(
                    f"""
                    UPDATE {FILE_TABLE}
                    SET applied=1
                    WHERE changeset_id=? AND resource=?
                      AND EXISTS(
                        SELECT 1 FROM {TRANSACTION_TABLE}
                        WHERE changeset_id=? AND execution_job_id=?
                          AND worker_instance_id=? AND fence_token=?
                          AND status='applying'
                      )
                    """,
                    (
                        changeset_id,
                        item["path"],
                        changeset_id,
                        execution_fence.job_id if execution_fence else "",
                        execution_fence.worker_instance_id if execution_fence else "",
                        execution_fence.fence_token if execution_fence else 0,
                    ),
                ).rowcount
            if not marked:
                _assert_execution_fence(root, execution_fence)
                raise ChangesetError(
                    "TENOR_CHANGESET_STATE_TRANSITION_REJECTED",
                    {"changeset_id": changeset_id, "target_status": "applying"},
                )
            if _test_fail_after_replaces is not None and replaced >= _test_fail_after_replaces:
                raise ChangesetError("TENOR_CHANGESET_TEST_INJECTED_FAILURE")

        _update_transaction(
            root,
            changeset_id,
            "validating",
            execution_fence=execution_fence,
            expected_statuses=frozenset({"applying"}),
        )
        validation_results = _run_validators(canonical_validators)
        _assert_execution_fence(root, execution_fence)
        if any(not result["ok"] for result in validation_results):
            return _rollback_failure(
                root,
                changeset_id,
                execution_fence=execution_fence,
                verdict="TENOR_CHANGESET_VALIDATION_FAILED_ROLLED_BACK",
                response_fields={"validators": validation_results},
            )
        drifted_resources: list[str] = []
        for item in canonical:
            try:
                observed_hash = _current_hash(item["target"])
            except ChangesetError:
                observed_hash = "__invalid_target__"
            if observed_hash != item["new_hash"]:
                drifted_resources.append(item["path"])
        if drifted_resources:
            return _rollback_failure(
                root,
                changeset_id,
                execution_fence=execution_fence,
                verdict="TENOR_CHANGESET_VALIDATOR_MUTATION_ROLLED_BACK",
                response_fields={
                    "drifted_resources": sorted(drifted_resources),
                    "validators": validation_results,
                },
            )
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
            "verdict": "TENOR_CHANGESET_GUARDING" if precommit_guard is not None else "TENOR_CHANGESET_COMMITTED",
            "changeset_id": changeset_id,
            "request_id": request_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "files": files,
            "validators": validation_results,
        }
        if precommit_guard is not None:
            _update_transaction(
                root,
                changeset_id,
                "guarding",
                result=result,
                execution_fence=execution_fence,
                expected_statuses=frozenset({"validating"}),
            )
            try:
                guard = precommit_guard(dict(result))
            except Exception as exc:
                guard = {
                    "ok": False,
                    "verdict": "TENOR_CHANGESET_PRECOMMIT_GUARD_EXCEPTION",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            _assert_execution_fence(root, execution_fence)
            if not isinstance(guard, dict) or not guard.get("ok"):
                return _rollback_failure(
                    root,
                    changeset_id,
                    execution_fence=execution_fence,
                    verdict="TENOR_CHANGESET_PRECOMMIT_GUARD_FAILED_ROLLED_BACK",
                    response_fields={
                        "guard": (
                            guard
                            if isinstance(guard, dict)
                            else {"value": repr(guard)}
                        ),
                        "validators": validation_results,
                    },
                )
        result["verdict"] = "TENOR_CHANGESET_COMMITTED"
        result["committed_at"] = _now()
        _assert_execution_fence(root, execution_fence)
        _update_transaction(
            root,
            changeset_id,
            "committed",
            result=result,
            execution_fence=execution_fence,
            expected_statuses=(
                frozenset({"guarding"})
                if precommit_guard is not None
                else frozenset({"validating"})
            ),
        )
        return result
    except ChangesetError as exc:
        if exc.verdict == "TENOR_CHANGESET_EXECUTION_FENCE_LOST":
            return {
                "ok": False,
                "verdict": exc.verdict,
                "changeset_id": changeset_id,
                **exc.details,
            }
        verdict = (
            "TENOR_CHANGESET_BASE_STALE"
            if exc.verdict == "TENOR_CHANGESET_BASE_STALE"
            else "TENOR_CHANGESET_APPLY_FAILED_ROLLED_BACK"
        )
        try:
            return _rollback_failure(
                root,
                changeset_id,
                execution_fence=execution_fence,
                verdict=verdict,
                response_fields=dict(exc.details),
                cause=exc.verdict,
            )
        except ChangesetError as rollback_exc:
            if rollback_exc.verdict == "TENOR_CHANGESET_EXECUTION_FENCE_LOST":
                return {
                    "ok": False,
                    "verdict": rollback_exc.verdict,
                    "changeset_id": changeset_id,
                    **rollback_exc.details,
                }
            raise
    except Exception as exc:
        try:
            return _rollback_failure(
                root,
                changeset_id,
                execution_fence=execution_fence,
                verdict="TENOR_CHANGESET_APPLY_FAILED_ROLLED_BACK",
                response_fields={"reason": f"{type(exc).__name__}: {exc}"},
            )
        except ChangesetError as rollback_exc:
            if rollback_exc.verdict == "TENOR_CHANGESET_EXECUTION_FENCE_LOST":
                return {
                    "ok": False,
                    "verdict": rollback_exc.verdict,
                    "changeset_id": changeset_id,
                    **rollback_exc.details,
                }
            raise
    finally:
        _release_locks(root, changeset_id, execution_fence)
        if transaction_dir.exists():
            with db.connect(root) as con:
                terminal = con.execute(
                    f"""
                    SELECT 1 FROM {TRANSACTION_TABLE}
                    WHERE changeset_id=?
                      AND status IN ('committed','rolled_back','rolled_back_recovered')
                    """,
                    (changeset_id,),
                ).fetchone()
            if terminal:
                shutil.rmtree(transaction_dir, ignore_errors=True)
