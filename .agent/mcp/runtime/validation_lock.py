from __future__ import annotations

import os
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
