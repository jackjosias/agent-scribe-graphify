#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

from scribe_bootstrap import bootstrap_project, print_report
from scribe_identity import DEFAULT_TTL_SECONDS, write_presence
from scribe_output_paths import graphify_out_dir
from scribe_tenor_init import (
    RAG_COMMAND,
    SKILL_PATH,
    _issue_proof,
    build_parser,
    emit_report,
    parse_graph_report,
    run_command,
)

_MCP_ROOT = Path(__file__).resolve().parents[4] / "mcp"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

from runtime.tenor_init_orchestrator import (  # noqa: E402
    TENOR_INIT_ALREADY_RUNNING,
    TenorInitBusy,
    finalize_tenor_init,
    prepare_tenor_init,
    refresh_tenor_init_lock,
    tenor_init_lock,
)


def _flush(message: str) -> None:
    print(message, flush=True)


def _wait_notice(lock: dict[str, object]) -> None:
    owner = lock.get("pid") or "unknown"
    started = lock.get("created_at") or "unknown"
    stage = lock.get("stage") or "unknown"
    _flush(f"TENOR_INIT_WAIT shared bootstrap running pid={owner} since={started} stage={stage}")


def main() -> int:
    args = build_parser().parse_args()
    project_root = Path(args.root).resolve()
    if not (project_root / SKILL_PATH).exists():
        print(f"TENOR INIT ERROR: missing {SKILL_PATH}", file=sys.stderr, flush=True)
        return 2

    agent_id = args.agent or os.environ.get("SCRIBE_AGENT_ID") or f"{args.agent_type}-{os.getpid()}-tenor-init"
    _flush(f"TENOR_INIT_START root={project_root} type={args.agent_type} agent={agent_id}")
    _flush("TENOR_INIT_STAGE acquire_shared_init_lock")

    try:
        with tenor_init_lock(project_root, wait_timeout_seconds=180.0, on_wait=_wait_notice) as acquired_lock:
            lock = refresh_tenor_init_lock(acquired_lock, stage="classify_installation")
            _flush("TENOR_INIT_STAGE classify_installation")
            installation = prepare_tenor_init(project_root)
            if not installation.ok:
                print(
                    f"TENOR_INIT_INSTALLATION_FAILED verdict={installation.installation_verdict}",
                    file=sys.stderr,
                    flush=True,
                )
                return 3

            _flush(
                "TENOR_INIT_INSTALLATION "
                f"classification={installation.classification} "
                f"project_changed={str(installation.project_changed).lower()} "
                f"relocated={str(installation.relocated).lower()} "
                f"purge_executed={str(installation.purge_executed).lower()}"
            )
            _flush(f"TENOR_INIT_MEMORY action={installation.memory_action}")

            lock = refresh_tenor_init_lock(lock, stage="bootstrap_project")
            _flush("TENOR_INIT_STAGE bootstrap_project")
            bootstrap_report = bootstrap_project(
                project_root,
                agent=agent_id,
                agent_type=args.agent_type,
                skip_graphify=args.skip_graphify,
                installation_plan=installation,
            )
            print_report(bootstrap_report)
            bootstrap_ok = bootstrap_report.doctor_code == 0 and not bootstrap_report.errors
            if not bootstrap_ok:
                print("TENOR_INIT_BOOTSTRAP_FAILED", file=sys.stderr, flush=True)
                return 4

            lock = refresh_tenor_init_lock(lock, stage="finalize_installation")
            _flush("TENOR_INIT_STAGE finalize_installation")
            finalized = finalize_tenor_init(project_root)
            if not finalized.get("ok"):
                print(
                    f"TENOR_INIT_FINALIZE_FAILED verdict={finalized.get('verdict')}",
                    file=sys.stderr,
                    flush=True,
                )
                return 5
    except TenorInitBusy as exc:
        owner = exc.lock.get("pid") or "unknown"
        started = exc.lock.get("created_at") or "unknown"
        stage = exc.lock.get("stage") or "unknown"
        print(
            f"{TENOR_INIT_ALREADY_RUNNING} pid={owner} since={started} stage={stage}",
            file=sys.stderr,
            flush=True,
        )
        return 75

    _flush("TENOR_INIT_STAGE register_session_presence")
    write_presence(agent_id, args.agent_type, args.surface, DEFAULT_TTL_SECONDS, status="idle")
    proof_token = _issue_proof(project_root, agent_id)

    _flush("TENOR_INIT_STAGE load_scribe_and_graphify_context")
    rag = str(project_root / RAG_COMMAND)
    scribe_command = str(project_root / ".agent/workflow/scribe/scribe")
    whoami = run_command(
        (scribe_command, "whoami", "--agent", agent_id, "--type", args.agent_type, "--surface", args.surface),
        project_root,
    )
    workflow_read = run_command(
        (scribe_command, "workflow", "read", "--agent", agent_id, "--type", args.agent_type),
        project_root,
    )
    workflow_check = run_command(
        (scribe_command, "workflow", "check", "--agent", agent_id),
        project_root,
    )
    rag_context = run_command((rag, "context"), project_root)
    rag_journal = run_command((rag, "query", "dernier JOURNAL session recente", "--limit", "3"), project_root)
    rag_scars = run_command((rag, "query", "SCAR TIER hot bug regression test_binding", "--limit", "5"), project_root)
    rag_ne_pas = run_command((rag, "query", "ne_pas_reproposer alternatives rejetees ghost", "--limit", "5"), project_root)
    graph = parse_graph_report(graphify_out_dir(project_root) / "GRAPH_REPORT.md")

    _flush("TENOR_INIT_STAGE emit_machine_proof")
    return emit_report(
        project_root=project_root,
        agent_id=agent_id,
        agent_type=args.agent_type,
        graph=graph,
        bootstrap_ok=True,
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
