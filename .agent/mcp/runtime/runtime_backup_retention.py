from __future__ import annotations

import json
import os
import shutil
try:
    import fcntl  # type: ignore
except Exception:
    fcntl = None  # type: ignore
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .state_paths import prepare_state_dirs
except Exception:
    from state_paths import prepare_state_dirs  # type: ignore

BACKUP_PREFIX = "coordination.backup-"
BACKUP_SUFFIX = ".sqlite"
DEFAULT_KEEP_LAST = 20

RUNTIME_BACKUP_REPORT = "RUNTIME_BACKUP_REPORT"
RUNTIME_BACKUP_ORGANIZED = "RUNTIME_BACKUP_ORGANIZED"
RUNTIME_BACKUP_CLEANED = "RUNTIME_BACKUP_CLEANED"
RUNTIME_BACKUP_DRY_RUN = "RUNTIME_BACKUP_DRY_RUN"
VALIDATION_RUNTIME_BUSY = "VALIDATION_RUNTIME_BUSY"
RUNTIME_ACTIVE_AGENTS_BUSY = "RUNTIME_ACTIVE_AGENTS_BUSY"
RUNTIME_BACKUP_RETENTION_REFUSED = "RUNTIME_BACKUP_RETENTION_REFUSED"


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    size: int
    mtime: float
    location: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "name": self.path.name,
            "size": self.size,
            "mtime": self.mtime,
            "location": self.location,
        }


def _runtime_dir(project_root: Path) -> Path:
    return prepare_state_dirs(project_root)["runtime"]


def _backups_dir(runtime_dir: Path) -> Path:
    return runtime_dir / "backups"


