#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scribe_output_paths import scribe_out_dir


DEFAULT_WORKFLOW_ACK_PATH = scribe_out_dir(Path.cwd()) / "workflow-acks.json"
WORKFLOW_ACK_PATH_ENV = "SCRIBE_WORKFLOW_ACK_PATH"
DEFAULT_REQUIRED_AGENTS: tuple[str, ...] = ()
DEFAULT_WORKFLOW_FILES = (
    Path("AGENTS.md"),
    Path(".agent/rules/scribe.md"),
    Path(".agent/skills/init-tenor/SKILL.md"),
    Path(".agent/workflow/scribe/README.md"),
    Path(".agent/workflow/scribe/rag/README.md"),
    Path(".agent/workflow/scribe/sel/docs/AGENTS.md"),
    Path(".agent/workflow/scribe/sel/docs/live-coordination.md"),
    Path(".agent/workflow/scribe/sel/docs/friction-policy.md"),
    Path(".agent/workflow/scribe/sel/docs/multi-agent-installation.md"),
    Path(".agent/workflow/scribe/sel/docs/scribe.md"),
)


@dataclass(frozen=True)
class WorkflowDigest:
    sha256: str
    files: list[dict[str, Any]]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def configured_workflow_ack_path() -> Path:
    override = os.environ.get(WORKFLOW_ACK_PATH_ENV)
    return Path(override) if override else DEFAULT_WORKFLOW_ACK_PATH


def read_workflow_acks(path: Path | None = None) -> dict[str, Any]:
    ack_path = path or configured_workflow_ack_path()
    if not ack_path.exists():
        return {"version": 1, "acks": {}}
    try:
        payload = json.loads(ack_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "acks": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "acks": {}}
    if not isinstance(payload.get("acks"), dict):
        payload["acks"] = {}
    payload.setdefault("version", 1)
    return payload


