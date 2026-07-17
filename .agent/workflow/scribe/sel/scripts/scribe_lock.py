#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scribe_workflow_ack import check_workflow_ack
from scribe_output_paths import scribe_out_dir


DEFAULT_LOCK_PATH = scribe_out_dir(Path.cwd()) / "locks" / "scribe.lock"
DEFAULT_SURFACE = "scribe-memory"
DEFAULT_TTL_MINUTES = 30
LOCK_PATH_ENV = "SCRIBE_LOCK_PATH"
OWNER_PID_ENV = "SCRIBE_OWNER_PID"
IS_WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class LockState:
    agent: str
    agent_type: str
    session: str
    pid: int
    acquired_at: datetime
    surface: str
    ttl_minutes: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "LockState":
        return cls(
            agent=str(payload.get("owner_id") or payload.get("agent") or ""),
            agent_type=str(payload.get("agent_type") or "unknown"),
            session=str(payload.get("session") or ""),
            pid=int(payload.get("owner_pid") or payload.get("pid") or 0),
            acquired_at=parse_timestamp(str(payload.get("acquired_at") or "")),
            surface=str(payload.get("surface") or DEFAULT_SURFACE),
            ttl_minutes=int(payload.get("ttl_minutes") or DEFAULT_TTL_MINUTES),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "agent_type": self.agent_type,
            "owner_id": self.agent,
            "owner_pid": self.pid,
            "session": self.session,
            "pid": self.pid,
            "acquired_at": self.acquired_at.isoformat().replace("+00:00", "Z"),
            "surface": self.surface,
            "ttl_minutes": self.ttl_minutes,
        }


def configured_lock_path() -> Path:
    override = os.environ.get(LOCK_PATH_ENV)
    return Path(override) if override else DEFAULT_LOCK_PATH


