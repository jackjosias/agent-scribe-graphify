#!/usr/bin/env python3
from __future__ import annotations

"""V2.16 bootstrap policy facade.

The bundle/bootstrap implementation lives in ``_scribe_bootstrap_impl``. This
public module owns atomic publication semantics so every caller receives the
same per-destination serialization and bounded Windows retry behavior.
"""

import os
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

import _scribe_bootstrap_impl as _impl

_REPLACE_MAX_ATTEMPTS = 10
_REPLACE_BASE_DELAY_SECONDS = 0.01
_REPLACE_MAX_DELAY_SECONDS = 0.25
_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, "_PathLockEntry"] = {}


class _PathLockEntry:
    __slots__ = ("lock", "users")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.users = 0


def _path_lock_key(path: Any) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


@contextmanager
def _destination_transaction(path: Any) -> Iterator[None]:
    """Serialize one destination without globally blocking unrelated files.

    ``users`` counts both owners and waiters, preventing registry eviction while
    another thread is queued. The entry is removed after the final user exits,
    so long-lived hosts do not accumulate one lock per historical path.
    """

    key = _path_lock_key(path)
    with _PATH_LOCKS_GUARD:
        entry = _PATH_LOCKS.get(key)
        if entry is None:
            entry = _PathLockEntry()
            _PATH_LOCKS[key] = entry
        entry.users += 1
    try:
        with entry.lock:
            yield
    finally:
        with _PATH_LOCKS_GUARD:
            current = _PATH_LOCKS.get(key)
            entry.users -= 1
            if current is entry and entry.users == 0:
                _PATH_LOCKS.pop(key, None)


def _atomic_lock_registry_size() -> int:
    with _PATH_LOCKS_GUARD:
        return len(_PATH_LOCKS)


def _atomic_replace(src: str, dst: Any) -> None:
    """Atomically publish ``src`` with bounded Windows sharing retries."""

    for attempt in range(_REPLACE_MAX_ATTEMPTS):
        try:
            os.replace(src, str(dst))
            return
        except PermissionError:
            if attempt + 1 >= _REPLACE_MAX_ATTEMPTS:
                raise
            delay = min(
                _REPLACE_BASE_DELAY_SECONDS * (2 ** attempt),
                _REPLACE_MAX_DELAY_SECONDS,
            )
            time.sleep(delay)


def _atomic_text_write(path: Any, content: str) -> None:
    """Write complete UTF-8 text through an exclusive sibling temp file."""

    with _destination_transaction(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            _atomic_replace(temporary, path)
        finally:
            try:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            except OSError:
                pass


# Install the canonical writers into the implementation module immediately.
_impl._atomic_replace = _atomic_replace
_impl._atomic_text_write = _atomic_text_write

# Public override points retained for tests and bounded host customization.
run_installer = _impl.run_installer
default_runner = _impl.default_runner


def bootstrap_project(*args: Any, **kwargs: Any) -> Any:
    # Existing tests patch these public names. Synchronize them before delegation
    # while keeping the implementation module private.
    _impl.run_installer = globals()["run_installer"]
    _impl.default_runner = globals()["default_runner"]
    _impl._atomic_replace = _atomic_replace
    _impl._atomic_text_write = _atomic_text_write
    return _impl.bootstrap_project(*args, **kwargs)


for _name in dir(_impl):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_impl, _name)


def __getattr__(name: str) -> Any:
    return getattr(_impl, name)


if __name__ == "__main__":
    raise SystemExit(_impl.main())
