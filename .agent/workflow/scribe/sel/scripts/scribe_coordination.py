#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_COORDINATION_DIR = Path("scribe-out") / "coordination"
COORDINATION_DIR_ENV = "SCRIBE_COORDINATION_DIR"
DEFAULT_CLAIM_TTL_SECONDS = 1800


def configured_coordination_dir() -> Path:
    override = os.environ.get(COORDINATION_DIR_ENV)
    return Path(override) if override else DEFAULT_COORDINATION_DIR


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso(value: datetime | None = None) -> str:
    current = value or utc_now()
    return current.isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def claim_id(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    cleaned = "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")
    return f"{cleaned[:48]}-{digest}" if cleaned else digest


def claims_dir(root: Path | None = None) -> Path:
    return (root or configured_coordination_dir()) / "claims"


def events_path(root: Path | None = None) -> Path:
    return (root or configured_coordination_dir()) / "events.jsonl"


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def append_event(payload: dict[str, Any], root: Path | None = None) -> None:
    path = events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def claim_entries(root: Path | None = None) -> list[tuple[Path, dict[str, Any]]]:
    base = root or configured_coordination_dir()
    paths = list(claims_dir(root).glob("*.json")) if claims_dir(root).exists() else []
    if base.exists():
        paths.extend(path for path in base.glob("*.json") if path.name != "events.json")
    entries: list[tuple[Path, dict[str, Any]]] = []
    seen: set[Path] = set()
    for path in sorted(paths):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        payload = read_json(path)
        if payload and payload.get("status") == "working":
            entries.append((path, payload))
    return entries


def is_claim_stale(claim: dict[str, Any], now: datetime | None = None) -> bool:
    expires_at = claim.get("expires_at")
    if not expires_at:
        return True
    parsed = parse_timestamp(str(expires_at))
    if parsed is None:
        return True
    return (now or utc_now()) > parsed


def cleanup_stale_claims(entries: list[tuple[Path, dict[str, Any]]], root: Path | None = None) -> list[str]:
    cleaned: list[str] = []
    for path, claim in entries:
        claim_name = str(claim.get("semantic_claim") or claim.get("claim_id") or path.stem)
        path.unlink(missing_ok=True)
        cleaned.append(claim_name)
        append_event({"type": "claim_stale_cleaned", "semantic_claim": claim_name, "agent": claim.get("agent"), "cleaned_at": utc_now_iso()}, root)
    return cleaned