def configured_owner_pid() -> int:
    override = os.environ.get(OWNER_PID_ENV)
    return int(override) if override else os.getpid()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.fromtimestamp(0, timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_lock(lock_path: Path | None = None) -> LockState | None:
    path = lock_path or configured_lock_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return LockState.from_payload(payload)
    except (OSError, ValueError, TypeError):
        return LockState("", "unknown", "", 0, datetime.fromtimestamp(0, timezone.utc), DEFAULT_SURFACE, 0)


def write_lock(state: LockState, lock_path: Path | None = None) -> None:
    path = lock_path or configured_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(state.to_payload(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def create_lock(state: LockState, lock_path: Path | None = None) -> bool:
    path = lock_path or configured_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(state.to_payload(), handle, indent=2, sort_keys=True)
        handle.write("\n")
    return True


def remove_lock(lock_path: Path | None = None) -> None:
    path = lock_path or configured_lock_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _windows_pid_exists(pid: int) -> bool:
    """Probe a Windows PID without sending a console control event."""

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
        # An uninspectable process may still own the lock. Failing closed is
        # safer than stealing ownership, and unlike os.kill(pid, 0) this path
        # can never broadcast CTRL_C_EVENT on Windows.
        return True


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        return _windows_pid_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def stale_reason(state: LockState, now: datetime | None = None) -> str | None:
    current = now or utc_now()
    if not state.agent or not state.session:
        return "malformed lock payload"
    if not pid_exists(state.pid):
        return f"pid {state.pid} is not running"
    age_minutes = (current - state.acquired_at).total_seconds() / 60
    if age_minutes > state.ttl_minutes:
        return f"ttl expired after {age_minutes:.1f} minute(s)"
    return None


def active_lock(lock_path: Path | None = None, surface: str = DEFAULT_SURFACE) -> tuple[LockState | None, str | None]:
    state = read_lock(lock_path)
    if state is None:
        return None, None
    reason = stale_reason(state)
    if reason:
        remove_lock(lock_path)
        return None, reason
    if state.surface != surface:
        return None, f"active lock belongs to surface {state.surface}"
    return state, None


def require_active_lock(surface: str = DEFAULT_SURFACE, lock_path: Path | None = None) -> tuple[bool, str]:
    state, reason = active_lock(lock_path, surface)
    if state is None:
        if reason:
            return False, f"no active {surface} lock; stale lock released ({reason})"
        return False, f"no active {surface} lock"
    return True, f"lock held by {state.agent} for {state.session}"


def mutation_lock_guard(surface: str = DEFAULT_SURFACE) -> int:
    ok, message = require_active_lock(surface)
    if ok:
        return 0
    print(f"SCRIBE LOCK: mutation refused: {message}", file=sys.stderr)
    print("  run: scribe lock acquire --agent <name> --session <JOURNAL-ID>", file=sys.stderr)
    return 2


def acquire_lock(
    agent: str,
    session: str,
    agent_type: str = "unknown",
    surface: str = DEFAULT_SURFACE,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
    owner_pid: int | None = None,
) -> tuple[bool, str]:
    if ttl_minutes <= 0:
        return False, "ttl_minutes must be positive"
    current, reason = active_lock(surface=surface)
    if current is not None:
        return False, f"already locked by {current.agent} for {current.session}"
    state = LockState(agent, agent_type, session, owner_pid or configured_owner_pid(), utc_now(), surface, ttl_minutes)
    if not create_lock(state):
        current, _ = active_lock(surface=surface)
        owner = f" by {current.agent} for {current.session}" if current else ""
        return False, f"lock changed during acquire{owner}"
    if reason:
        return True, f"acquired after releasing stale lock ({reason})"
    return True, "acquired"


def release_lock(agent: str | None = None, surface: str = DEFAULT_SURFACE) -> tuple[bool, str]:
    current = read_lock()
    if current is None:
        return True, "already unlocked"
    if current.surface != surface:
        return False, f"lock belongs to surface {current.surface}, not {surface}"
    if agent and current.agent != agent:
        return False, f"lock held by {current.agent}, not {agent}"
    reason = stale_reason(current)
    remove_lock()
    if reason:
        return True, f"released stale lock ({reason})"
    return True, "released"


def cmd_acquire(args: argparse.Namespace) -> int:
    if not args.no_workflow_check:
        ack_ok, verdict, _, _, _ = check_workflow_ack(args.agent)
        if not ack_ok:
            print(f"SCRIBE LOCK: mutation refused: workflow {verdict} for {args.agent}", file=sys.stderr)
            print(f"  run: scribe workflow read --agent {args.agent} --type {args.agent_type}", file=sys.stderr)
            return 2
    ok, message = acquire_lock(args.agent, args.session, args.agent_type, args.surface, args.ttl_minutes, args.owner_pid)
    print(f"SCRIBE LOCK: {message}")
    if ok:
        print(f"  file: {configured_lock_path()}")
        print(f"  agent: {args.agent}")
        print(f"  owner_id: {args.agent}")
        print(f"  agent_type: {args.agent_type}")
        print(f"  session: {args.session}")
        print(f"  surface: {args.surface}")
        print(f"  owner_pid: {args.owner_pid or configured_owner_pid()}")
    return 0 if ok else 2


def cmd_release(args: argparse.Namespace) -> int:
    ok, message = release_lock(args.agent, args.surface)
    print(f"SCRIBE LOCK: {message}")
    return 0 if ok else 2


def cmd_status(args: argparse.Namespace) -> int:
    state, reason = active_lock(surface=args.surface)
    print("SCRIBE LOCK STATUS")
    print(f"  file: {configured_lock_path()}")
    if state is None:
        print("  state: unlocked")
        if reason:
            print(f"  stale_released: {reason}")
        return 0
    print("  state: locked")
    print(f"  agent: {state.agent}")
    print(f"  owner_id: {state.agent}")
    print(f"  agent_type: {state.agent_type}")
    print(f"  session: {state.session}")
    print(f"  pid: {state.pid}")
    print(f"  owner_pid: {state.pid}")
    print(f"  acquired_at: {state.acquired_at.isoformat().replace('+00:00', 'Z')}")
    print(f"  surface: {state.surface}")
    print(f"  ttl_minutes: {state.ttl_minutes}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scribe lock", description="Coordinate SCRIBE mutations with a local JSON lock.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    acquire = subparsers.add_parser("acquire", help="Acquire the SCRIBE mutation lock.")
    acquire.add_argument("--agent", required=True, help="Agent name holding the lock.")
    acquire.add_argument("--type", dest="agent_type", default="unknown", choices=("api", "cli", "extension", "unknown"))
    acquire.add_argument("--session", required=True, help="JOURNAL-ID tied to this mutation session.")
    acquire.add_argument("--surface", default=DEFAULT_SURFACE)
    acquire.add_argument("--ttl-minutes", type=int, default=DEFAULT_TTL_MINUTES)
    acquire.add_argument("--owner-pid", type=int, help="Long-lived process PID that owns this lock. Defaults to the current process.")
    acquire.add_argument("--no-workflow-check", action="store_true", help="Emergency bypass for bootstrapping tests only.")
    acquire.set_defaults(func=cmd_acquire)

    release = subparsers.add_parser("release", help="Release the SCRIBE mutation lock.")
    release.add_argument("--agent", help="Refuse release if another agent owns the active lock.")
    release.add_argument("--surface", default=DEFAULT_SURFACE)
    release.set_defaults(func=cmd_release)

    status = subparsers.add_parser("status", help="Show the SCRIBE mutation lock status.")
    status.add_argument("--surface", default=DEFAULT_SURFACE)
    status.set_defaults(func=cmd_status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
