"""launcher.py — Host adapter launcher with full V2.14 support.

Additions over V2.13:
  - run_lease_extend()        : renew an active lease mid-operation (prevents
                                ACTION_LEASE_EXPIRED in high-latency environments).
  - run_guard_auto_follow()   : auto-execute safe must_call steps until
                                PRE_ACTION_GUARD_OK is reached, returning the
                                final lease to the caller.
  - run_preflight()           : now also checks instruction block presence and
                                triggers auto-repair when the block is missing.
  - run_portability_check()   : verifies .agent is at the git root.
  - run_resource_lock_*()     : multi-agent resource locking helpers.
"""
from __future__ import annotations

import os
import sys
import json
import time
import subprocess
from pathlib import Path
from typing import Any

from .policy import HostPolicy, HostVerdict
from . import instructions as _instr


# ─────────────────────────────────────────────────────────────
# Safe must_call steps that guard --auto-follow may execute
# automatically without human approval.
# These are read-only or idempotent setup tools — never write tools.
# ─────────────────────────────────────────────────────────────
_SAFE_MUST_CALL_TOOLS = frozenset({
    "before_task",
    "resume_task_context",
    "discipline_ping",
    "scribe_query",
    "graphify_query",
    "portability_check",
    "resource_lock_status",
})

# Maximum auto-follow iterations to prevent infinite loops.
_MAX_AUTO_FOLLOW_ITERATIONS = 8

# Default retry settings for transient subprocess failures.
_CALL_RETRY_COUNT = 3
_CALL_RETRY_BASE_DELAY = 0.5   # seconds — doubles each retry (exp backoff)
_CALL_RETRY_MAX_DELAY = 8.0


# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────
# Environment helpers
# ─────────────────────────────────────────────────────────────

