from __future__ import annotations

import json
import math
import os
import socket
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from . import installation_state

TENOR_INIT_NEW_INSTALLATION = "TENOR_INIT_NEW_INSTALLATION"
TENOR_INIT_SAME_PROJECT = "TENOR_INIT_SAME_PROJECT"
TENOR_INIT_RELOCATED_PROJECT = "TENOR_INIT_RELOCATED_PROJECT"
TENOR_INIT_LEGACY_INSTALLATION = "TENOR_INIT_LEGACY_INSTALLATION"
TENOR_INIT_CORRUPT_INSTALLATION = "TENOR_INIT_CORRUPT_INSTALLATION"

SCRIBE_MEMORY_ADOPT = "SCRIBE_MEMORY_ADOPT"
SCRIBE_MEMORY_CREATE = "SCRIBE_MEMORY_CREATE"

TENOR_INIT_LOCK_ACQUIRED = "TENOR_INIT_LOCK_ACQUIRED"
TENOR_INIT_ALREADY_RUNNING = "TENOR_INIT_ALREADY_RUNNING"
TENOR_INIT_LOCK_OWNERSHIP_LOST = "TENOR_INIT_LOCK_OWNERSHIP_LOST"

LOCK_RELATIVE = Path(".agent") / ".tenor-init.lock"
SCRIBE_RELATIVE = Path("AGENT-MEMOIRE_PROJECT_STATUS.scribe")


class TenorInitBusy(RuntimeError):
    def __init__(self, lock: dict[str, Any]) -> None:
        super().__init__(TENOR_INIT_ALREADY_RUNNING)
        self.lock = lock


class TenorInitLockOwnershipLost(RuntimeError):
    pass


@dataclass(frozen=True)
class TenorInitPlan:
    ok: bool
    project_root: str
    classification: str
    installation_verdict: str
    runtime_verdict: str
    project_changed: bool
    relocated: bool
    purge_required: bool
    purge_executed: bool
    previous_project_root: str
    current_project_root: str
    memory_action: str
    scribe_existed_before: bool
    manifest_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TenorInitLock:
    path: Path
    nonce: str
    payload: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _classification_for(detection: dict[str, Any]) -> str:
    verdict = str(detection.get("verdict") or "")
    if verdict == installation_state.AGENT_BUNDLE_RELOCATION_DETECTED:
        return TENOR_INIT_RELOCATED_PROJECT
    if verdict == "LEGACY_STATE_WITHOUT_INSTALL_MANIFEST":
        return TENOR_INIT_LEGACY_INSTALLATION
    if verdict == "CORRUPT_INSTALLATION_MANIFEST":
        return TENOR_INIT_CORRUPT_INSTALLATION
    if verdict == "INSTALLATION_MANIFEST_MISSING_EMPTY_STATE":
        return TENOR_INIT_NEW_INSTALLATION
    return TENOR_INIT_SAME_PROJECT


