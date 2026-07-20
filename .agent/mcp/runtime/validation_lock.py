from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:
    import fcntl  # type: ignore
except Exception:
    fcntl = None  # type: ignore


class ValidationRuntimeBusy(RuntimeError):
    def __init__(self, lock_path: Path, timeout_seconds: float):
        super().__init__("VALIDATION_RUNTIME_BUSY")
        self.lock_path = lock_path
        self.timeout_seconds = timeout_seconds


def validation_runtime_busy_message(lock_path: Path) -> str:
    return f"VALIDATION_RUNTIME_BUSY_RUN_SEQUENTIALLY: {lock_path}"


def default_lock_path(root: Path | None = None) -> Path:
    base = (root or Path(__file__).resolve().parents[3]).resolve()
    return base / ".agent" / "state" / "runtime" / "validation-smoke.lock"


def _quiesce_validation_database(database: Path) -> None:
    """Checkpoint a healthy WAL database before disposable-state removal.

    A malformed database is intentionally not repaired here: validation state
    owns no project truth and will be removed by the caller.  The checkpoint
    prevents an old WAL generation from being attached to the next database
    created at the same path on filesystems with delayed metadata visibility.
    """

    if not database.is_file():
        return
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(database), timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
        if not mode or str(mode[0]).lower() != "delete":
            raise sqlite3.OperationalError("validation database did not leave WAL mode")
    except sqlite3.Error:
        # Corrupt disposable validation state is removed by the bounded sweep.
        return
    finally:
        if connection is not None:
            connection.close()


def _fsync_directory(path: Path) -> None:
    """Persist unlink metadata where directory fsync is supported."""

    descriptor: int | None = None
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)


def reset_validation_runtime_database(root: Path | None = None, retries: int = 20) -> None:
    """Remove the disposable validation database and all journal sidecars.

    Callers must hold ``validation_runtime_lock`` and must stop child MCP
    processes first. Validation state is intentionally disposable: rebuilding a
    fresh migrated database avoids carrying free-list or journal state between
    independent smoke runs. Sidecars are removed first and Windows sharing
    violations receive bounded retries.
    """

    base = (root or Path(__file__).resolve().parents[3]).resolve()
    runtime = base / ".agent" / "state" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    database = runtime / "coordination.sqlite"
    _quiesce_validation_database(database)
    attempts = max(1, min(int(retries), 100))
    targets = (
        database,
        runtime / "coordination.sqlite-wal",
        runtime / "coordination.sqlite-shm",
    )
    last_errors: dict[str, str] = {}
    for attempt in range(attempts):
        last_errors = {}
        for path in targets:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except PermissionError as exc:
                last_errors[str(path)] = str(exc)
        remaining = [str(path) for path in targets if path.exists()]
        if not remaining:
            _fsync_directory(runtime)
            return
        if attempt + 1 < attempts:
            time.sleep(min(0.25, 0.01 * (2 ** min(attempt, 5))))
    raise RuntimeError(
        "VALIDATION_RUNTIME_RESET_FAILED "
        f"remaining={remaining} errors={last_errors}"
    )


@contextmanager
def validation_runtime_lock(root: Path | None = None, timeout_seconds: float = 300.0, poll_interval: float = 0.1) -> Iterator[Path]:
    lock_path = default_lock_path(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    handle = lock_path.open("a+", encoding="utf-8")
    acquired = False
    try:
        while True:
            if fcntl is None:
                try:
                    marker_fd = os.open(str(lock_path.with_suffix(".mkdirlock")), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(marker_fd)
                    acquired = True
                    break
                except FileExistsError:
                    pass
            else:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    pass
            if time.monotonic() >= deadline:
                raise ValidationRuntimeBusy(lock_path, timeout_seconds)
            time.sleep(max(0.01, min(float(poll_interval), 1.0)))
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} acquired_at={time.time()}\n")
        handle.flush()
        os.fsync(handle.fileno())
        yield lock_path
    finally:
        if acquired and fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        if acquired and fcntl is None:
            try:
                lock_path.with_suffix(".mkdirlock").unlink()
            except FileNotFoundError:
                pass
        handle.close()
