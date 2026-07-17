#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from scribe_bootstrap import bootstrap_project, print_report
from scribe_identity import DEFAULT_TTL_SECONDS, write_presence
from scribe_output_paths import graphify_out_dir
from scribe_tenor_init import (
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
_AGENT_ROOT = Path(__file__).resolve().parents[4]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from host_adapter import host_config  # noqa: E402
from runtime import graphify_readiness  # noqa: E402
from runtime.tenor_init_orchestrator import (  # noqa: E402
    TENOR_INIT_ALREADY_RUNNING,
    TenorInitBusy,
    finalize_tenor_init,
    prepare_tenor_init,
    refresh_tenor_init_lock,
    tenor_init_lock,
)

_REQUIRED_LOCAL_MCP_TOOLS = {
    "file_hash",
    "tenor_init_bridge",
    "portability_check",
    "graphify_required_check",
    "graphify_project_build",
    "tenor_task_start",
    "tenor_apply_changeset",
    "tenor_activity",
    "tenor_task_control",
}


def _flush(message: str) -> None:
    print(message, flush=True)


def _wait_notice(lock: dict[str, object]) -> None:
    owner = lock.get("pid") or "unknown"
    started = lock.get("created_at") or "unknown"
    stage = lock.get("stage") or "unknown"
    _flush(f"TENOR_INIT_WAIT shared bootstrap running pid={owner} since={started} stage={stage}")


def _graph_machine_summary(project_root: Path) -> tuple[dict[str, str], graphify_readiness.Readiness]:
    readiness = graphify_readiness.inspect_graphify_readiness(project_root, allow_fixture=False)
    graph = parse_graph_report(graphify_out_dir(project_root) / "GRAPH_REPORT.md")
    graph["status"] = readiness.verdict
    graph["nodes"] = str(readiness.node_count)
    graph["edges"] = str(readiness.edge_count)
    return graph, readiness


def main() -> int:
    args = build_parser().parse_args()
    project_root = Path(args.root).resolve()
    if not (project_root / SKILL_PATH).exists():
        print(f"TENOR INIT ERROR: missing {SKILL_PATH}", file=sys.stderr, flush=True)
        return 2
    if args.skip_graphify:
        print(
            "TENOR INIT ERROR: --skip-graphify is forbidden on the canonical init path; "
            "use bounded `scribe graph --project-build` or an isolated test fixture.",
            file=sys.stderr,
            flush=True,
        )
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
                skip_graphify=False,
                installation_plan=installation,
            )
            print_report(bootstrap_report)
            bootstrap_ok = bootstrap_report.doctor_code == 0 and not bootstrap_report.errors
            if not bootstrap_ok:
                print("TENOR_INIT_BOOTSTRAP_FAILED", file=sys.stderr, flush=True)
                return 4

            lock = refresh_tenor_init_lock(lock, stage="verify_graphify_readiness")
            _flush("TENOR_INIT_STAGE verify_graphify_readiness")
            graph, graph_ready = _graph_machine_summary(project_root)
            if not graph_ready.ok:
                print(
                    f"TENOR_INIT_GRAPHIFY_INVALID verdict={graph_ready.verdict} "
                    f"reason={graph_ready.reason} next={graph_ready.next_action}",
                    file=sys.stderr,
                    flush=True,
                )
                return 4
            _flush(
                f"TENOR_INIT_GRAPHIFY verdict={graph_ready.verdict} "
                f"nodes={graph_ready.node_count} edges={graph_ready.edge_count} "
                f"sources={graph_ready.source_file_count}"
            )

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

            lock = refresh_tenor_init_lock(lock, stage="configure_project_local_host")
            _flush("TENOR_INIT_STAGE configure_project_local_host")
            host_report = host_config.configure_host(project_root, explicit=args.host)
            _flush(
                "TENOR_INIT_HOST "
                f"host={host_report.get('host_id', 'unknown')} "
                f"verdict={host_report.get('verdict', 'HOST_CONFIG_INVALID')} "
                f"guide={host_report.get('guide', '-') }"
            )
            if not host_report.get("ok"):
                print(
                    f"TENOR_INIT_HOST_BLOCKED verdict={host_report.get('verdict')} "
                    f"reason={host_report.get('reason', '')} guide={host_report.get('guide', '-')}",
                    file=sys.stderr,
                    flush=True,
                )
                return 6
            if host_report.get("restart_required"):
                print(
                    "HOST_RECONNECT_REQUIRED: project-local MCP configuration changed; "
                    "restart/reconnect the detected host, then rerun TENOR INIT.",
                    file=sys.stderr,
                    flush=True,
                )
                return 76

            lock = refresh_tenor_init_lock(lock, stage="verify_local_mcp_server")
            _flush("TENOR_INIT_STAGE verify_local_mcp_server")
            local_mcp = run_command(
                (sys.executable, ".agent/mcp/server_entry.py", "--list-tools"),
                project_root,
            )
            if local_mcp.returncode != 0:
                print(
                    "TENOR_INIT_LOCAL_MCP_FAILED "
                    f"rc={local_mcp.returncode} stderr={local_mcp.stderr.strip()}",
                    file=sys.stderr,
                    flush=True,
                )
                return 7
            try:
                listed = json.loads(local_mcp.stdout)
                available = {
                    str(item.get("name"))
                    for item in listed.get("tools", [])
                    if isinstance(item, dict) and item.get("name")
                }
            except (AttributeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                print(f"TENOR_INIT_LOCAL_MCP_INVALID_OUTPUT reason={exc}", file=sys.stderr, flush=True)
                return 7
            missing_tools = sorted(_REQUIRED_LOCAL_MCP_TOOLS - available)
            if missing_tools:
                print(
                    f"TENOR_INIT_LOCAL_MCP_INCOMPLETE missing={','.join(missing_tools)}",
                    file=sys.stderr,
                    flush=True,
                )
                return 7
            _flush(f"TENOR_INIT_LOCAL_MCP_READY tools={len(available)}")
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
    try:
        _issue_proof(project_root, agent_id)
        proof_issued = True
    except Exception as exc:  # noqa: BLE001
        print(
            f"TENOR_INIT_PROOF_ISSUE_FAILED: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 5

    _flush("TENOR_INIT_STAGE emit_machine_proof")
    return emit_report(
        project_root=project_root,
        agent_id=agent_id,
        agent_type=args.agent_type,
        graph=graph,
        bootstrap_ok=True,
        proof_issued=proof_issued,
        host_report=host_report,
        local_mcp=local_mcp,
    )


if __name__ == "__main__":
    raise SystemExit(main())
