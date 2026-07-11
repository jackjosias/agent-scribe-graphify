from __future__ import annotations

import json
import os
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

LOCK_RELATIVE = Path(".agent") / ".tenor-init.lock"
SCRIBE_RELATIVE = Path("AGENT-MEMOIRE_PROJECT_STATUS.scribe")


class TenorInitBusy(RuntimeError):
    def __init__(self, lock: dict[str, Any]) -> None:
        super().__init__(TENOR_INIT_ALREADY_RUNNING)
        self.lock = lock


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
    """Classify the installation before SCRIBE creation/adoption, then prepare runtime.

    This function is the installation authority for TENOR INIT.  The presence of
    AGENT-MEMOIRE_PROJECT_STATUS.scribe never decides whether the bundle belongs
    to the same project.  The installation manifest/fingerprint decides that
    first; SCRIBE is adopted or created only after project-bound state is safe.
    """

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


def _lock_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / LOCK_RELATIVE


def _read_lock(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _lock_is_stale(payload: dict[str, Any], stale_after_seconds: float) -> bool:
    try:
        created_epoch = float(payload.get("created_epoch", 0.0))
    except (TypeError, ValueError):
        return True
    if created_epoch <= 0:
        return True
    return (time.time() - created_epoch) > stale_after_seconds


def acquire_tenor_init_lock(
    project_root: Path | str,
    *,
    wait_timeout_seconds: float = 180.0,
    stale_after_seconds: float = 900.0,
    poll_seconds: float = 0.10,
    on_wait: Callable[[dict[str, Any]], None] | None = None,
) -> TenorInitLock:
    """Serialize shared TENOR bootstrap while allowing many agent sessions.

    Six terminals may launch TENOR INIT in the same project.  Only the shared
    installation/bootstrap phase is serialized.  Once the lock is released,
    every terminal may register its own agent session in the common runtime.
    """

    root = Path(project_root).resolve()
    path = _lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, wait_timeout_seconds)
    notified = False

    while True:
        nonce = uuid.uuid4().hex
        payload = {
            "schema": "tenor_init_lock_v1",
            "nonce": nonce,
            "pid": os.getpid(),
            "project_root": str(root),
            "created_at": _utc_now(),
            "created_epoch": time.time(),
        }
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            current = _read_lock(path)
            if _lock_is_stale(current, stale_after_seconds):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    current["verdict"] = TENOR_INIT_ALREADY_RUNNING
                    raise TenorInitBusy(current) from None
                continue
            if not notified and on_wait is not None:
                on_wait(current)
                notified = True
            if time.monotonic() >= deadline:
                current["verdict"] = TENOR_INIT_ALREADY_RUNNING
                raise TenorInitBusy(current)
            time.sleep(max(0.01, poll_seconds))
            continue

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return TenorInitLock(path=path, nonce=nonce, payload=payload)


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