def classify_installation(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    detection = installation_state.detect_agent_relocation(root)
    classification = _classification_for(detection)
    return {
        "ok": bool(detection.get("ok", False)),
        "project_root": str(root),
        "classification": classification,
        "project_changed": classification != TENOR_INIT_SAME_PROJECT,
        "relocated": bool(detection.get("relocated", False)),
        "purge_required": bool(detection.get("purge_required", False)),
        "previous_project_root": str(detection.get("previous_project_root") or ""),
        "current_project_root": str(detection.get("current_project_root") or root),
        "detection": detection,
    }


def prepare_tenor_init(project_root: Path | str, *, allow_purge: bool = True) -> TenorInitPlan:
    """Classify installation before SCRIBE adoption/creation and prepare state."""

    root = Path(project_root).resolve()
    scribe_existed_before = (root / SCRIBE_RELATIVE).is_file()
    classification = classify_installation(root)
    prepared = installation_state.ensure_fresh_installation_state(root, allow_purge=allow_purge)

    purge = prepared.get("purge") if isinstance(prepared.get("purge"), dict) else None
    return TenorInitPlan(
        ok=bool(prepared.get("ok", False)),
        project_root=str(root),
        classification=str(classification["classification"]),
        installation_verdict=str(prepared.get("verdict") or ""),
        runtime_verdict=str(prepared.get("runtime_verdict") or ""),
        project_changed=bool(classification["project_changed"]),
        relocated=bool(classification["relocated"]),
        purge_required=bool(classification["purge_required"]),
        purge_executed=purge is not None,
        previous_project_root=str(classification["previous_project_root"]),
        current_project_root=str(classification["current_project_root"]),
        memory_action=SCRIBE_MEMORY_ADOPT if scribe_existed_before else SCRIBE_MEMORY_CREATE,
        scribe_existed_before=scribe_existed_before,
        manifest_path=str(prepared.get("manifest_path") or ""),
    )


def finalize_tenor_init(project_root: Path | str) -> dict[str, Any]:
    """Mark the installation ready only after shared bootstrap succeeds."""

    return installation_state.finalize_installation_state(Path(project_root).resolve())


def _lock_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / LOCK_RELATIVE


def _read_lock(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _payload_age_seconds(path: Path, payload: dict[str, Any]) -> float:
    raw = payload.get("updated_epoch")
    if raw is None:
        raw = payload.get("created_epoch")
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
    """Probe a Windows PID without sending any signal or mutating the process."""

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
        # Fail closed: an uninspectable owner must not have its lock stolen.
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


def _lock_is_stale(path: Path, payload: dict[str, Any], stale_after_seconds: float) -> bool:
    if _payload_age_seconds(path, payload) <= stale_after_seconds:
        return False
    owner_host = str(payload.get("hostname") or "")
    if owner_host and owner_host != socket.gethostname():
        return False
    return not _pid_is_alive(payload.get("pid"))


def _remove_stale_lock(path: Path, observed: dict[str, Any]) -> bool:
    """Remove only the exact stale lock that was observed.

    The second read closes the release/re-acquire TOCTOU window: a waiter must
    never unlink a newer owner's lock merely because the previous path vanished
    between FileExistsError and inspection.
    """
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
    return True


def _atomic_replace(src: str, dst: Path) -> None:
    """Publish a sibling temp file atomically with a bounded Windows retry.

    On Windows, os.replace can raise PermissionError while the destination is
    read concurrently; we retry a bounded number of times with backoff and then
    re-raise. We never silently fall back to a non-atomic direct write.
    """
    last_exc: OSError | None = None
    for attempt in range(5):
        try:
            os.replace(src, str(dst))
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt < 4:
                time.sleep(0.01 * (2 ** attempt))
                continue
            raise
    if last_exc is not None:
        raise last_exc


def _atomic_lock_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_replace(temporary, path)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


def acquire_tenor_init_lock(
    project_root: Path | str,
    *,
    wait_timeout_seconds: float = 180.0,
    stale_after_seconds: float = 900.0,
    poll_seconds: float = 0.10,
    on_wait: Callable[[dict[str, Any]], None] | None = None,
) -> TenorInitLock:
    """Serialize shared bootstrap while preserving independent agent sessions."""

    root = Path(project_root).resolve()
    path = _lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, wait_timeout_seconds)
    notified_nonce = ""

    while True:
        nonce = uuid.uuid4().hex
        now = time.time()
        payload = {
            "schema": "tenor_init_lock_v2",
            "nonce": nonce,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "project_root": str(root),
            "stage": "acquired",
            "created_at": _utc_now(),
            "created_epoch": now,
            "updated_at": _utc_now(),
            "updated_epoch": now,
        }
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            current = _read_lock(path)
            if not current and not path.exists():
                continue
            if _lock_is_stale(path, current, stale_after_seconds):
                try:
                    removed = _remove_stale_lock(path, current)
                except OSError:
                    current["verdict"] = TENOR_INIT_ALREADY_RUNNING
                    raise TenorInitBusy(current) from None
                if removed:
                    continue
            current_nonce = str(current.get("nonce") or "")
            if on_wait is not None and current_nonce != notified_nonce:
                on_wait(current)
                notified_nonce = current_nonce
            if time.monotonic() >= deadline:
                current["verdict"] = TENOR_INIT_ALREADY_RUNNING
                current["age_seconds"] = _payload_age_seconds(path, current)
                current["owner_alive"] = _pid_is_alive(current.get("pid"))
                raise TenorInitBusy(current)
            time.sleep(max(0.01, poll_seconds))
            continue

        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return TenorInitLock(path=path, nonce=nonce, payload=payload)


def refresh_tenor_init_lock(lock: TenorInitLock, *, stage: str) -> TenorInitLock:
    current: dict[str, Any] = {}
    for attempt in range(5):
        current = _read_lock(lock.path)
        if current.get("nonce") == lock.nonce:
            break
        if current:
            raise TenorInitLockOwnershipLost(TENOR_INIT_LOCK_OWNERSHIP_LOST)
        if attempt < 4:
            time.sleep(0.01 * (attempt + 1))
    else:
        raise TenorInitLockOwnershipLost(TENOR_INIT_LOCK_OWNERSHIP_LOST)
    now = time.time()
    current.update({"stage": stage, "updated_at": _utc_now(), "updated_epoch": now})
    _atomic_lock_write(lock.path, current)
    return TenorInitLock(path=lock.path, nonce=lock.nonce, payload=current)


def release_tenor_init_lock(lock: TenorInitLock) -> None:
    current = _read_lock(lock.path)
    if current.get("nonce") != lock.nonce:
        return
    try:
        lock.path.unlink()
    except FileNotFoundError:
        pass


@contextmanager
def tenor_init_lock(
    project_root: Path | str,
    *,
    wait_timeout_seconds: float = 180.0,
    stale_after_seconds: float = 900.0,
    on_wait: Callable[[dict[str, Any]], None] | None = None,
) -> Iterator[TenorInitLock]:
    lock = acquire_tenor_init_lock(
        project_root,
        wait_timeout_seconds=wait_timeout_seconds,
        stale_after_seconds=stale_after_seconds,
        on_wait=on_wait,
    )
    try:
        yield lock
    finally:
        release_tenor_init_lock(lock)
