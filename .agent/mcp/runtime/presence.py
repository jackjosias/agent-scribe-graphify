from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from runtime import db


DEFAULT_INTERVAL_SECONDS = 30
MIN_INTERVAL_SECONDS = 5
MAX_INTERVAL_SECONDS = 300

_GUARD = threading.RLock()
_WORKERS: dict[tuple[str, str], tuple[threading.Thread, threading.Event]] = {}


def _interval_seconds() -> int:
    raw = os.environ.get("AGENT_PRESENCE_HEARTBEAT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, min(value, MAX_INTERVAL_SECONDS))


def _worker(project_root: Path, agent_id: str, stop: threading.Event, interval: int) -> None:
    while not stop.wait(interval):
        try:
            agent = db.get_agent(agent_id, project_root)
            if not agent or agent.get("status") == "retired":
                return
            db.heartbeat(agent_id, project_root)
        except Exception:
            # A transient SQLite lock or runtime rotation must not kill the
            # host process. The next bounded interval retries automatically.
            continue


def start(project_root: Path, agent_id: str) -> dict[str, Any]:
    if not agent_id:
        return {"ok": False, "verdict": "AGENT_ID_REQUIRED"}
    root = project_root.resolve()
    key = (str(root), agent_id)
    interval = _interval_seconds()
    with _GUARD:
        existing = _WORKERS.get(key)
        if existing and existing[0].is_alive():
            return {
                "ok": True,
                "verdict": "AGENT_PRESENCE_ALREADY_RUNNING",
                "agent_id": agent_id,
                "interval_seconds": interval,
            }
        try:
            db.heartbeat(agent_id, root)
        except Exception as exc:
            return {
                "ok": False,
                "verdict": "AGENT_PRESENCE_START_FAILED",
                "agent_id": agent_id,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        stop = threading.Event()
        thread = threading.Thread(
            target=_worker,
            args=(root, agent_id, stop, interval),
            name=f"tenor-presence-{agent_id[:24]}",
            daemon=True,
        )
        _WORKERS[key] = (thread, stop)
        thread.start()
    return {
        "ok": True,
        "verdict": "AGENT_PRESENCE_STARTED",
        "agent_id": agent_id,
        "interval_seconds": interval,
        "pid": os.getpid(),
    }


def stop(project_root: Path, agent_id: str, timeout_seconds: float = 2.0) -> dict[str, Any]:
    root = project_root.resolve()
    key = (str(root), agent_id)
    with _GUARD:
        worker = _WORKERS.pop(key, None)
    if not worker:
        return {"ok": True, "verdict": "AGENT_PRESENCE_ALREADY_STOPPED", "agent_id": agent_id}
    thread, stop_event = worker
    stop_event.set()
    thread.join(timeout=max(0.0, timeout_seconds))
    return {
        "ok": not thread.is_alive(),
        "verdict": "AGENT_PRESENCE_STOPPED" if not thread.is_alive() else "AGENT_PRESENCE_STOP_TIMEOUT",
        "agent_id": agent_id,
    }


def status(project_root: Path, agent_id: str) -> dict[str, Any]:
    key = (str(project_root.resolve()), agent_id)
    with _GUARD:
        worker = _WORKERS.get(key)
    return {
        "agent_id": agent_id,
        "running": bool(worker and worker[0].is_alive()),
        "pid": os.getpid(),
        "checked_at": int(time.time()),
    }
