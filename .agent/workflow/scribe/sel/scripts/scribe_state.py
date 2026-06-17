#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scribe_doctor_model import parse_yaml
from scribe_lock import DEFAULT_SURFACE, active_lock
from scribe_clean import print_scribe_noise_warning
from scribe_workflow_ack import (
    check_workflow_ack,
    parse_agent_list,
    workflow_read_lines,
    workflow_status_lines,
)


DEFAULT_SCRIBE_PATH = Path("AGENT-MEMOIRE_PROJECT_STATUS.scribe")
DEFAULT_STATE_PATH = Path("scribe-out") / "state.json"
STATE_PATH_ENV = "SCRIBE_STATE_PATH"
WRITE_KINDS = {
    "memory_append",
    "journal_close",
    "tier_rebalance",
    "status_mutation",
    "archive_apply",
    "repair",
    "install",
}
AGENT_TYPES = {"extension", "cli", "api", "unknown"}


@dataclass(frozen=True)
class ScribeSnapshot:
    sha256: str
    line_count: int
    last_journal_id: str | None


@dataclass(frozen=True)
class SyncCheck:
    state: dict[str, Any] | None
    snapshot: ScribeSnapshot
    state_path: Path
    missing: bool
    hash_mismatch: bool
    invalid_write_kind: bool

    @property
    def ok(self) -> bool:
        return not (self.missing or self.hash_mismatch or self.invalid_write_kind)


