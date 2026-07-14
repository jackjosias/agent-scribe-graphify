from __future__ import annotations

import json
import math
import os
import socket
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class OwnedFileLockTimeout(TimeoutError):
    def __init__(self, path: Path, owner: dict[str, Any]) -> None:
        super().__init__(f"timed out waiting for owned lock: {path}")
        self.path = path
        self.owner = owner


def _read_lock(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _lock_age_seconds(path: Path, payload: dict[str, Any]) -> float:
    try:
        epoch = float(payload.get("created_epoch"))
        if not math.isfinite(epoch) or epoch <= 0:
            raise ValueError("invalid lock epoch")
    except (TypeError, ValueError):
        try:
            epoch = path.stat().st_mtime
        except OSError:
            return float("inf")
    return max(0.0, time.time() - epoch)


def _windows_pid_is_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        error_access_denied = 5
        error_invalid_parameter = 87
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        get_exit_code.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        handle = open_process(process_query_limited_information, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == error_invalid_parameter:
                return False
            # Access denied and unknown probe failures both fail closed: the
            # caller must not steal a lock from an uninspectable process.
            return True
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            close_handle(handle)
    except Exception:
        return True


def _pid_is_alive(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_is_alive(value)
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _is_stale(path: Path, payload: dict[str, Any], stale_after_seconds: float) -> bool:
    if _lock_age_seconds(path, payload) <= stale_after_seconds:
        return False
    owner_host = str(payload.get("hostname") or "")
    if owner_host and owner_host != socket.gethostname():
        return False
    return not _pid_is_alive(payload.get("pid"))


def _remove_exact_lock(path: Path, observed: dict[str, Any]) -> bool:
    expected_nonce = str(observed.get("nonce") or "")
    if expected_nonce:
        current = _read_lock(path)
        if str(current.get("nonce") or "") != expected_nonce:
            return False
    else:
        try:
            before = path.stat()
        except FileNotFoundError:
            return False
        current = _read_lock(path)
        try:
            after = path.stat()
        except FileNotFoundError:
            return False
        if current or (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


@contextmanager
def owned_file_lock(
    path: Path | str,
    *,
    purpose: str,
    timeout_seconds: float = 15.0,
    stale_after_seconds: float = 120.0,
    poll_seconds: float = 0.025,
) -> Iterator[dict[str, Any]]:
    """Acquire a nonce-owned inter-process lock without stealing live owners."""

    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    payload = {
        "schema": "owned_file_lock_v1",
        "nonce": nonce,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "purpose": purpose,
        "created_epoch": time.time(),
    }
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")

    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            owner = _read_lock(lock_path)
            if _is_stale(lock_path, owner, stale_after_seconds) and _remove_exact_lock(lock_path, owner):
                continue
            if time.monotonic() >= deadline:
                raise OwnedFileLockTimeout(lock_path, owner)
            time.sleep(max(0.005, poll_seconds))
            continue

        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            _remove_exact_lock(lock_path, payload)
            raise
        break

    try:
        yield payload
    finally:
        _remove_exact_lock(lock_path, payload)
