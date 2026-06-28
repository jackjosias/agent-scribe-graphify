#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from scribe_bootstrap import bootstrap_project, print_report
from scribe_coordination import active_claims_with_cleanup
from scribe_identity import DEFAULT_TTL_SECONDS, write_presence
from scribe_lock import DEFAULT_SURFACE, active_lock, remove_lock, stale_reason
from scribe_state import AGENT_TYPES, check_sync
from scribe_output_paths import graphify_out_dir, migrate_all_legacy_outputs

# Proof signer — graceful fallback if module not yet installed
try:
    import sys as _sys
    _SELF_DIR = Path(__file__).parent
    if str(_SELF_DIR) not in _sys.path:
        _sys.path.insert(0, str(_SELF_DIR))
    from proof_signer import issue_proof as _issue_proof  # type: ignore
    _PROOF_SIGNER_OK = True
except Exception:  # noqa: BLE001
    _PROOF_SIGNER_OK = False
    def _issue_proof(*_a, **_kw) -> str:  # type: ignore
        return "PROOF_SIGNER_UNAVAILABLE"


PROJECT_SCRIBE = Path("AGENT-MEMOIRE_PROJECT_STATUS.scribe")
SKILL_PATH = Path(".agent") / "skills" / "init-tenor" / "SKILL.md"
GRAPH_REPORT_PATH = graphify_out_dir(Path.cwd()) / "GRAPH_REPORT.md"
RAG_COMMAND = Path(".agent") / "workflow" / "scribe" / "scribe-rag"


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_command(args: Sequence[str], cwd: Path, timeout: int = 30) -> CommandResult:
    try:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return CommandResult(tuple(args), 127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(tuple(args), 124, stdout, stderr or f"timeout after {timeout}s")
    return CommandResult(tuple(args), completed.returncode, completed.stdout, completed.stderr)


def first_nonempty_line(text: str, fallback: str = "-") -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return fallback



def first_result_line(text: str, prefixes: Sequence[str] = ("- ", "[")) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(stripped.startswith(prefix) for prefix in prefixes):
            return stripped
    return first_nonempty_line(text)

def limited_lines(text: str, limit: int = 12) -> list[str]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) <= limit:
        return lines
    return lines[:limit] + [f"... {len(lines) - limit} ligne(s) masquee(s)"]


def parse_graph_report(path: Path) -> dict[str, str]:
    if not path.exists():
        return {
            "status": "ABSENT",
            "nodes": "0",
            "edges": "0",
            "communities": "0",
            "god_nodes": "Aucun",
            "blast_radius": "Aucun",
        }
    text = path.read_text(encoding="utf-8", errors="replace")
    nodes = find_first(text, (r"Nodes\s*:\s*([0-9]+)", r"([0-9]+)\s+nodes?", r"([0-9]+)\s+n.uds?"), "unknown")
    edges = find_first(text, (r"Edges\s*:\s*([0-9]+)", r"([0-9]+)\s+edges?", r"([0-9]+)\s+ar.tes?"), "unknown")
    communities = find_first(
        text,
        (r"Communities\s*:\s*([0-9]+)", r"([0-9]+)\s+communities?", r"([0-9]+)\s+communaut"),
        "unknown",
    )
    god_nodes = extract_god_nodes(text)
    return {
        "status": "LU",
        "nodes": nodes,
        "edges": edges,
        "communities": communities,
        "god_nodes": ", ".join(god_nodes) if god_nodes else "Non detectes",
        "blast_radius": god_nodes[0] if god_nodes else "Non detecte",
    }


