"""Portable host adapter for the V2.16 TENOR operating layer."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import host_config, instructions as _instructions
from .policy import HostPolicy, HostVerdict

_SAFE_MUST_CALL_TOOLS = frozenset({
    "before_task",
    "resume_task_context",
    "discipline_ping",
    "scribe_query",
    "graphify_query",
    "portability_check",
    "resource_lock_status",
})
_MAX_AUTO_FOLLOW_ITERATIONS = 8
_CALL_RETRY_COUNT = 3
_CALL_RETRY_BASE_DELAY = 0.5
_CALL_RETRY_MAX_DELAY = 8.0
TENOR_INIT_REQUIRED = "TENOR_INIT_REQUIRED"
HOST_MCP_UNBOUND = "HOST_MCP_UNBOUND"


class HostLaunchConfig:
    def __init__(
        self,
        agent_id: str = "",
        host_type: str = "unknown",
        task_id: str = "",
        context_token: str = "",
        workspace_root: Path | str | None = None,
    ) -> None:
        self.agent_id = agent_id or os.environ.get("AGENT_ID", "")
        self.host_type = host_type or os.environ.get("HOST_TYPE", "unknown")
        self.task_id = task_id or os.environ.get("TASK_ID", "")
        self.context_token = context_token or os.environ.get("CONTEXT_TOKEN", "")
        self.workspace_root = Path(workspace_root or os.getcwd()).resolve()

    def to_dict(self) -> dict[str, str]:
        return {
            "agent_id": self.agent_id,
            "host_type": self.host_type,
            "task_id": self.task_id,
            "context_token": self.context_token,
            "workspace_root": str(self.workspace_root),
        }


def _build_pythonpath(workspace_root: Path) -> str:
    """Build a deterministic import path without inheriting an implicit CWD.

    Host adapters may execute a workspace-local entrypoint that delegates to a
    source checkout during tests or development. Preserve absolute, existing
    interpreter paths while rejecting empty/relative entries that would make
    imports depend on the caller's current directory.
    """
    candidates: list[str] = [str((workspace_root / ".agent" / "mcp").resolve())]
    candidates.extend(str(item) for item in sys.path if item)
    existing_env = os.environ.get("PYTHONPATH", "")
    if existing_env:
        candidates.extend(part for part in existing_env.split(os.pathsep) if part)

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        try:
            path = Path(raw).expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if not path.exists():
            continue
        value = str(path)
        key = os.path.normcase(value)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return os.pathsep.join(normalized)


def build_guarded_environment(config: HostLaunchConfig) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "AGENT_SCRIBE_GRAPHIFY_ROOT": str(config.workspace_root),
        "AGENT_ID": config.agent_id,
        "HOST_TYPE": config.host_type,
        "TASK_ID": config.task_id,
        "CONTEXT_TOKEN": config.context_token,
        "SCRIBE_OWNER_PID": str(os.getpid()),
        "PYTHONPATH": _build_pythonpath(config.workspace_root),
    })
    return env


def _runtime_modules(workspace_root: Path):
    mcp_dir = workspace_root / ".agent" / "mcp"
    if str(mcp_dir) not in sys.path:
        sys.path.insert(0, str(mcp_dir))
    from runtime import installation_state
    return installation_state


def inspect_local_tenor_state(config: HostLaunchConfig) -> dict[str, Any]:
    try:
        installation_state = _runtime_modules(config.workspace_root)
        return installation_state.inspect_installation_state(config.workspace_root)
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        argv = [
            sys.executable,
            ".agent/workflow/scribe/scribe",
            "tenor-init",
            "--type",
            "cli",
            "--host",
            config.host_type or "auto",
        ]
        return {
            "ok": False,
            "ready": False,
            "verdict": TENOR_INIT_REQUIRED,
            "reason": f"Cannot inspect local TENOR state: {exc}",
            "next_action": subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv),
            "next_action_argv": argv,
        }


def run_local_tenor_init(
    config: HostLaunchConfig,
    *,
    agent_type: str = "cli",
    timeout: float = 240.0,
) -> dict[str, Any]:
    command = config.workspace_root / ".agent" / "workflow" / "scribe" / "scribe"
    if not command.is_file():
        return {"ok": False, "verdict": TENOR_INIT_REQUIRED, "reason": f"Missing TENOR CLI: {command}"}
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(command),
                "tenor-init",
                "--root",
                str(config.workspace_root),
                "--type",
                agent_type,
                "--host",
                config.host_type or "auto",
            ],
            cwd=str(config.workspace_root),
            env=build_guarded_environment(config),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "verdict": "TENOR_INIT_TIMEOUT",
            "reason": f"TENOR INIT exceeded {timeout}s",
            "stdout": exc.stdout if isinstance(exc.stdout, str) else "",
            "stderr": exc.stderr if isinstance(exc.stderr, str) else "",
        }
    except (FileNotFoundError, OSError) as exc:
        return {"ok": False, "verdict": "TENOR_INIT_LAUNCH_FAILED", "reason": str(exc)}
    state = inspect_local_tenor_state(config)
    ready = result.returncode == 0 and bool(state.get("ready"))
    return {
        "ok": ready,
        "verdict": "TENOR_INIT_LOCAL_READY" if ready else "TENOR_INIT_LOCAL_FAILED",
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "state": state,
    }


def _decode_tool_output(stdout: str, stderr: str) -> dict[str, Any]:
    raw = stdout.strip()
    try:
        outer = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": "JSON_DECODE_FAILED",
            "reason": f"Could not parse tool output as JSON: {exc}",
            "stdout": raw,
            "stderr": stderr.strip(),
        }
    if isinstance(outer, dict) and "content" in outer:
        content = outer.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = str(content[0].get("text", ""))
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"ok": True, "text": text}
    return outer if isinstance(outer, dict) else {"ok": True, "value": outer}


def call_mcp_tool(
    tool_name: str,
    args: dict[str, Any],
    workspace_root: Path,
    timeout: float = 30.0,
    retries: int = _CALL_RETRY_COUNT,
) -> dict[str, Any]:
    entry = workspace_root / ".agent" / "mcp" / "server_entry.py"
    if not entry.is_file():
        return {"ok": False, "error": "ENTRY_SCRIPT_NOT_FOUND", "reason": f"MCP entry script not found at {entry}"}

    config = HostLaunchConfig(agent_id=str(args.get("agent_id") or ""), workspace_root=workspace_root)
    env = build_guarded_environment(config)
    command = [sys.executable, str(entry), "--call", tool_name, "--args", json.dumps(args)]
    delay = _CALL_RETRY_BASE_DELAY
    attempts = max(1, retries)
    last_error: dict[str, Any] = {}

    for attempt in range(attempts):
        try:
            process = subprocess.run(
                command,
                cwd=str(workspace_root),
                env=env,
                text=True,
                capture_output=True,
                timeout=timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "TIMEOUT", "reason": f"Tool call '{tool_name}' timed out after {timeout}s."}
        except (FileNotFoundError, OSError) as exc:
            last_error = {"ok": False, "error": "SUBPROCESS_FAILED", "reason": str(exc), "attempt": attempt + 1}
            if attempt + 1 < attempts:
                time.sleep(min(delay, _CALL_RETRY_MAX_DELAY))
                delay *= 2
                continue
            return last_error

        if process.returncode == 78:
            try:
                payload = json.loads(process.stderr.strip() or process.stdout.strip())
            except json.JSONDecodeError:
                payload = {"reason": process.stderr.strip() or process.stdout.strip()}
            return {
                "ok": False,
                "error": TENOR_INIT_REQUIRED,
                "verdict": TENOR_INIT_REQUIRED,
                "returncode": 78,
                **payload,
            }
        if process.returncode != 0:
            last_error = {
                "ok": False,
                "error": "NON_ZERO_EXIT_CODE",
                "returncode": process.returncode,
                "stdout": process.stdout.strip(),
                "stderr": process.stderr.strip(),
                "reason": f"Tool call exited with status {process.returncode}.",
            }
            if process.returncode == 75 and attempt + 1 < attempts:
                time.sleep(min(delay, _CALL_RETRY_MAX_DELAY))
                delay *= 2
                continue
            return last_error
        return _decode_tool_output(process.stdout, process.stderr)
    return last_error


def run_preflight(
    config: HostLaunchConfig,
    agents_md_path: Path | str | None = None,
    auto_repair_instructions: bool = True,
) -> dict[str, Any]:
    agent_dir = config.workspace_root / ".agent"
    entry = agent_dir / "mcp" / "server_entry.py"
    if not agent_dir.is_dir() or not entry.is_file():
        return {"ok": False, "verdict": HostVerdict.UNSAFE, "reason": "Missing project-local .agent MCP entrypoint."}

    local_state = inspect_local_tenor_state(config)
    if not local_state.get("ready"):
        return {
            "ok": False,
            "verdict": TENOR_INIT_REQUIRED,
            "state": "LOCAL_INIT_REQUIRED",
            "local_state": local_state,
            "next_action": local_state.get("next_action") or "Run TENOR INIT with the current interpreter and exact host id.",
        }

    configured_host = host_config.configure_host(
        config.workspace_root,
        explicit=config.host_type or "auto",
    )
    if not configured_host.get("ok"):
        return {
            "ok": False,
            "verdict": configured_host.get("verdict", HOST_MCP_UNBOUND),
            "state": "HOST_CONFIGURATION_BLOCKED",
            "host_configuration": configured_host,
            "next_action": f"Follow {configured_host.get('guide', '.agent/docs/hosts/README.md')}",
        }
    if configured_host.get("restart_required"):
        return {
            "ok": False,
            "verdict": "HOST_RECONNECT_REQUIRED",
            "state": "LOCAL_INIT_READY_HOST_RECONNECT_REQUIRED",
            "host_configuration": configured_host,
            "next_action": "Restart/reconnect the detected host, then rerun TENOR INIT.",
        }

    try:
        process = subprocess.run(
            [sys.executable, str(entry), "--list-tools"],
            cwd=str(config.workspace_root),
            env=build_guarded_environment(config),
            text=True,
            capture_output=True,
            timeout=20,
            shell=False,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "verdict": HostVerdict.UNSAFE, "reason": f"Failed to list local MCP tools: {exc}"}
    if process.returncode != 0:
        return {"ok": False, "verdict": HostVerdict.UNSAFE, "reason": process.stderr.strip() or process.stdout.strip(), "returncode": process.returncode}
    try:
        data = json.loads(process.stdout)
        tools = [str(item.get("name")) for item in data.get("tools", []) if isinstance(item, dict) and item.get("name")]
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        return {"ok": False, "verdict": HostVerdict.UNSAFE, "reason": f"Failed to parse local tools JSON: {exc}"}

    policy = HostPolicy(config.workspace_root)
    missing = policy.get_missing_tools(tools)
    capabilities = policy.classify_host_capabilities()
    target = Path(agents_md_path) if agents_md_path is not None else config.workspace_root / "AGENTS.md"
    instruction_result: dict[str, Any] | None = None
    installed = _instructions.verify_instruction_installation(target)
    if not installed and auto_repair_instructions:
        instruction_result = _instructions.install_host_instructions(target, config.host_type, config.workspace_root)
        installed = bool(instruction_result.get("ok"))
    verdict = policy.decide_host_safety_level(tools, capabilities, installed)
    return {
        "ok": not missing,
        "verdict": verdict,
        "state": "LOCAL_MCP_READY_HOST_VISIBILITY_UNPROVEN",
        "local_server_ready": True,
        "host_tools_visible_to_llm": None,
        "host_visibility_verdict": HOST_MCP_UNBOUND,
        "available_tools": tools,
        "missing_tools": missing,
        "capabilities": capabilities,
        "instruction_block_ok": installed,
        "instruction_repair_result": instruction_result,
        "host_configuration": configured_host,
        "local_state": local_state,
        "next_action": "Verify these tools in the host LLM tool interface and prove the MCP root binding.",
    }


def run_portability_check(config: HostLaunchConfig) -> dict[str, Any]:
    return call_mcp_tool("portability_check", {"workspace_root": str(config.workspace_root)}, config.workspace_root, timeout=15.0)


def run_discipline_ping(config: HostLaunchConfig, phase: str = "", resource: str = "") -> dict[str, Any]:
    return call_mcp_tool("discipline_ping", {"agent_id": config.agent_id, "phase": phase, "resource": resource}, config.workspace_root)


def run_pre_action_guard(
    config: HostLaunchConfig,
    request: str,
    intent: str,
    resource: str,
    planned_action: str,
    task_id: str = "",
    context_token: str = "",
) -> dict[str, Any]:
    return call_mcp_tool(
        "pre_action_guard",
        {
            "agent_id": config.agent_id,
            "request": request,
            "intent": intent,
            "resource": resource,
            "planned_action": planned_action,
            "task_id": task_id or config.task_id,
            "context_token": context_token or config.context_token,
        },
        config.workspace_root,
    )


def run_guard_auto_follow(
    config: HostLaunchConfig,
    request: str,
    intent: str,
    resource: str,
    planned_action: str,
    task_id: str = "",
    context_token: str = "",
    max_iterations: int = _MAX_AUTO_FOLLOW_ITERATIONS,
) -> dict[str, Any]:
    trace: list[dict[str, Any]] = []
    called: set[str] = set()
    current_task = task_id or config.task_id
    current_context = context_token or config.context_token
    for iteration in range(max(1, max_iterations)):
        guard = run_pre_action_guard(config, request, intent, resource, planned_action, current_task, current_context)
        trace.append({"step": f"guard_{iteration}", "result": guard})
        if not guard.get("ok"):
            return {"ok": False, "verdict": "AUTO_FOLLOW_GUARD_FAILED", "guard_result": guard, "trace": trace}
        if guard.get("verdict") == "PRE_ACTION_GUARD_OK":
            lease = guard.get("action_lease") if isinstance(guard.get("action_lease"), dict) else guard.get("lease", {})
            return {
                "ok": True,
                "verdict": "PRE_ACTION_GUARD_OK",
                "action_lease_id": guard.get("action_lease_id") or (lease or {}).get("lease_id", ""),
                "lease": lease or {},
                "iterations": iteration + 1,
                "trace": trace,
            }
        raw_must_call = guard.get("must_call") or []
        if isinstance(raw_must_call, dict):
            raw_must_call = [raw_must_call.get("tool")]
        must_call = [str(tool) for tool in raw_must_call if tool]
        if not must_call:
            return {"ok": False, "verdict": "MANUAL_INTERVENTION_REQUIRED", "guard_result": guard, "trace": trace}
        for tool in must_call:
            if tool not in _SAFE_MUST_CALL_TOOLS:
                return {"ok": False, "verdict": "MANUAL_INTERVENTION_REQUIRED", "reason": f"{tool} is not auto-follow safe", "trace": trace}
            if tool in called:
                return {"ok": False, "verdict": "AUTO_FOLLOW_CYCLE_DETECTED", "reason": f"{tool} repeated", "trace": trace}
            arguments: dict[str, Any] = {"agent_id": config.agent_id}
            if current_task:
                arguments["task_id"] = current_task
            if current_context:
                arguments["context_token"] = current_context
            if resource:
                arguments["resource"] = resource
            if tool == "before_task":
                arguments.update({"request": request, "intent": intent})
            result = call_mcp_tool(tool, arguments, config.workspace_root)
            called.add(tool)
            trace.append({"step": f"auto_{tool}", "result": result})
            if not result.get("ok"):
                return {"ok": False, "verdict": "AUTO_FOLLOW_TOOL_FAILED", "failed_tool": tool, "tool_result": result, "trace": trace}
            current_task = str(result.get("task_id") or current_task)
            current_context = str(result.get("context_token") or current_context)
    return {"ok": False, "verdict": "AUTO_FOLLOW_MAX_ITERATIONS", "trace": trace}


def run_lease_extend(config: HostLaunchConfig, lease_id: str, extend_seconds: int | None = None) -> dict[str, Any]:
    if not lease_id:
        return {"ok": False, "error": "LEASE_ID_REQUIRED"}
    args: dict[str, Any] = {"lease_id": lease_id, "agent_id": config.agent_id}
    if extend_seconds is not None:
        args["extend_seconds"] = int(extend_seconds)
    return call_mcp_tool("lease_extend", args, config.workspace_root)


def run_resource_lock_claim(config: HostLaunchConfig, resource: str, task_id: str = "", ttl_seconds: int | None = None) -> dict[str, Any]:
    if not resource:
        return {"ok": False, "error": "RESOURCE_REQUIRED"}
    args: dict[str, Any] = {"agent_id": config.agent_id, "resource": resource, "task_id": task_id or config.task_id}
    if ttl_seconds is not None:
        args["ttl_seconds"] = int(ttl_seconds)
    return call_mcp_tool("resource_lock_claim", args, config.workspace_root)


def run_resource_lock_release(config: HostLaunchConfig, resource: str, lock_id: str = "") -> dict[str, Any]:
    if not resource:
        return {"ok": False, "error": "RESOURCE_REQUIRED"}
    args = {"agent_id": config.agent_id, "resource": resource}
    if lock_id:
        args["lock_id"] = lock_id
    return call_mcp_tool("resource_lock_release", args, config.workspace_root)


def run_resource_lock_status(config: HostLaunchConfig, resource: str) -> dict[str, Any]:
    if not resource:
        return {"ok": False, "error": "RESOURCE_REQUIRED"}
    return call_mcp_tool("resource_lock_status", {"resource": resource}, config.workspace_root)


def run_tenor_init_bridge(
    config: HostLaunchConfig,
    agent_session_id: str,
    host_tool: str = "",
    model_name: str = "",
    proof_token: str = "",
) -> dict[str, Any]:
    if not agent_session_id:
        return {"ok": False, "verdict": "TENOR_INIT_BRIDGE_INVALID", "reason": "agent_session_id is required"}
    del host_tool, model_name, proof_token
    return {
        "ok": False,
        "verdict": "TENOR_INIT_BRIDGE_HOST_UNBOUND",
        "state": HOST_MCP_UNBOUND,
        "reason": (
            "The local launcher cannot prove host MCP visibility. Call tenor_init_bridge "
            "through the real host-visible MCP tool surface after reconnect."
        ),
        "agent_session_id": agent_session_id,
        "host_id": config.host_type or "unknown",
    }


def run_workspace_audit(config: HostLaunchConfig, task_id: str = "", resource: str = "") -> dict[str, Any]:
    return call_mcp_tool(
        "workspace_audit",
        {"agent_id": config.agent_id, "task_id": task_id or config.task_id, "resource": resource},
        config.workspace_root,
    )
