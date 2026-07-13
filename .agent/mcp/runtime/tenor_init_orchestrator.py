from __future__ import annotations

"""TENOR init facade with per-destination serialization for lock publication."""

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import _tenor_init_orchestrator_impl as _impl

for _name in dir(_impl):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_impl, _name)

_ORIGINAL_ATOMIC_LOCK_WRITE = _impl._atomic_lock_write
_DESTINATION_LOCKS_GUARD = threading.Lock()
_DESTINATION_LOCKS: dict[str, dict[str, Any]] = {}


def _destination_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


@contextmanager
def _destination_lock(path: Path) -> Iterator[None]:
    key = _destination_key(path)
    with _DESTINATION_LOCKS_GUARD:
        entry = _DESTINATION_LOCKS.get(key)
        if entry is None:
            entry = {"lock": threading.RLock(), "users": 0}
            _DESTINATION_LOCKS[key] = entry
        entry["users"] = int(entry["users"]) + 1
        lock = entry["lock"]

    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _DESTINATION_LOCKS_GUARD:
            current = _DESTINATION_LOCKS.get(key)
            if current is entry:
                current["users"] = int(current["users"]) - 1
                if int(current["users"]) <= 0:
                    _DESTINATION_LOCKS.pop(key, None)


def _atomic_lock_write(path: Path, payload: dict[str, Any]) -> None:
    """Serialize the complete temp-write/fsync/replace transaction per path."""

    with _destination_lock(path):
        _ORIGINAL_ATOMIC_LOCK_WRITE(path, payload)


# Internal functions defined in the implementation resolve this global at call
# time, so both public and internal paths receive the same serialization.
_impl._atomic_lock_write = _atomic_lock_write