def state_path_for_scribe(scribe_path: Path) -> Path:
    override = os.environ.get(STATE_PATH_ENV)
    if override:
        return Path(override)
    if scribe_path.is_absolute():
        return scribe_path.parent / "scribe-out" / "state.json"
    return DEFAULT_STATE_PATH


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_state(state_path: Path) -> dict[str, Any] | None:
    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def write_state_atomic(state_path: Path, payload: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_name(f".{state_path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(state_path)


def snapshot_scribe(scribe_path: Path) -> ScribeSnapshot:
    content = scribe_path.read_bytes()
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    raw = content.decode("utf-8")
    data, _ = parse_yaml(raw, scribe_path)
    return ScribeSnapshot(digest, len(raw.splitlines()), last_journal_id(data if isinstance(data, dict) else {}))


def last_journal_id(data: dict[str, Any]) -> str | None:
    journal = data.get("journal")
    if not isinstance(journal, list):
        return None
    for item in reversed(journal):
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            return item["id"]
    return None


def build_state_payload(
    scribe_path: Path,
    writer_agent: str,
    writer_type: str,
    session: str,
    changed_ids: list[str],
    write_kind: str,
) -> dict[str, Any]:
    snapshot = snapshot_scribe(scribe_path)
    return {
        "version": 1,
        "scribe_path": str(scribe_path),
        "scribe_sha256": snapshot.sha256,
        "scribe_line_count": snapshot.line_count,
        "last_write_at": utc_now_iso(),
        "writer": {"agent": writer_agent, "type": normalize_agent_type(writer_type)},
        "last_session": session,
        "last_journal_id": snapshot.last_journal_id,
        "changed_ids": sorted(dedupe(changed_ids)),
        "write_kind": normalize_write_kind(write_kind),
    }


def update_state_after_write(
    scribe_path: Path,
    writer_agent: str,
    writer_type: str,
    session: str,
    changed_ids: list[str],
    write_kind: str,
) -> Path:
    state_path = state_path_for_scribe(scribe_path)
    payload = build_state_payload(scribe_path, writer_agent, writer_type, session, changed_ids, write_kind)
    write_state_atomic(state_path, payload)
    return state_path


def update_state_from_active_lock(scribe_path: Path, changed_ids: list[str], write_kind: str) -> Path:
    state, _ = active_lock(surface=DEFAULT_SURFACE)
    writer_agent = state.agent if state is not None else "unknown"
    writer_type = getattr(state, "agent_type", "unknown") if state is not None else "unknown"
    session = state.session if state is not None else ""
    return update_state_after_write(scribe_path, writer_agent, writer_type, session, changed_ids, write_kind)


def check_sync(scribe_path: Path, state_path: Path | None = None) -> SyncCheck:
    snapshot = snapshot_scribe(scribe_path)
    path = state_path or state_path_for_scribe(scribe_path)
    state = read_state(path)
    if state is None:
        return SyncCheck(state, snapshot, path, True, False, False)
    state_hash = str(state.get("scribe_sha256") or "")
    write_kind = str(state.get("write_kind") or "")
    return SyncCheck(state, snapshot, path, False, state_hash != snapshot.sha256, write_kind not in WRITE_KINDS)


def normalize_agent_type(value: str) -> str:
    return value if value in AGENT_TYPES else "unknown"


def normalize_write_kind(value: str) -> str:
    if value not in WRITE_KINDS:
        raise ValueError(f"Unsupported write_kind: {value}")
    return value


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def print_state_summary(check: SyncCheck) -> None:
    print(f"  state_file: {check.state_path}")
    print(f"  scribe_sha256: {check.snapshot.sha256}")
    print(f"  scribe_line_count: {check.snapshot.line_count}")
    if not check.state:
        return
    writer = check.state.get("writer") if isinstance(check.state.get("writer"), dict) else {}
    print(f"  last_writer: {writer.get('agent', '-')}")
    print(f"  last_writer_type: {writer.get('type', '-')}")
    print(f"  last_session: {check.state.get('last_session', '-')}")
    print(f"  last_journal_id: {check.state.get('last_journal_id', '-')}")
    print(f"  changed_ids: {', '.join(check.state.get('changed_ids', []) or []) or '-'}")
    print(f"  write_kind: {check.state.get('write_kind', '-')}")


def cmd_sync(args: argparse.Namespace) -> int:
    scribe_path = Path(args.scribe)
    if args.repair:
        update_state_after_write(scribe_path, args.agent, args.agent_type, args.session, args.changed_ids, args.write_kind)
    check = check_sync(scribe_path)
    print("SCRIBE SYNC")
    print(f"  agent: {args.agent}")
    print(f"  type: {args.agent_type}")
    print_state_summary(check)
    if check.ok:
        print("  verdict: IN_SYNC")
        if args.repair:
            print_scribe_noise_warning(scribe_path.resolve().parent)
        return 0
    if check.missing:
        print("  verdict: STALE_STATE_MISSING")
        print("  repair: scribe sync --repair --agent <name> --type <type> --session <JOURNAL-ID>")
    elif check.hash_mismatch:
        print("  verdict: STALE_HASH")
        print("  action: relire le SCRIBE ou le delta avant de continuer.")
    else:
        print("  verdict: INVALID_WRITE_KIND")
        print(f"  allowed: {', '.join(sorted(WRITE_KINDS))}")
    return 1


def cmd_whoami(args: argparse.Namespace) -> int:
    check = check_sync(Path(args.scribe))
    print("SCRIBE WHOAMI")
    print_state_summary(check)
    return 0 if check.ok else 1



def cmd_workflow_read(args: argparse.Namespace) -> int:
    _, lines = workflow_read_lines(args.agent, args.agent_type, Path(args.root))
    print("\n".join(lines))
    return 0


def cmd_workflow_check(args: argparse.Namespace) -> int:
    ok, verdict, ack, digest, path = check_workflow_ack(args.agent, Path(args.root))
    print("SCRIBE WORKFLOW CHECK")
    print(f"  agent: {args.agent}")
    print(f"  ack_file: {path}")
    print(f"  workflow_sha256: {digest.sha256}")
    if ack:
        print(f"  acknowledged_at: {ack.get('acknowledged_at', '-')}")
    print(f"  verdict: {verdict}")
    if not ok:
        print(f"  run: scribe workflow read --agent {args.agent} --type <extension|cli|api|unknown>")
    return 0 if ok else 2


def cmd_workflow_status(args: argparse.Namespace) -> int:
    required = parse_agent_list(args.required)
    ok, lines = workflow_status_lines(required, Path(args.root))
    print("\n".join(lines))
    return 0 if ok or not args.strict else 2

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scribe state", description="Synchronize agents through scribe-out/state.json.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync = subparsers.add_parser("sync", help="Check or repair the shared SCRIBE state file.")
    sync.add_argument("--scribe", default=str(DEFAULT_SCRIBE_PATH))
    sync.add_argument("--agent", required=True)
    sync.add_argument("--type", dest="agent_type", default="unknown", choices=sorted(AGENT_TYPES))
    sync.add_argument("--repair", action="store_true")
    sync.add_argument("--session", default="", help="Session/JOURNAL-ID used when repairing state.")
    sync.add_argument("--changed-id", dest="changed_ids", action="append", default=[])
    sync.add_argument("--write-kind", default="repair", choices=sorted(WRITE_KINDS))
    sync.set_defaults(func=cmd_sync)

    whoami = subparsers.add_parser("whoami", help="Show the last SCRIBE writer recorded in state.json.")
    whoami.add_argument("--scribe", default=str(DEFAULT_SCRIBE_PATH))
    whoami.set_defaults(func=cmd_whoami)

    workflow = subparsers.add_parser("workflow", help="Record and verify agent workflow read acknowledgements.")
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)

    workflow_read = workflow_sub.add_parser("read", help="Read and acknowledge the current SCRIBE workflow digest.")
    workflow_read.add_argument("--agent", required=True)
    workflow_read.add_argument("--type", dest="agent_type", default="unknown", choices=sorted(AGENT_TYPES))
    workflow_read.add_argument("--root", default=".")
    workflow_read.set_defaults(func=cmd_workflow_read)

    workflow_check = workflow_sub.add_parser("check", help="Require a fresh workflow acknowledgement for one agent.")
    workflow_check.add_argument("--agent", required=True)
    workflow_check.add_argument("--root", default=".")
    workflow_check.set_defaults(func=cmd_workflow_check)

    workflow_status = workflow_sub.add_parser("status", help="Show workflow acknowledgements for the agent pool or an explicit named gate.")
    workflow_status.add_argument("--required", default=None)
    workflow_status.add_argument("--root", default=".")
    workflow_status.add_argument("--strict", action="store_true")
    workflow_status.set_defaults(func=cmd_workflow_status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "repair", False) and not args.session:
        print("scribe sync --repair requires --session <JOURNAL-ID>", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
