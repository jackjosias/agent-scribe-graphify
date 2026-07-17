from __future__ import annotations

import json
import math
import os
import re
import socket
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .templates import render_minimal_host_instructions

_START = "<!-- agent-scribe-graphify:auto-guard:start -->"
_END = "<!-- agent-scribe-graphify:auto-guard:end -->"
_BLOCK_PATTERN = re.compile(re.escape(_START) + r".*?" + re.escape(_END), re.DOTALL)
_LOCK_TIMEOUT_SECONDS = 15.0
_LOCK_STALE_SECONDS = 60.0
_ATOMIC_REPLACE_ATTEMPTS = 10
_ATOMIC_REPLACE_INITIAL_DELAY_SECONDS = 0.01
_ATOMIC_REPLACE_MAX_DELAY_SECONDS = 0.25
_WINDOWS_TRANSIENT_REPLACE_ERRORS = {5, 32, 33}
IS_WINDOWS = os.name == "nt"
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}


def is_path_safe(target_file: Path, workspace_root: Path) -> bool:
    try:
        target = Path(target_file).resolve(strict=False)
        root = Path(workspace_root).resolve(strict=True)
        return target == root or root in target.parents
    except (OSError, RuntimeError):
        return False


def remove_old_marked_block(content: str) -> str:
    return _BLOCK_PATTERN.sub("", content).strip()


def update_marked_block(content: str, block: str) -> str:
    if _BLOCK_PATTERN.search(content):
        return _BLOCK_PATTERN.sub(block, content)
    if content.strip():
        return f"{content.rstrip()}\n\n{block}\n"
    return f"{block}\n"


def verify_instruction_installation(target_file: Path) -> bool:
    try:
        content = target_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return content.count(_START) == 1 and content.count(_END) == 1 and _BLOCK_PATTERN.search(content) is not None


def _process_lock_for(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


def _lock_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.agent-instructions.lock")


def _read_lock(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _payload_age_seconds(path: Path, payload: dict[str, Any]) -> float:
    raw = payload.get("updated_epoch", payload.get("created_epoch"))
    try:
        epoch = float(raw)
        if not math.isfinite(epoch) or epoch <= 0.0:
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

        get_exit_code_process = kernel32.GetExitCodeProcess
        get_exit_code_process.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        get_exit_code_process.restype = wintypes.BOOL

        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        handle = open_process(process_query_limited_information, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == error_invalid_parameter:
                return False
            if error == error_access_denied:
                return True
            return True
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code_process(handle, ctypes.byref(exit_code)):
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
    if IS_WINDOWS:
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


def _lock_is_stale(path: Path, payload: dict[str, Any]) -> bool:
    if _payload_age_seconds(path, payload) <= _LOCK_STALE_SECONDS:
        return False
    owner_host = str(payload.get("hostname") or "")
    if owner_host and owner_host != socket.gethostname():
        return False
    return not _pid_is_alive(payload.get("pid"))


def _remove_exact_stale_lock(path: Path, observed: dict[str, Any]) -> bool:
    expected_nonce = str(observed.get("nonce") or "")
    if expected_nonce:
        latest = _read_lock(path)
        if str(latest.get("nonce") or "") != expected_nonce:
            return False
    else:
        try:
            before = path.stat()
        except FileNotFoundError:
            return False
        latest = _read_lock(path)
        try:
            after = path.stat()
        except FileNotFoundError:
            return False
        if latest or (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


@contextmanager
def _instruction_transaction(target: Path) -> Iterator[None]:
    """Serialize read/merge/write/verify in-process and across host processes."""

    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(target)
    process_lock = _process_lock_for(target)
    nonce = uuid.uuid4().hex
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    acquired = False

    with process_lock:
        while not acquired:
            payload = {
                "schema": "host_instruction_lock_v1",
                "nonce": nonce,
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "target": str(target.resolve(strict=False)),
                "created_epoch": time.time(),
            }
            encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                acquired = True
            except FileExistsError:
                observed = _read_lock(lock_path)
                if _lock_is_stale(lock_path, observed) and _remove_exact_stale_lock(lock_path, observed):
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Timed out waiting for host instruction lock: {lock_path}")
                time.sleep(min(0.05, remaining))

        try:
            yield
        finally:
            latest = _read_lock(lock_path)
            if str(latest.get("nonce") or "") == nonce:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass


def _atomic_replace_with_retry(source: Path, destination: Path) -> None:
    """Replace a file atomically, retrying bounded Windows sharing races."""

    delay = _ATOMIC_REPLACE_INITIAL_DELAY_SECONDS
    for attempt in range(_ATOMIC_REPLACE_ATTEMPTS):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            retryable = IS_WINDOWS and (
                isinstance(exc, PermissionError)
                or getattr(exc, "winerror", None) in _WINDOWS_TRANSIENT_REPLACE_ERRORS
            )
            if not retryable or attempt + 1 >= _ATOMIC_REPLACE_ATTEMPTS:
                raise
            time.sleep(delay)
            delay = min(_ATOMIC_REPLACE_MAX_DELAY_SECONDS, delay * 2)


def _atomic_text_write(path: Path, content: str) -> None:
    """Write content through an exclusive sibling temporary and atomic replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, existing_mode)
        except OSError:
            pass
        _atomic_replace_with_retry(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def install_host_instructions(
    target_file: Path | str,
    host_type: str,
    workspace_root: Path | str | None = None,
) -> dict[str, Any]:
    target = Path(target_file)
    if workspace_root is not None and not is_path_safe(target, Path(workspace_root)):
        raise ValueError(f"Path traversal detected: {target_file} is outside workspace {workspace_root}")

    try:
        with _instruction_transaction(target):
            existed = target.exists()
            try:
                original = target.read_text(encoding="utf-8") if existed else ""
            except OSError as exc:
                return {"ok": False, "error": "READ_FAILED", "reason": f"Could not read {target_file}: {exc}"}

            updated = update_marked_block(original, render_minimal_host_instructions(host_type))
            changed = updated != original
            if changed:
                _atomic_text_write(target, updated)

            if not verify_instruction_installation(target):
                return {
                    "ok": False,
                    "error": "VERIFY_FAILED",
                    "reason": f"Managed host instruction block was not installed exactly once in {target}",
                }
            return {
                "ok": True,
                "existed": existed,
                "changed": changed,
                "installed_at": str(target.resolve()),
                "host_type": host_type,
            }
    except TimeoutError as exc:
        return {"ok": False, "error": "LOCK_TIMEOUT", "reason": str(exc)}
    except OSError as exc:
        return {"ok": False, "error": "WRITE_FAILED", "reason": f"Could not write to {target_file}: {exc}"}
