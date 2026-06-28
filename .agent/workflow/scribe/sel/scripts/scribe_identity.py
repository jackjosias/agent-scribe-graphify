#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scribe_lock import configured_lock_path, read_lock, remove_lock, stale_reason
from scribe_state import DEFAULT_SCRIBE_PATH, check_sync, print_state_summary
from scribe_output_paths import scribe_out_dir


DEFAULT_PRESENCE_DIR = scribe_out_dir(Path.cwd()) / "presence"
PRESENCE_DIR_ENV = "SCRIBE_PRESENCE_DIR"
AGENT_ID_ENV = "SCRIBE_AGENT_ID"
DEFAULT_TTL_SECONDS = 120
NON_EXCLUSIVE_SURFACES = {"", "idle", "none", "unknown"}


@dataclass(frozen=True)
class Presence:
    agent_id: str
    agent_type: str
    surface: str
    pid: int
    started_at: datetime
    last_heartbeat: datetime
    ttl_seconds: int
    status: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Presence":
        return cls(
            agent_id=str(payload.get("agent_id") or payload.get("agent") or ""),
            agent_type=str(payload.get("agent_type") or payload.get("type") or "unknown"),
            surface=str(payload.get("surface") or "unknown"),
            pid=parse_pid(payload.get("pid")),
            started_at=parse_timestamp(str(payload.get("started_at") or "")),
            last_heartbeat=parse_timestamp(str(payload.get("last_heartbeat") or payload.get("last_seen") or "")),
            ttl_seconds=int(payload.get("ttl_seconds") or DEFAULT_TTL_SECONDS),
            status=str(payload.get("status") or "active"),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "surface": self.surface,
            "pid": self.pid,
            "started_at": isoformat(self.started_at),
            "last_heartbeat": isoformat(self.last_heartbeat),
            "ttl_seconds": self.ttl_seconds,
            "status": self.status,
        }


def generate_agent_id(agent_type: str) -> str:
    date = time.strftime("%Y%m%d")
    raw = f"{date}-{os.getpid()}-{socket.gethostname()}-{time.time_ns()}"
    uid = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{normalize_agent_type(agent_type)}-{date}-{uid}"


def normalize_agent_type(value: str) -> str:
    cleaned = "".join(char for char in value.lower() if char.isalnum() or char == "-").strip("-")
    return cleaned or "agent"


def configured_presence_dir() -> Path:
    override = os.environ.get(PRESENCE_DIR_ENV)
    return Path(override) if override else DEFAULT_PRESENCE_DIR


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_pid(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def presence_path(agent_id: str, root: Path | None = None) -> Path:
    return (root or configured_presence_dir()) / f"{agent_id}.json"


def read_presence(path: Path) -> Presence | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return Presence.from_payload(payload) if isinstance(payload, dict) else None


def write_presence(
    agent_id: str,
    agent_type: str,
    surface: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    pid: int | None = None,
    status: str = "active",
) -> Presence:
    now = utc_now()
    path = presence_path(agent_id)
    current = read_presence(path) if path.exists() else None
    started_at = current.started_at if current else now
    presence = Presence(agent_id, agent_type, surface, pid or os.getpid(), started_at, now, ttl_seconds, status)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(presence.to_payload(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)
    return presence


def stale_presence_reason(presence: Presence, now: datetime | None = None) -> str | None:
    current = now or utc_now()
    if not presence.agent_id:
        return "malformed presence payload"
    if presence.status == "closed":
        return "agent closed"
    if not pid_exists(presence.pid):
        return f"pid {presence.pid} is not running"
    age_seconds = (current - presence.last_heartbeat).total_seconds()
    if age_seconds > presence.ttl_seconds:
        return f"ttl expired after {age_seconds:.1f} second(s)"
    return None


def active_presences(root: Path | None = None) -> tuple[list[Presence], list[str]]:
    directory = root or configured_presence_dir()
    active: list[Presence] = []
    removed: list[str] = []
    if not directory.exists():
        return active, removed
    for path in sorted(directory.glob("*.json")):
        presence = read_presence(path)
        reason = stale_presence_reason(presence) if presence else "unreadable presence payload"
        if reason:
            path.unlink(missing_ok=True)
            removed_id = presence.agent_id if presence else path.stem
            removed.append(f"{removed_id}: {reason}")
        elif presence:
            active.append(presence)
    return active, removed


def surface_conflicts(surface: str, agent_id: str, root: Path | None = None) -> list[Presence]:
    if surface in NON_EXCLUSIVE_SURFACES:
        return []
    active, _ = active_presences(root)
    return [item for item in active if item.surface == surface and item.agent_id != agent_id]


def cmd_whoami(args: argparse.Namespace) -> int:
    agent_id = args.agent or os.environ.get(AGENT_ID_ENV) or generate_agent_id(args.agent_type)
    if not args.no_register:
        write_presence(agent_id, args.agent_type, args.surface, args.ttl_seconds, status=args.status)
    active, removed = active_presences()
    conflicts = surface_conflicts(args.surface, agent_id)
    lock_state = read_lock()
    lock_reason = stale_reason(lock_state) if lock_state else None
    if lock_state and lock_reason:
        remove_lock()
        lock_state = None

    print("SCRIBE WHOAMI")
    print(f"  Mon ID: {agent_id}")
    print(f"  Ma surface: {args.surface}")
    print(f"  Presence dir: {configured_presence_dir()}")
    print(f"  Agents actifs: {len(active)}")
    for item in active:
        print(f"  - {item.agent_id} -> {item.surface} pid={item.pid} heartbeat={isoformat(item.last_heartbeat)}")
    if removed:
        print(f"  Stale nettoyes: {len(removed)}")
        for removed_entry in removed:
            removed_name = removed_entry.split(":", 1)[0].removesuffix(".json")
            print(f"  cleaned stale presence: {removed_name}")
    if conflicts:
        print(f"  Surface conflict: {args.surface} already claimed by {', '.join(item.agent_id for item in conflicts)}")
    if lock_state:
        print(f"  Lock status: owned by {lock_state.agent} surface={lock_state.surface} pid={lock_state.pid}")
    else:
        print(f"  Lock status: unlocked")
        if lock_reason:
            print(f"  Stale lock released: {lock_reason}")
    print_state_summary(check_sync(Path(args.scribe)))
    return 2 if conflicts else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scribe whoami", description="Show agent identity, live presence, and SCRIBE state.")
    parser.add_argument("--agent", help="Stable session agent id. Defaults to SCRIBE_AGENT_ID or a generated id.")
    parser.add_argument("--type", dest="agent_type", default="cli", choices=("api", "cli", "extension", "unknown"))
    parser.add_argument("--surface", default="idle")
    parser.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    parser.add_argument("--status", default="idle")
    parser.add_argument("--no-register", action="store_true", help="Do not write/update this agent presence heartbeat.")
    parser.add_argument("--scribe", default=str(DEFAULT_SCRIBE_PATH))
    return parser


def main() -> int:
    return cmd_whoami(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
