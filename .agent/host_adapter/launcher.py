from __future__ import annotations

import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Any

from .policy import HostPolicy, HostVerdict


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

        if workspace_root is None:
            self.workspace_root = Path(os.getcwd()).resolve()
        else:
            self.workspace_root = Path(workspace_root).resolve()

    def to_dict(self) -> dict[str, str]:
        return {
            "agent_id": self.agent_id,
            "host_type": self.host_type,
            "task_id": self.task_id,
            "context_token": self.context_token,
            "workspace_root": str(self.workspace_root),
        }


def build_guarded_environment(config: HostLaunchConfig) -> dict[str, str]:
    env = dict(os.environ)
    env["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(config.workspace_root)
    env["AGENT_ID"] = config.agent_id
    env["HOST_TYPE"] = config.host_type
    env["TASK_ID"] = config.task_id
    env["CONTEXT_TOKEN"] = config.context_token
    # Set owner pid to current process
    env["SCRIBE_OWNER_PID"] = str(os.getpid())
    return env


def call_mcp_tool(
    tool_name: str,
    args: dict[str, Any],
    workspace_root: Path,
    timeout: float = 30.0,
) -> dict[str, Any]:
    entry_script = workspace_root / ".agent" / "mcp" / "server_entry.py"
    if not entry_script.exists():
        return {
            "ok": False,
            "error": "ENTRY_SCRIPT_NOT_FOUND",
            "reason": f"MCP entry script not found at {entry_script}",
        }

    env = dict(os.environ)
    env["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(workspace_root)
    # Add MCP directory to PYTHONPATH so subprocess python can import mcp modules
    mcp_dir = Path(__file__).resolve().parents[1] / "mcp"
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    if existing_pythonpath:
        env["PYTHONPATH"] = f"{mcp_dir}{os.pathsep}{existing_pythonpath}"
    else:
        env["PYTHONPATH"] = str(mcp_dir)

    # also set AGENT_ID if present in args
    if "agent_id" in args:
        env["AGENT_ID"] = args["agent_id"]

    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(entry_script),
                "--call",
                tool_name,
                "--args",
                json.dumps(args),
            ],
            cwd=str(workspace_root),
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "TIMEOUT",
            "reason": f"Subprocess tool call timed out after {timeout} seconds.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": "SUBPROCESS_FAILED",
            "reason": str(exc),
        }

    if proc.returncode != 0:
        return {
            "ok": False,
            "error": "NON_ZERO_EXIT_CODE",
            "exit_code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "reason": f"Tool call exited with status {proc.returncode}.",
        }

    raw_output = proc.stdout.strip()
    try:
        outer = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": "JSON_DECODE_FAILED",
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "reason": f"Could not parse tool output as JSON: {exc}",
        }

    # Handle standard MCP output wrapping
    if isinstance(outer, dict) and "content" in outer:
        content_list = outer["content"]
        if content_list and isinstance(content_list, list):
            text_val = content_list[0].get("text", "")
            try:
                return json.loads(text_val)
            except json.JSONDecodeError:
                return {"ok": True, "text": text_val}

    return outer


def run_preflight(config: HostLaunchConfig) -> dict[str, Any]:
    agent_dir = config.workspace_root / ".agent"
    if not agent_dir.exists():
        return {
            "ok": False,
            "verdict": HostVerdict.UNSAFE,
            "reason": "Missing .agent directory at workspace root.",
        }

    entry_script = agent_dir / "mcp" / "server_entry.py"
    if not entry_script.exists():
        return {
            "ok": False,
            "verdict": HostVerdict.UNSAFE,
            "reason": f"Missing server_entry.py at {entry_script}.",
        }

    # Retrieve available tools
    try:
        proc = subprocess.run(
            [sys.executable, str(entry_script), "--list-tools"],
            cwd=str(config.workspace_root),
            text=True,
            capture_output=True,
            timeout=15,
            shell=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "verdict": HostVerdict.UNSAFE,
            "reason": f"Failed to list MCP tools: {exc}",
        }

    if proc.returncode != 0:
        return {
            "ok": False,
            "verdict": HostVerdict.UNSAFE,
            "reason": f"List tools failed: {proc.stderr.strip() or proc.stdout.strip()}",
        }

    try:
        data = json.loads(proc.stdout)
        tools_list = [t.get("name") for t in data.get("tools", [])]
    except Exception as exc:
        return {
            "ok": False,
            "verdict": HostVerdict.UNSAFE,
            "reason": f"Failed to parse tools JSON: {exc}",
        }

    policy = HostPolicy(config.workspace_root)
    capabilities = policy.classify_host_capabilities()
    verdict = policy.decide_host_safety_level(tools_list, capabilities)

    return {
        "ok": True,
        "verdict": verdict,
        "available_tools": tools_list,
        "capabilities": capabilities,
    }


def run_discipline_ping(
    config: HostLaunchConfig,
    phase: str = "",
    resource: str = "",
) -> dict[str, Any]:
    args = {
        "agent_id": config.agent_id,
        "phase": phase,
        "resource": resource,
    }
    return call_mcp_tool("discipline_ping", args, config.workspace_root)


def run_pre_action_guard(
    config: HostLaunchConfig,
    request: str,
    intent: str,
    resource: str,
    planned_action: str,
    task_id: str = "",
    context_token: str = "",
) -> dict[str, Any]:
    args = {
        "agent_id": config.agent_id,
        "request": request,
        "intent": intent,
        "resource": resource,
        "planned_action": planned_action,
        "task_id": task_id or config.task_id,
        "context_token": context_token or config.context_token,
    }
    return call_mcp_tool("pre_action_guard", args, config.workspace_root)


def run_workspace_audit(
    config: HostLaunchConfig,
    task_id: str = "",
    resource: str = "",
) -> dict[str, Any]:
    args = {
        "agent_id": config.agent_id,
        "task_id": task_id or config.task_id,
        "resource": resource,
    }
    return call_mcp_tool("workspace_audit", args, config.workspace_root)