def active_claims_with_cleanup(root: Path | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    active: list[dict[str, Any]] = []
    stale: list[tuple[Path, dict[str, Any]]] = []
    current = utc_now()
    for path, payload in claim_entries(root):
        if is_claim_stale(payload, current):
            stale.append((path, payload))
        else:
            active.append(payload)
    return active, cleanup_stale_claims(stale, root)


def active_claims(root: Path | None = None) -> list[dict[str, Any]]:
    claims, _ = active_claims_with_cleanup(root)
    return claims


def expected_files(payload: dict[str, Any]) -> set[str]:
    files = payload.get("expected_files")
    return {str(item) for item in files} if isinstance(files, list) else set()


def find_conflicts(agent: str, semantic_claim: str, files: set[str]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    semantic_conflicts: list[dict[str, Any]] = []
    shared_files: dict[str, list[str]] = {}
    for claim in active_claims():
        if claim.get("agent") == agent:
            continue
        if claim.get("semantic_claim") == semantic_claim:
            semantic_conflicts.append(claim)
        overlap = sorted(files & expected_files(claim))
        if overlap:
            shared_files[str(claim.get("semantic_claim") or claim.get("claim_id"))] = overlap
    return semantic_conflicts, shared_files


def cmd_claim(args: argparse.Namespace) -> int:
    files = set(args.expected_file or [])
    semantic_conflicts, shared_files = find_conflicts(args.agent, args.claim, files)
    if semantic_conflicts:
        owners = ", ".join(str(item.get("agent")) for item in semantic_conflicts)
        print(f"SCRIBE COORDINATION: semantic claim already active: {args.claim}")
        print(f"  owners: {owners}")
        return 2

    now_dt = utc_now()
    now = utc_now_iso(now_dt)
    expires_at = utc_now_iso(now_dt + timedelta(seconds=DEFAULT_CLAIM_TTL_SECONDS))
    payload = {
        "agent": args.agent,
        "claim_id": claim_id(args.claim),
        "semantic_claim": args.claim,
        "task": args.task,
        "expected_files": sorted(files),
        "status": "working",
        "started_at": now,
        "last_update": now,
        "ttl_seconds": DEFAULT_CLAIM_TTL_SECONDS,
        "expires_at": expires_at,
    }
    write_json_atomic(claims_dir() / f"{payload['claim_id']}.json", payload)
    append_event({"type": "claim_started", **payload})
    print("SCRIBE COORDINATION: claim acquired")
    print(f"  agent: {args.agent}")
    print(f"  semantic_claim: {args.claim}")
    print(f"  expected_files: {len(files)}")
    print(f"  expires_at: {expires_at}")
    if shared_files:
        print("  shared_files_detected: yes")
        for claim, overlap in shared_files.items():
            print(f"  - {claim}: {', '.join(overlap)}")
        print("  required: re-read current files and rebase before delivery")
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    path = claims_dir() / f"{claim_id(args.claim)}.json"
    payload = read_json(path)
    if not payload:
        print(f"SCRIBE COORDINATION: no active claim found for {args.claim}")
        return 2
    if payload.get("agent") != args.agent:
        print(f"SCRIBE COORDINATION: claim owned by {payload.get('agent')}, not {args.agent}")
        return 2
    payload["status"] = "done"
    payload["finished_at"] = utc_now_iso()
    payload["changed_files"] = sorted(set(args.changed_file or []))
    payload["summary"] = args.summary
    write_json_atomic(path, payload)
    append_event({"type": "claim_finished", **payload})
    print("SCRIBE COORDINATION: claim finished")
    print(f"  semantic_claim: {args.claim}")
    print(f"  changed_files: {len(payload['changed_files'])}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    claims, cleaned = active_claims_with_cleanup()
    print("SCRIBE COORDINATION STATUS")
    print(f"  root: {configured_coordination_dir()}")
    if cleaned:
        print(f"  stale_claims_cleaned: {len(cleaned)}")
        for claim_name in cleaned:
            print(f"  cleaned stale claim: {claim_name}")
    print(f"  active_claims: {len(claims)}")
    for claim in claims[: args.limit]:
        files = expected_files(claim)
        print(f"  - {claim.get('semantic_claim')} agent={claim.get('agent')} files={len(files)} expires_at={claim.get('expires_at')}")
    if len(claims) > args.limit:
        print(f"  ... {len(claims) - args.limit} more")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scribe coordination", description="Coordinate semantic claims between agents.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    claim = subparsers.add_parser("claim", help="Acquire a semantic work claim.")
    claim.add_argument("--agent", required=True)
    claim.add_argument("--claim", required=True, help="Semantic claim, e.g. indicator:RSI-14.")
    claim.add_argument("--task", required=True)
    claim.add_argument("--expected-file", "--files", dest="expected_file", action="append", default=[])
    claim.set_defaults(func=cmd_claim)

    finish = subparsers.add_parser("finish", help="Mark a semantic claim as finished.")
    finish.add_argument("--agent", required=True)
    finish.add_argument("--claim", required=True)
    finish.add_argument("--summary", required=True)
    finish.add_argument("--changed-file", action="append", default=[])
    finish.set_defaults(func=cmd_finish)

    status = subparsers.add_parser("status", help="Show active semantic claims.")
    status.add_argument("--limit", type=int, default=20)
    status.set_defaults(func=cmd_status)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
