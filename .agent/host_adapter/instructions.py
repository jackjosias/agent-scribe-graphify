from __future__ import annotations

"""V2.16 host-instruction transaction facade.

The rendering/install implementation remains in ``_instructions_impl``. This
module owns the contention policy: bounded acquisition, exact-owner cleanup,
immediate recovery of dead local owners, and leak-free per-target thread locks.
"""

import json
import os
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import _instructions_impl as _impl

_LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_STALE_SECONDS = 60.0
_RELEASE_MAX_ATTEMPTS = 10
_RELEASE_BASE_DELAY_SECONDS = 0.01
_RELEASE_MAX_DELAY_SECONDS = 0.25
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, "_ProcessLockEntry"] = {}


class _ProcessLockEntry:
    __slots__ = ("lock", "users")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.users = 0


def _target_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


@contextmanager
def _process_transaction(path: Path) -> Iterator[None]:
    key = _target_key(path)
    with _PROCESS_LOCKS_GUARD:
        entry = _PROCESS_LOCKS.get(key)
        if entry is None:
            entry = _ProcessLockEntry()
            _PROCESS_LOCKS[key] = entry
        entry.users += 1
    try:
        with entry.lock:
            yield
    finally:
        with _PROCESS_LOCKS_GUARD:
            current = _PROCESS_LOCKS.get(key)
            entry.users -= 1
            if current is entry and entry.users == 0:
                _PROCESS_LOCKS.pop(key, None)


def _process_lock_registry_size() -> int:
    with _PROCESS_LOCKS_GUARD:
        return len(_PROCESS_LOCKS)


def _lock_is_stale(path: Path, payload: dict[str, Any]) -> bool:
    """A dead same-host owner is recoverable immediately and exactly.

    Partial/unreadable locks remain protected by the normal age threshold. A
    foreign-host owner is never declared stale from local PID information.
    """

    owner_host = str(payload.get("hostname") or "")
    if owner_host and owner_host != socket.gethostname():
        return False
    owner_pid = payload.get("pid")
    try:
        valid_pid = int(owner_pid) > 0
    except (TypeError, ValueError):
        valid_pid = False
    if valid_pid and not _impl._pid_is_alive(owner_pid):
        return True
    if _impl._payload_age_seconds(path, payload) <= _LOCK_STALE_SECONDS:
        return False
    return valid_pid and not _impl._pid_is_alive(owner_pid)


def _release_owned_lock(path: Path, nonce: str) -> bool:
    """Remove only ``nonce`` with bounded Windows sharing retries."""

    for attempt in range(_RELEASE_MAX_ATTEMPTS):
        latest = _impl._read_lock(path)
        if not latest:
            return not path.exists()
        if str(latest.get("nonce") or "") != nonce:
            return False
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if attempt + 1 >= _RELEASE_MAX_ATTEMPTS:
                raise
            delay = min(
                _RELEASE_BASE_DELAY_SECONDS * (2 ** attempt),
                _RELEASE_MAX_DELAY_SECONDS,
            )
            time.sleep(delay)
    return False


@contextmanager
def _instruction_transaction(target: Path) -> Iterator[None]:
    """Serialize read/merge/write/verify across threads and processes."""

    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _impl._lock_path(target)
    nonce = uuid.uuid4().hex
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    wait_round = 0

    with _process_transaction(target):
        while True:
            payload = {
                "schema": "host_instruction_lock_v2",
                "nonce": nonce,
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "target": str(target.resolve(strict=False)),
                "created_epoch": time.time(),
            }
            encoded = (
                json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
            ).encode("utf-8")
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                break
            except FileExistsError:
                observed = _impl._read_lock(lock_path)
                if _lock_is_stale(lock_path, observed) and _impl._remove_exact_stale_lock(
                    lock_path,
                    observed,
                ):
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    owner = {
                        "pid": observed.get("pid"),
                        "hostname": observed.get("hostname"),
                        "nonce_prefix": str(observed.get("nonce") or "")[:8],
                        "age_seconds": _impl._payload_age_seconds(lock_path, observed),
                    }
                    raise TimeoutError(
                        "Timed out waiting for host instruction lock "
                        f"{lock_path}; owner={owner}"
                    )
                wait_round += 1
                base = min(0.01 * (1.5 ** min(wait_round, 8)), 0.20)
                jitter = (int(nonce[:2], 16) / 255.0) * 0.01
                time.sleep(min(base + jitter, remaining))

        try:
            yield
        finally:
            _release_owned_lock(lock_path, nonce)


# Apply the public transaction policy to the implementation's global lookups.
_impl._LOCK_TIMEOUT_SECONDS = _LOCK_TIMEOUT_SECONDS
_impl._LOCK_STALE_SECONDS = _LOCK_STALE_SECONDS
_impl._lock_is_stale = _lock_is_stale
_impl._instruction_transaction = _instruction_transaction

for _name in dir(_impl):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_impl, _name)


def __getattr__(name: str) -> Any:
    return getattr(_impl, name)