def build_guarded_environment(config: HostLaunchConfig) -> dict[str, str]:
    env = dict(os.environ)
    env["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(config.workspace_root)
    env["AGENT_ID"] = config.agent_id
    env["HOST_TYPE"] = config.host_type
    env["TASK_ID"] = config.task_id
    env["CONTEXT_TOKEN"] = config.context_token
    env["SCRIBE_OWNER_PID"] = str(os.getpid())
    return env


# ─────────────────────────────────────────────────────────────
# Core subprocess call with exponential-backoff retry
# ─────────────────────────────────────────────────────────────

def call_mcp_tool(
    tool_name: str,
    args: dict[str, Any],
    workspace_root: Path,
    timeout: float = 30.0,
    retries: int = _CALL_RETRY_COUNT,
) -> dict[str, Any]:
    """Invoke a single MCP tool via server_entry.py --call.

    Retry logic with exponential backoff guards against transient process
    failures (e.g. OS loader contention under heavy concurrent load).

    Invariants:
      - Never raises; always returns a dict with at least {"ok": bool}.
      - Uses subprocess.run (not Popen) — no zombie risk.
      - shell=False — no shell-injection surface.
    """
    entry_script = workspace_root / ".agent" / "mcp" / "server_entry.py"
    if not entry_script.exists():
        return {
            "ok": False,
            "error": "ENTRY_SCRIPT_NOT_FOUND",
            "reason": f"MCP entry script not found at {entry_script}",
        }

    env = dict(os.environ)
    env["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(workspace_root)
    # Ensure mcp dir is on PYTHONPATH so server_entry imports succeed.
    mcp_dir = str(workspace_root / ".agent" / "mcp")
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{mcp_dir}{os.pathsep}{existing_pp}" if existing_pp else mcp_dir
    if "agent_id" in args:
        env["AGENT_ID"] = str(args["agent_id"])

    last_error: dict[str, Any] = {}
    delay = _CALL_RETRY_BASE_DELAY

    for attempt in range(max(1, retries)):
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
        except subprocess.TimeoutExpired:
            last_error = {
                "ok": False,
                "error": "TIMEOUT",
                "reason": f"Tool call '{tool_name}' timed out after {timeout}s (attempt {attempt + 1}/{retries}).",
            }
            # Timeout is not retriable — the subprocess is already dead.
            break
        except Exception as exc:
            last_error = {
                "ok": False,
                "error": "SUBPROCESS_FAILED",
                "reason": str(exc),
                "attempt": attempt + 1,
            }
            if attempt < retries - 1:
                time.sleep(min(delay, _CALL_RETRY_MAX_DELAY))
                delay *= 2
            continue

        if proc.returncode != 0:
            last_error = {
                "ok": False,
                "error": "NON_ZERO_EXIT_CODE",
                "exit_code": proc.returncode,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
                "reason": f"Tool call exited with status {proc.returncode}.",
            }
            # Non-zero exit may be retriable (e.g. import error under load).
            if attempt < retries - 1:
                time.sleep(min(delay, _CALL_RETRY_MAX_DELAY))
                delay *= 2
            continue

        raw_output = proc.stdout.strip()
        try:
            outer = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            last_error = {
                "ok": False,
                "error": "JSON_DECODE_FAILED",
                "stdout": raw_output,
                "stderr": proc.stderr.strip(),
                "reason": f"Could not parse tool output as JSON: {exc}",
            }
            # JSON decode failure is not retriable — deterministic bug.
            break

        # Unwrap standard MCP content envelope.
        if isinstance(outer, dict) and "content" in outer:
            content_list = outer["content"]
            if content_list and isinstance(content_list, list):
                text_val = content_list[0].get("text", "")
                try:
                    return json.loads(text_val)
                except json.JSONDecodeError:
                    return {"ok": True, "text": text_val}

        return outer

    return last_error


# ─────────────────────────────────────────────────────────────
# Preflight — now with instruction-block auto-repair check
# ─────────────────────────────────────────────────────────────

def run_preflight(
    config: HostLaunchConfig,
    agents_md_path: Path | str | None = None,
    auto_repair_instructions: bool = True,
) -> dict[str, Any]:
    """Run preflight checks and optionally auto-repair missing instruction block.

    New in V2.14:
      - Verifies instruction block is still present in AGENTS.md (or supplied file).
      - If missing AND auto_repair_instructions=True → re-installs automatically.
      - If missing AND auto_repair_instructions=False → returns ACCEPTABLE verdict
        instead of SAFE_CANDIDATE (block missing = reduced confidence).
      - SAFE verdict hardened: never granted on simple /tmp probe alone.
        See policy.py for the full SAFE requirements matrix.
    """
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

    # --- List available MCP tools ---
    try:
        proc = subprocess.run(
            [sys.executable, str(entry_script), "--list-tools"],
            cwd=str(config.workspace_root),
            text=True,
            capture_output=True,
            timeout=20,
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

    # --- Classify host capabilities & decide safety verdict ---
    policy = HostPolicy(config.workspace_root)
    capabilities = policy.classify_host_capabilities()
    verdict = policy.decide_host_safety_level(tools_list, capabilities)

    # --- Instruction block presence check ---
    instruction_block_ok: bool | None = None
    instruction_repair_result: dict[str, Any] | None = None
    _agents_md = agents_md_path or (config.workspace_root / "AGENTS.md")
    agents_md = Path(_agents_md)

    if agents_md.exists():
        instruction_block_ok = _instr.verify_instruction_installation(agents_md)
        if not instruction_block_ok:
            if auto_repair_instructions:
                instruction_repair_result = _instr.install_host_instructions(
                    target_file=agents_md,
                    host_type=config.host_type,
                    workspace_root=config.workspace_root,
                )
                if instruction_repair_result.get("ok"):
                    instruction_block_ok = True
            else:
                # Block missing without auto-repair: downgrade verdict confidence.
                if verdict == HostVerdict.SAFE_CANDIDATE:
                    verdict = HostVerdict.ACCEPTABLE
    else:
        # AGENTS.md doesn't exist yet — install it.
        if auto_repair_instructions:
            instruction_repair_result = _instr.install_host_instructions(
                target_file=agents_md,
                host_type=config.host_type,
                workspace_root=config.workspace_root,
            )
            instruction_block_ok = bool(instruction_repair_result and instruction_repair_result.get("ok"))
        else:
            instruction_block_ok = False

    return {
        "ok": True,
        "verdict": verdict,
        "available_tools": tools_list,
        "capabilities": capabilities,
        "instruction_block_ok": instruction_block_ok,
        "instruction_repair_result": instruction_repair_result,
    }


# ─────────────────────────────────────────────────────────────
# Portability check
# ─────────────────────────────────────────────────────────────

def run_portability_check(config: HostLaunchConfig) -> dict[str, Any]:
    """Verify .agent is at the git root. Fail-fast if not."""
    return call_mcp_tool(
        "portability_check",
        {"workspace_root": str(config.workspace_root)},
        config.workspace_root,
        timeout=15.0,
    )


# ─────────────────────────────────────────────────────────────
# Discipline ping
# ─────────────────────────────────────────────────────────────

def run_discipline_ping(
    config: HostLaunchConfig,
    phase: str = "",
    resource: str = "",
) -> dict[str, Any]:
    return call_mcp_tool(
        "discipline_ping",
        {"agent_id": config.agent_id, "phase": phase, "resource": resource},
        config.workspace_root,
    )


# ─────────────────────────────────────────────────────────────
# Pre-action guard + auto-follow
# ─────────────────────────────────────────────────────────────

def run_pre_action_guard(
    config: HostLaunchConfig,
    request: str,
    intent: str,
    resource: str,
    planned_action: str,
    task_id: str = "",
    context_token: str = "",
) -> dict[str, Any]:
    """Call pre_action_guard once and return the raw result."""
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
    """Run pre_action_guard and auto-execute safe must_call steps.

    Design rationale:
      A small LLM may see 'must_call: ["before_task", "discipline_ping"]' and
      not execute them, leaving the guard loop stuck. This function closes the
      loop autonomously: it calls the guard, checks the must_call list, executes
      each *safe* tool automatically, then re-calls the guard until:
        - verdict == PRE_ACTION_GUARD_OK  → return the lease + full trace, OR
        - a must_call tool is NOT in _SAFE_MUST_CALL_TOOLS → stop and return the
          result for the human / large LLM to handle, OR
        - max_iterations exceeded → return a clear error.

    Anti infinite-loop guarantees:
      1. max_iterations hard cap (default 8).
      2. Only tools in _SAFE_MUST_CALL_TOOLS are auto-executed.
      3. Any tool call that returns ok=False immediately aborts.
      4. We track the set of tools already called; if must_call contains a
         tool we already called, we stop (idempotency assumption failed).

    Returns:
      dict with "verdict" in {"PRE_ACTION_GUARD_OK", "MANUAL_INTERVENTION_REQUIRED",
      "AUTO_FOLLOW_MAX_ITERATIONS", "AUTO_FOLLOW_TOOL_FAILED"} plus "trace" list
      of all steps taken.
    """
    if max_iterations < 1:
        max_iterations = 1

    trace: list[dict[str, Any]] = []
    already_called: set[str] = set()
    _task_id = task_id or config.task_id
    _ctx = context_token or config.context_token

    for iteration in range(max_iterations):
        guard_result = run_pre_action_guard(
            config=config,
            request=request,
            intent=intent,
            resource=resource,
            planned_action=planned_action,
            task_id=_task_id,
            context_token=_ctx,
        )
        trace.append({"step": f"guard_{iteration}", "result": guard_result})

        if not guard_result.get("ok"):
            return {
                "ok": False,
                "verdict": "AUTO_FOLLOW_GUARD_FAILED",
                "reason": guard_result.get("reason") or guard_result.get("error"),
                "guard_result": guard_result,
                "trace": trace,
            }

        guard_verdict = guard_result.get("verdict", "")

        if guard_verdict == "PRE_ACTION_GUARD_OK":
            return {
                "ok": True,
                "verdict": "PRE_ACTION_GUARD_OK",
                "action_lease_id": guard_result.get("action_lease_id", ""),
                "lease": guard_result.get("lease", {}),
                "iterations": iteration + 1,
                "trace": trace,
            }

        must_call: list[str] = guard_result.get("must_call", []) or []
        if not must_call:
            # Guard rejected but gave no must_call — needs human decision.
            return {
                "ok": False,
                "verdict": "MANUAL_INTERVENTION_REQUIRED",
                "reason": f"Guard returned '{guard_verdict}' with no must_call steps.",
                "guard_result": guard_result,
                "trace": trace,
            }

        # Execute each must_call step if safe.
        for tool in must_call:
            if tool not in _SAFE_MUST_CALL_TOOLS:
                return {
                    "ok": False,
                    "verdict": "MANUAL_INTERVENTION_REQUIRED",
                    "reason": f"must_call tool '{tool}' requires manual execution (not in auto-follow safe list).",
                    "guard_result": guard_result,
                    "trace": trace,
                }

            if tool in already_called:
                return {
                    "ok": False,
                    "verdict": "AUTO_FOLLOW_CYCLE_DETECTED",
                    "reason": f"Tool '{tool}' was already auto-called in this session — aborting to prevent loop.",
                    "trace": trace,
                }

            # Build minimal args for each safe tool.
            tool_args: dict[str, Any] = {"agent_id": config.agent_id}
            if _task_id:
                tool_args["task_id"] = _task_id
            if resource:
                tool_args["resource"] = resource
            if _ctx and tool in {"resume_task_context", "before_task"}:
                tool_args["context_token"] = _ctx

            step_result = call_mcp_tool(tool, tool_args, config.workspace_root)
            already_called.add(tool)
            trace.append({"step": f"auto_{tool}", "result": step_result})

            if not step_result.get("ok"):
                return {
                    "ok": False,
                    "verdict": "AUTO_FOLLOW_TOOL_FAILED",
                    "failed_tool": tool,
                    "tool_result": step_result,
                    "trace": trace,
                }

    return {
        "ok": False,
        "verdict": "AUTO_FOLLOW_MAX_ITERATIONS",
        "reason": f"Reached max {max_iterations} iterations without PRE_ACTION_GUARD_OK.",
        "trace": trace,
    }


# ─────────────────────────────────────────────────────────────
# Lease extend — prevents ACTION_LEASE_EXPIRED mid-operation
# ─────────────────────────────────────────────────────────────

def run_lease_extend(
    config: HostLaunchConfig,
    lease_id: str,
    extend_seconds: int | None = None,
) -> dict[str, Any]:
    """Extend an active action lease TTL.

    Call BEFORE the lease expires when an operation takes longer than expected
    (large file writes, slow tool calls, African-latency conditions, etc.).

    Prevents the agent from being blocked mid-operation and forced to restart
    from workflow_next. The discipline module enforces hard caps on total
    extension count and cumulative TTL to prevent infinite-loop via extends.
    """
    if not lease_id:
        return {"ok": False, "error": "LEASE_ID_REQUIRED", "reason": "lease_id must be provided."}

    args: dict[str, Any] = {
        "lease_id": lease_id,
        "agent_id": config.agent_id,
    }
    if extend_seconds is not None:
        args["extend_seconds"] = int(extend_seconds)

    return call_mcp_tool("lease_extend", args, config.workspace_root)


# ─────────────────────────────────────────────────────────────
# Resource locks — multi-agent concurrency guard
# ─────────────────────────────────────────────────────────────

def run_resource_lock_claim(
    config: HostLaunchConfig,
    resource: str,
    task_id: str = "",
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Claim an exclusive resource lock for this agent.

    Prevents N orchestrator agents from writing to the same resource
    concurrently. Lock is separate from action leases so it can be held across
    multiple apply_patch calls within a single task.
    """
    if not resource:
        return {"ok": False, "error": "RESOURCE_REQUIRED", "reason": "resource must be provided."}

    args: dict[str, Any] = {
        "agent_id": config.agent_id,
        "resource": resource,
        "task_id": task_id or config.task_id,
    }
    if ttl_seconds is not None:
        args["ttl_seconds"] = int(ttl_seconds)

    return call_mcp_tool("resource_lock_claim", args, config.workspace_root)


def run_resource_lock_release(
    config: HostLaunchConfig,
    resource: str,
) -> dict[str, Any]:
    """Release a previously claimed resource lock."""
    if not resource:
        return {"ok": False, "error": "RESOURCE_REQUIRED", "reason": "resource must be provided."}
    return call_mcp_tool(
        "resource_lock_release",
        {"agent_id": config.agent_id, "resource": resource},
        config.workspace_root,
    )


def run_resource_lock_status(
    config: HostLaunchConfig,
    resource: str,
) -> dict[str, Any]:
    """Query lock status for a resource without mutating anything."""
    if not resource:
        return {"ok": False, "error": "RESOURCE_REQUIRED", "reason": "resource must be provided."}
    return call_mcp_tool(
        "resource_lock_status",
        {"resource": resource},
        config.workspace_root,
    )


# ─────────────────────────────────────────────────────────────
# Workspace audit
# ─────────────────────────────────────────────────────────────

def run_workspace_audit(
    config: HostLaunchConfig,
    task_id: str = "",
    resource: str = "",
) -> dict[str, Any]:
    """Audit workspace for direct-write bypasses (tracked + untracked files)."""
    return call_mcp_tool(
        "workspace_audit",
        {
            "agent_id": config.agent_id,
            "task_id": task_id or config.task_id,
            "resource": resource,
        },
        config.workspace_root,
    )