def write_workflow_acks(payload: dict[str, Any], path: Path | None = None) -> Path:
    ack_path = path or configured_workflow_ack_path()
    ack_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = ack_path.with_name(f".{ack_path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(ack_path)
    return ack_path


def existing_workflow_files(root: Path) -> list[tuple[Path, Path]]:
    result: list[tuple[Path, Path]] = []
    for relative in DEFAULT_WORKFLOW_FILES:
        absolute = root / relative
        if absolute.is_file():
            result.append((relative, absolute))
    return result


def compute_workflow_digest(root: Path | str = Path(".")) -> WorkflowDigest:
    base = Path(root)
    hasher = hashlib.sha256()
    files: list[dict[str, Any]] = []
    for relative, absolute in existing_workflow_files(base):
        data = absolute.read_bytes()
        file_sha = "sha256:" + hashlib.sha256(data).hexdigest()
        rel_text = relative.as_posix()
        hasher.update(rel_text.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(data)
        hasher.update(b"\0")
        files.append({"path": rel_text, "sha256": file_sha, "bytes": len(data)})
    if not files:
        hasher.update(b"SCRIBE_WORKFLOW_EMPTY")
    return WorkflowDigest("sha256:" + hasher.hexdigest(), files)


def normalize_agent_type(value: str) -> str:
    return value if value in {"extension", "cli", "api", "unknown"} else "unknown"


def record_workflow_ack(
    agent: str,
    agent_type: str,
    root: Path | str = Path("."),
    ack_path: Path | None = None,
) -> tuple[dict[str, Any], WorkflowDigest, Path]:
    digest = compute_workflow_digest(root)
    path = ack_path or configured_workflow_ack_path()
    payload = read_workflow_acks(path)
    payload["version"] = 1
    payload["workflow_sha256"] = digest.sha256
    payload["workflow_files"] = digest.files
    payload.setdefault("acks", {})[agent] = {
        "agent": agent,
        "type": normalize_agent_type(agent_type),
        "acknowledged_at": utc_now_iso(),
        "workflow_sha256": digest.sha256,
        "files": [item["path"] for item in digest.files],
    }
    write_workflow_acks(payload, path)
    return payload["acks"][agent], digest, path


def check_workflow_ack(
    agent: str,
    root: Path | str = Path("."),
    ack_path: Path | None = None,
) -> tuple[bool, str, dict[str, Any] | None, WorkflowDigest, Path]:
    digest = compute_workflow_digest(root)
    path = ack_path or configured_workflow_ack_path()
    payload = read_workflow_acks(path)
    ack = payload.get("acks", {}).get(agent) if isinstance(payload.get("acks"), dict) else None
    if not isinstance(ack, dict):
        return False, "ACK_REQUIRED", None, digest, path
    if ack.get("workflow_sha256") != digest.sha256:
        return False, "ACK_STALE", ack, digest, path
    return True, "ACK_OK", ack, digest, path


def parse_agent_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def workflow_status_lines(required_agents: list[str], root: Path | str = Path("."), ack_path: Path | None = None) -> tuple[bool, list[str]]:
    payload = read_workflow_acks(ack_path)
    digest = compute_workflow_digest(root)
    path = ack_path or configured_workflow_ack_path()
    lines = ["SCRIBE WORKFLOW STATUS", f"  ack_file: {path}", f"  workflow_sha256: {digest.sha256}"]
    all_ok = True
    if not required_agents:
        lines.append("  required_agents: none (agent-pool mode; use --required only for an explicit named gate)")
    for agent in required_agents:
        ok, verdict, ack, _, _ = check_workflow_ack(agent, root, path)
        all_ok = all_ok and ok
        ack_time = ack.get("acknowledged_at", "-") if isinstance(ack, dict) else "-"
        lines.append(f"  agent[{agent}]: {verdict} acknowledged_at={ack_time}")
    recorded = payload.get("acks", {}) if isinstance(payload.get("acks"), dict) else {}
    recorded_agents = sorted(recorded)
    if required_agents:
        extra = sorted(agent for agent in recorded_agents if agent not in required_agents)
        if extra:
            lines.append(f"  extra_agents: {', '.join(extra)}")
    elif recorded_agents:
        lines.append(f"  recorded_agents: {', '.join(recorded_agents)}")
    else:
        lines.append("  recorded_agents: none")
    verdict = "ALL_ACKED" if required_agents and all_ok else "ACKS_MISSING_OR_STALE" if required_agents else "POOL_STATUS_OK"
    lines.append(f"  verdict: {verdict}")
    return all_ok, lines


def workflow_read_lines(agent: str, agent_type: str, root: Path | str = Path(".")) -> tuple[dict[str, Any], list[str]]:
    ack, digest, path = record_workflow_ack(agent, agent_type, root)
    lines = [
        "SCRIBE WORKFLOW READ",
        f"  agent: {agent}",
        f"  type: {ack.get('type', 'unknown')}",
        f"  ack_file: {path}",
        f"  workflow_sha256: {digest.sha256}",
        "  required_files:",
    ]
    if digest.files:
        for item in digest.files:
            lines.append(f"  - {item['path']} {item['sha256']} bytes={item['bytes']}")
    else:
        lines.append("  - none found; run from the project root after bootstrap")
    lines.extend(
        [
            "  mandatory_before_shared_write:",
            "  - scribe whoami --type <type> --surface idle",
            "  - scribe workflow check --agent <name>",
            "  - scribe coordination status",
            "  - scribe coordination claim --agent <name> --claim <semantic-claim> --task \"<task>\" [--expected-file <path>]",
            "  - scribe-rag preflight --tier <tier> \"<plan>\"",
            "  - scribe-rag gate when the SCRIBE/RAG bundle changes",
            "  - scribe lock acquire --agent <name> --type <type> --session <JOURNAL-ID> [--surface <surface>]",
            "  verdict: ACK_RECORDED",
        ]
    )
    return ack, lines
