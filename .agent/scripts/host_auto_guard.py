#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import json
from pathlib import Path

# Add parent of scripts (.agent) to path so we can import host_adapter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from host_adapter.launcher import (
    HostLaunchConfig,
    run_preflight,
    run_discipline_ping,
    run_pre_action_guard,
    run_workspace_audit,
)
from host_adapter.instructions import install_host_instructions


def print_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def handle_preflight(args: argparse.Namespace) -> int:
    config = HostLaunchConfig(
        agent_id=args.agent_id,
        host_type=args.host,
        workspace_root=args.workspace_root,
    )
    res = run_preflight(config)
    if args.json:
        print_json(res)
        return 0 if res.get("ok") else 1

    if not res.get("ok"):
        print(f"PREFLIGHT_FAILED: {res.get('reason')}")
        print("Verdict host: UNSAFE")
        return 1

    verdict = res.get("verdict", "UNSAFE")
    print(f"PREFLIGHT_OK")
    print(f"Verdict host: {verdict}")

    if verdict == "UNSAFE":
        print("Missing required MCP tools in server list. Please verify server installation.")
    elif verdict == "SAFE_CANDIDATE":
        print("Required MCP tools present. Note: Direct write capability is open outside sandbox.")

    return 0


def handle_install(args: argparse.Namespace) -> int:
    target_path = Path(args.target)
    if target_path.is_dir():
        target_path = target_path / "AGENTS.md"

    try:
        res = install_host_instructions(
            target_file=target_path,
            host_type=args.host,
            workspace_root=args.workspace_root,
        )
    except Exception as exc:
        if args.json:
            print_json({"ok": False, "error": "EXCEPTION", "reason": str(exc)})
        else:
            print(f"INSTALL_FAILED: {exc}")
        return 1

    if args.json:
        print_json(res)
    else:
        if res.get("ok"):
            print(f"Instructions successfully installed to: {res.get('installed_at')}")
            print(f"Idempotent verification completed.")
        else:
            print(f"Installation failed: {res.get('reason')}")

    return 0 if res.get("ok") else 1


def handle_ping(args: argparse.Namespace) -> int:
    config = HostLaunchConfig(
        agent_id=args.agent_id,
        workspace_root=args.workspace_root,
    )
    res = run_discipline_ping(config, phase=args.phase, resource=args.resource)
    if args.json:
        print_json(res)
    else:
        if res.get("ok") is False:
            print(f"PING_FAILED: {res.get('reason') or res.get('error')}")
            return 1
        print("PING_OK")
        print(f"Agent ID: {res.get('agent_id')}")
        print(f"Status  : {res.get('agent_status')}")
    return 0


def handle_guard(args: argparse.Namespace) -> int:
    config = HostLaunchConfig(
        agent_id=args.agent_id,
        task_id=args.task_id,
        context_token=args.context_token,
        workspace_root=args.workspace_root,
    )
    res = run_pre_action_guard(
        config=config,
        request=args.request,
        intent=args.intent,
        resource=args.resource,
        planned_action=args.planned_action,
        task_id=args.task_id,
        context_token=args.context_token,
    )

    if args.json:
        print_json(res)
        return 0

    verdict = res.get("verdict", "")
    state = res.get("state", "")

    if verdict == "PRE_ACTION_GUARD_OK":
        print("PRE_ACTION_GUARD_OK")
        action_lease = res.get("action_lease")
        if action_lease:
            print(f"action_lease_id: {action_lease.get('lease_id')}")
        else:
            print("No action lease required (read-only command).")
        return 0

    if verdict == "NEXT_ACTION_REQUIRED" or "REQUIRED" in verdict or "REQUIRED" in state:
        print(f"FSM State: {state or verdict}")
        must_call = res.get("must_call")
        if must_call:
            print("must_call:")
            print(f"  Tool: {must_call.get('tool')}")
            print(f"  Args: {json.dumps(must_call.get('args', {}))}")
            # Recommended next command
            tname = must_call.get("tool")
            targs = must_call.get("args", {})
            print(f"Next command recommended: python3 .agent/mcp/server_entry.py --call {tname} --args '{json.dumps(targs)}'")
        else:
            print("Must call next protocol tool before proceeding.")
        return 0

    print(f"FORBIDDEN: {res.get('reason', 'Action is currently forbidden under the discipline policy.')}")
    return 1