def _is_backup_name(path: Path) -> bool:
    return path.name.startswith(BACKUP_PREFIX) and path.name.endswith(BACKUP_SUFFIX)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_runtime_child(path: Path, runtime_dir: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
        runtime = runtime_dir.resolve(strict=True)
    except FileNotFoundError:
        return False
    return _is_relative_to(resolved, runtime) and resolved != runtime


def _validate_keep_last(keep_last: int) -> int:
    try:
        value = int(keep_last)
    except (TypeError, ValueError) as exc:
        raise ValueError("keep_last must be an integer") from exc
    if value < 1 or value > 10000:
        raise ValueError("keep_last must be between 1 and 10000")
    return value


def _backup_info(path: Path, runtime_dir: Path) -> BackupInfo:
    if not _safe_runtime_child(path, runtime_dir):
        raise RuntimeError(RUNTIME_BACKUP_RETENTION_REFUSED)
    stat = path.stat()
    location = "runtime_root" if path.parent == runtime_dir else "backups"
    return BackupInfo(path=path, size=stat.st_size, mtime=stat.st_mtime, location=location)


def list_runtime_backups(runtime_dir: Path) -> list[BackupInfo]:
    runtime = runtime_dir.resolve()
    backups: list[BackupInfo] = []
    if runtime.exists():
        for path in runtime.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"):
            if path.is_file() and _is_backup_name(path):
                backups.append(_backup_info(path, runtime))
    backup_dir = _backups_dir(runtime)
    if backup_dir.exists():
        for path in backup_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"):
            if path.is_file() and _is_backup_name(path):
                backups.append(_backup_info(path, runtime))
    backups.sort(key=lambda item: (item.mtime, item.path.name), reverse=True)
    return backups


def _active_agents(db_file: Path) -> list[str]:
    if not db_file.is_file():
        return []
    uri = f"file:{db_file}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute("SELECT agent_id FROM agents WHERE status='active'").fetchall()
        return [str(row[0]) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _validation_lock_is_active(lock: Path) -> bool:
    if not lock.exists():
        return False
    if fcntl is None:
        return True
    try:
        with lock.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            finally:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
    except OSError:
        return True
    return False


def assert_runtime_cleanup_safe(project_root: Path) -> dict[str, Any]:
    runtime = _runtime_dir(project_root)
    lock = runtime / "validation-smoke.lock"
    if _validation_lock_is_active(lock):
        return {
            "ok": False,
            "verdict": VALIDATION_RUNTIME_BUSY,
            "reason": "validation-smoke.lock is active",
            "lock": str(lock),
        }
    agents = _active_agents(runtime / "coordination.sqlite")
    if agents:
        return {
            "ok": False,
            "verdict": RUNTIME_ACTIVE_AGENTS_BUSY,
            "reason": "active agents are present in coordination.sqlite",
            "active_agents": agents,
        }
    return {
        "ok": True,
        "verdict": "RUNTIME_CLEANUP_SAFE",
        "active_agents": [],
        "validation_lock": False,
    }


def runtime_backup_report(project_root: Path, keep_last: int = DEFAULT_KEEP_LAST) -> dict[str, Any]:
    keep = _validate_keep_last(keep_last)
    runtime = _runtime_dir(project_root)
    try:
        backups = list_runtime_backups(runtime)
    except RuntimeError as exc:
        return {"ok": False, "verdict": RUNTIME_BACKUP_RETENTION_REFUSED, "reason": str(exc), "runtime_dir": str(runtime)}
    total_bytes = sum(item.size for item in backups)
    top_level = [item for item in backups if item.location == "runtime_root"]
    ordered_oldest = sorted(backups, key=lambda item: (item.mtime, item.path.name))
    return {
        "ok": True,
        "verdict": RUNTIME_BACKUP_REPORT,
        "runtime_dir": str(runtime),
        "backups_dir": str(_backups_dir(runtime)),
        "keep_last": keep,
        "backup_count": len(backups),
        "top_level_backup_count": len(top_level),
        "total_bytes": total_bytes,
        "oldest_backup": ordered_oldest[0].as_dict() if ordered_oldest else None,
        "newest_backup": backups[0].as_dict() if backups else None,
        "backups": [item.as_dict() for item in backups],
        "safety": assert_runtime_cleanup_safe(project_root),
    }


def organize_runtime_backups(project_root: Path, *, apply: bool = False) -> dict[str, Any]:
    if apply:
        safety = assert_runtime_cleanup_safe(project_root)
        if not safety.get("ok"):
            return safety
    runtime = _runtime_dir(project_root)
    backups_dir = _backups_dir(runtime)
    try:
        top_level = [item for item in list_runtime_backups(runtime) if item.location == "runtime_root"]
    except RuntimeError as exc:
        return {"ok": False, "verdict": RUNTIME_BACKUP_RETENTION_REFUSED, "reason": str(exc), "runtime_dir": str(runtime)}
    moves: list[dict[str, str]] = []
    for item in top_level:
        target = backups_dir / item.path.name
        if not _safe_runtime_child(item.path, runtime) or not _safe_runtime_child(target, runtime):
            return {
                "ok": False,
                "verdict": RUNTIME_BACKUP_RETENTION_REFUSED,
                "reason": "backup move target escapes runtime dir",
                "source": str(item.path),
                "target": str(target),
            }
        moves.append({"source": str(item.path), "target": str(target)})

    if apply:
        backups_dir.mkdir(parents=True, exist_ok=True)
        for move in moves:
            source = Path(move["source"])
            target = Path(move["target"])
            if source.exists():
                if target.exists():
                    target = backups_dir / f"{source.stem}.{os.getpid()}{source.suffix}"
                    move["target"] = str(target)
                shutil.move(str(source), str(target))

    return {
        "ok": True,
        "verdict": RUNTIME_BACKUP_ORGANIZED if apply else RUNTIME_BACKUP_DRY_RUN,
        "apply": apply,
        "runtime_dir": str(runtime),
        "moves": moves,
        "moved_count": len(moves) if apply else 0,
        "would_move_count": 0 if apply else len(moves),
    }


def cleanup_runtime_backups(project_root: Path, *, keep_last: int = DEFAULT_KEEP_LAST, apply: bool = False) -> dict[str, Any]:
    keep = _validate_keep_last(keep_last)
    safety = assert_runtime_cleanup_safe(project_root)
    if not safety.get("ok"):
        return safety

    runtime = _runtime_dir(project_root)
    backups_dir = _backups_dir(runtime)
    try:
        backups = [item for item in list_runtime_backups(runtime) if item.location == "backups"]
    except RuntimeError as exc:
        return {"ok": False, "verdict": RUNTIME_BACKUP_RETENTION_REFUSED, "reason": str(exc), "runtime_dir": str(runtime)}
    backups.sort(key=lambda item: (item.mtime, item.path.name), reverse=True)
    delete = backups[keep:]
    deleted: list[str] = []
    refused: list[str] = []

    for item in delete:
        if not _safe_runtime_child(item.path, runtime) or item.path.parent.resolve() != backups_dir.resolve():
            refused.append(str(item.path))
            continue
        if apply and item.path.exists():
            item.path.unlink()
            deleted.append(str(item.path))

    if refused:
        return {
            "ok": False,
            "verdict": RUNTIME_BACKUP_RETENTION_REFUSED,
            "reason": "one or more backup paths escaped runtime/backups",
            "refused": refused,
        }

    return {
        "ok": True,
        "verdict": RUNTIME_BACKUP_CLEANED if apply else RUNTIME_BACKUP_DRY_RUN,
        "apply": apply,
        "runtime_dir": str(runtime),
        "backups_dir": str(backups_dir),
        "keep_last": keep,
        "backup_count": len(backups),
        "delete_count": len(delete),
        "would_delete": [str(item.path) for item in delete] if not apply else [],
        "deleted": deleted,
    }


def as_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