def find_first(text: str, patterns: Sequence[str], fallback: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return fallback


def extract_god_nodes(text: str) -> list[str]:
    results: list[str] = []
    capture = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if re.search(r"god[- ]?nodes?|highest degree|central", line, re.IGNORECASE):
            capture = True
            continue
        if capture and line.startswith("#"):
            break
        if not capture:
            continue
        candidates = re.findall(r"`([^`]+)`", line)
        if not candidates:
            candidates = re.findall(r"[-*]\s*([A-Za-z_$][\w$]*(?:\(\))?)", line)
        for candidate in candidates:
            cleaned = candidate.strip()
            if cleaned and cleaned not in results:
                results.append(cleaned)
            if len(results) >= 5:
                return results
    if not results:
        for candidate in re.findall(r"`([A-Za-z_$][\w$]*(?:\(\))?)`", text):
            if candidate not in results:
                results.append(candidate)
            if len(results) >= 5:
                break
    return results


def state_summary(project_root: Path) -> dict[str, str]:
    check = check_sync(project_root / PROJECT_SCRIBE)
    state = check.state if isinstance(check.state, dict) else {}
    writer = state.get("writer") if isinstance(state.get("writer"), dict) else {}
    return {
        "line_count": str(check.snapshot.line_count),
        "last_writer": str(writer.get("agent") or "-"),
        "last_writer_type": str(writer.get("type") or "-"),
        "last_journal_id": str(state.get("last_journal_id") or check.snapshot.last_journal_id or "-"),
        "sync": "IN_SYNC" if check.ok else "STALE",
    }


def lock_summary() -> str:
    lock_state, _ = active_lock(surface=DEFAULT_SURFACE)
    if not lock_state:
        return "unlocked"
    reason = stale_reason(lock_state)
    if reason:
        remove_lock()
        return f"unlocked (stale lock released: {reason})"
    return f"locked by {lock_state.agent} surface={lock_state.surface}"


def command_block(title: str, result: CommandResult) -> list[str]:
    lines = [f"{title}: rc={result.returncode} :: {' '.join(result.args)}"]
    for line in limited_lines(result.stdout, 10):
        lines.append(f"  OUT {line}")
    for line in limited_lines(result.stderr, 6):
        lines.append(f"  ERR {line}")
    if not result.stdout.strip() and not result.stderr.strip():
        lines.append("  OUT -")
    return lines


def emit_report(
    *,
    project_root: Path,
    agent_id: str,
    agent_type: str,
    graph: dict[str, str],
    bootstrap_ok: bool,
    whoami: CommandResult,
    workflow_read: CommandResult,
    workflow_check: CommandResult,
    rag_context: CommandResult,
    rag_journal: CommandResult,
    rag_scars: CommandResult,
    rag_ne_pas: CommandResult,
    proof_token: str = "",
) -> int:
    state = state_summary(project_root)
    claims, cleaned = active_claims_with_cleanup()
    status = "VALID" if bootstrap_ok and whoami.returncode == 0 and workflow_check.returncode == 0 and rag_context.returncode == 0 else "INVALID"
    mode = "STANDARD"
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("📋 SCRIBE-CHECK TENOR V4 — MACHINE PROOF")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"Status init          : {status}")
    print(f"Date session         : {utc_now_iso()}")
    print(f"Skill init-tenor     : {'PRESENT' if (project_root / SKILL_PATH).exists() else 'ABSENT'} — {SKILL_PATH}")
    print(f"Bootstrap execute    : {'OUI' if bootstrap_ok else 'NON'}")
    print(f"Graphify lu          : {graph['status']} — {graph['nodes']} nodes {graph['edges']} edges {graph['communities']} communautes")
    print(f"God-nodes            : {graph['god_nodes']}")
    print(f"Blast radius node 1  : {graph['blast_radius']}")
    print(f"Agent session        : {agent_id}")
    print(f"Whoami proof         : {'OK' if whoami.returncode == 0 else 'FAIL'}")
    print(f"Proof token          : {proof_token}")
    print(f"Proof verify MCP     : verify_proof(token=<above>, agent_id='{agent_id}')")
    print(f"Agent type           : {agent_type}")
    print(f"Lock status          : {lock_summary()}")
    print(f"Last writer          : {state['last_writer']} ({state['last_writer_type']})")
    print(f"State sync           : {state['sync']}")
    print(f"Claims actifs        : {len(claims)}")
    if claims:
        for claim in claims[:5]:
            print(f"  - {claim.get('semantic_claim')} agent={claim.get('agent')} expires_at={claim.get('expires_at')}")
    if cleaned:
        print(f"Claims stale nettoyes: {len(cleaned)}")
    print(f"Mémoire projet lue   : OUI via scribe-rag — {state['line_count']} lignes SCRIBE")
    print(f"Hot entries          : {first_result_line(rag_context.stdout)}")
    print(f"Dernier JOURNAL      : {state['last_journal_id']}")
    print(f"SCARs HOT            : {first_result_line(rag_scars.stdout)}")
    print(f"ne_pas_reproposer    : {first_result_line(rag_ne_pas.stdout)}")
    print(f"Mode actif           : {mode}")
    print("Justification mode   : Init portable complete demandee; STANDARD par defaut avant tache concrete")
    print(f"Workflow ack         : {'ACK_OK' if workflow_check.returncode == 0 else 'NON'}")
    print("MCP Chrome lu        : NON CONCERNE")
    print("Validation nav.      : N/A")
    print("Prochaine action     : Attendre la premiere tache utilisateur; lancer preflight avant code")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("")
    print("PREUVES COMMANDES")
    for line in command_block("whoami", whoami):
        print(line)
    for line in command_block("workflow read", workflow_read):
        print(line)
    for line in command_block("workflow check", workflow_check):
        print(line)
    for line in command_block("scribe-rag context", rag_context):
        print(line)
    for line in command_block("scribe-rag query dernier journal", rag_journal):
        print(line)
    for line in command_block("scribe-rag query scars hot", rag_scars):
        print(line)
    for line in command_block("scribe-rag query ne_pas_reproposer", rag_ne_pas):
        print(line)
    return 0 if status == "VALID" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scribe tenor-init",
        description="Run the deterministic TENOR init sequence for weak or small-context agents.",
    )
    parser.add_argument("--root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--agent", help="Stable agent id. Defaults to SCRIBE_AGENT_ID or generated presence id.")
    parser.add_argument("--type", dest="agent_type", default="cli", choices=sorted(AGENT_TYPES))
    parser.add_argument("--surface", default="tenor-init")
    parser.add_argument("--skip-graphify", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = Path(args.root).resolve()
    if not (project_root / SKILL_PATH).exists():
        print(f"TENOR INIT ERROR: missing {SKILL_PATH}", file=sys.stderr)
        return 2
    agent_id = args.agent or os.environ.get("SCRIBE_AGENT_ID") or f"{args.agent_type}-{os.getpid()}-tenor-init"
    write_presence(agent_id, args.agent_type, args.surface, DEFAULT_TTL_SECONDS, status="idle")

    # Issue signed proof token BEFORE bootstrap so the token exists even if bootstrap partially fails.
    # The token is project-bound (HMAC keyed to project root) and non-falsifiable.
    proof_token = _issue_proof(project_root, agent_id)

    bootstrap_report = bootstrap_project(
        project_root,
        agent=agent_id,
        agent_type=args.agent_type,
        skip_graphify=args.skip_graphify,
    )
    print_report(bootstrap_report)
    bootstrap_ok = bootstrap_report.doctor_code == 0 and not bootstrap_report.errors
    rag = str(project_root / RAG_COMMAND)
    whoami = run_command((str(project_root / ".agent/workflow/scribe/scribe"), "whoami", "--agent", agent_id, "--type", args.agent_type, "--surface", args.surface), project_root)
    workflow_read = run_command((str(project_root / ".agent/workflow/scribe/scribe"), "workflow", "read", "--agent", agent_id, "--type", args.agent_type), project_root)
    workflow_check = run_command((str(project_root / ".agent/workflow/scribe/scribe"), "workflow", "check", "--agent", agent_id), project_root)
    rag_context = run_command((rag, "context"), project_root)
    rag_journal = run_command((rag, "query", "dernier JOURNAL session recente", "--limit", "3"), project_root)
    rag_scars = run_command((rag, "query", "SCAR TIER hot bug regression test_binding", "--limit", "5"), project_root)
    rag_ne_pas = run_command((rag, "query", "ne_pas_reproposer alternatives rejetees ghost", "--limit", "5"), project_root)
    graph = parse_graph_report(project_root / GRAPH_REPORT_PATH)
    return emit_report(
        project_root=project_root,
        agent_id=agent_id,
        agent_type=args.agent_type,
        graph=graph,
        bootstrap_ok=bootstrap_ok,
        whoami=whoami,
        workflow_read=workflow_read,
        workflow_check=workflow_check,
        rag_context=rag_context,
        rag_journal=rag_journal,
        rag_scars=rag_scars,
        rag_ne_pas=rag_ne_pas,
        proof_token=proof_token,
    )


if __name__ == "__main__":
    raise SystemExit(main())