def handle_audit(args: argparse.Namespace) -> int:
    config = HostLaunchConfig(
        agent_id=args.agent_id,
        task_id=args.task_id,
        workspace_root=args.workspace_root,
    )
    res = run_workspace_audit(config, task_id=args.task_id, resource=args.resource)

    if args.json:
        print_json(res)
        return 0

    verdict = res.get("verdict", "WORKSPACE_AUDIT_UNAVAILABLE")
    print(verdict)
    if verdict == "DIRECT_WRITE_BYPASS_DETECTED":
        modified = res.get("modified_files", [])
        print(f"Modified files bypassing MCP: {', '.join(modified)}")
    return 0


def handle_status(args: argparse.Namespace) -> int:
    config = HostLaunchConfig(
        agent_id=args.agent_id,
        task_id=args.task_id,
        context_token=args.context_token,
        workspace_root=args.workspace_root,
    )
    print("Host Auto-Guard Configuration:")
    print_json(config.to_dict())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Host Auto-Guard CLI")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    parser.add_argument("--workspace-root", type=str, default=".", help="Workspace root path")

    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    # preflight
    p_pre = subparsers.add_parser("preflight", help="Run preflight checks")
    p_pre.add_argument("--host", type=str, default="unknown", help="Host engine name")
    p_pre.add_argument("--agent-id", type=str, default="", help="Agent identifier")

    # install-instructions
    p_inst = subparsers.add_parser("install-instructions", help="Install auto-guard instructions")
    p_inst.add_argument("--host", type=str, default="unknown", help="Host engine name")
    p_inst.add_argument("--target", type=str, default=".", help="Target file or directory")

    # ping
    p_ping = subparsers.add_parser("ping", help="Run FSM discipline ping")
    p_ping.add_argument("--agent-id", type=str, required=True, help="Agent identifier")
    p_ping.add_argument("--phase", type=str, default="", help="Ping phase")
    p_ping.add_argument("--resource", type=str, default="", help="Ping resource")

    # guard
    p_guard = subparsers.add_parser("guard", help="Verify planned action before execution")
    p_guard.add_argument("--agent-id", type=str, required=True, help="Agent identifier")
    p_guard.add_argument("--request", type=str, default="", help="User request string")
    p_guard.add_argument("--intent", type=str, default="", help="User intent (e.g., write)")
    p_guard.add_argument("--resource", type=str, default="", help="Resource target path")
    p_guard.add_argument("--planned-action", type=str, required=True, help="MCP action to execute")
    p_guard.add_argument("--task-id", type=str, default="", help="Task identifier")
    p_guard.add_argument("--context-token", type=str, default="", help="Context token")

    # audit
    p_aud = subparsers.add_parser("audit", help="Run workspace audit to check bypass writes")
    p_aud.add_argument("--agent-id", type=str, required=True, help="Agent identifier")
    p_aud.add_argument("--task-id", type=str, required=True, help="Task identifier")
    p_aud.add_argument("--resource", type=str, default="", help="Audited resource")

    # status
    p_stat = subparsers.add_parser("status", help="Show configuration status")
    p_stat.add_argument("--agent-id", type=str, default="", help="Agent identifier")
    p_stat.add_argument("--task-id", type=str, default="", help="Task identifier")
    p_stat.add_argument("--context-token", type=str, default="", help="Context token")

    args = parser.parse_args()

    # Append path if host_adapter module needs local resolution
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    if args.command == "preflight":
        return handle_preflight(args)
    elif args.command == "install-instructions":
        return handle_install(args)
    elif args.command == "ping":
        return handle_ping(args)
    elif args.command == "guard":
        return handle_guard(args)
    elif args.command == "audit":
        return handle_audit(args)
    elif args.command == "status":
        return handle_status(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
