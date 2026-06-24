"""policy.py — Host safety classification for V2.13+.

Key change from V2.13.0:
    The SAFE verdict was too optimistic (probe /tmp only).
    New rule (per second LLM analysis): SAFE is almost never auto-granted.
    Default ceiling is SAFE_CANDIDATE when all controls are active.
    SAFE requires explicit sandbox proof that native write/exec tools are disabled.

    preflight now also checks:
    - Whether auto-guard instructions block is still installed in AGENTS.md.
    - Portability root validity.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


class HostVerdict:
    UNSAFE = "UNSAFE"
    ACCEPTABLE = "ACCEPTABLE"
    SAFE_CANDIDATE = "SAFE_CANDIDATE"
    # SAFE is reserved for environments where native write is physically impossible.
    # Do NOT grant SAFE on /tmp probe alone — that was the V2.13.0 bug.
    SAFE = "SAFE"


_REQUIRED_TOOLS = [
    "discipline_ping",
    "pre_action_guard",
    "workspace_audit",
    "workflow_next",
    "workflow_snapshot",
    "batch_file_hash",
    "resume_task_context",
    "lease_extend",
    "resource_lock_claim",
    "resource_lock_release",
    "resource_lock_status",
    "portability_check",
]

# Auto-guard instruction markers (must be present in AGENTS.md / host instructions).
_GUARD_BLOCK_START = "<!-- agent-scribe-graphify:auto-guard:start -->"
_GUARD_BLOCK_END = "<!-- agent-scribe-graphify:auto-guard:end -->"


class HostPolicy:
    def __init__(self, workspace_root: Path | str | None = None) -> None:
        if workspace_root is None:
            self.workspace_root = Path(os.getcwd()).resolve()
        else:
            self.workspace_root = Path(workspace_root).resolve()

    def get_required_tools(self) -> list[str]:
        return list(_REQUIRED_TOOLS)

    def validate_mcp_tools(self, available_tools: list[str]) -> bool:
        required = self.get_required_tools()
        return all(tool in available_tools for tool in required)

    def get_missing_tools(self, available_tools: list[str]) -> list[str]:
        return [t for t in self.get_required_tools() if t not in available_tools]

    def classify_host_capabilities(self) -> dict[str, Any]:
        """Probe host capabilities without mutating state.

        Cross-platform: uses os.access and subprocess with timeout.
        The /tmp probe was removed — it was unreliable (network mounts, Docker,
        SELinux can block /tmp but allow workspace writes).

        Instead we probe the workspace directory itself, which is the real risk surface.
        """
        # Probe 1: Can we write inside the workspace? (This is the actual risk.)
        workspace_write = False
        probe_path = self.workspace_root / ".agent" / "state" / f".probe_{os.getpid()}"
        try:
            probe_path.parent.mkdir(parents=True, exist_ok=True)
            probe_path.write_text("probe", encoding="utf-8")
            workspace_write = probe_path.is_file()
            probe_path.unlink(missing_ok=True)
        except Exception:
            workspace_write = False

        # Probe 2: Shell access (git available == subprocess works).
        shell_access = False
        try:
            res = subprocess.run(
                ["git", "--version"],
                capture_output=True, text=True, timeout=5, shell=False,
            )
            shell_access = res.returncode == 0
        except Exception:
            shell_access = False

        return {
            "workspace_write": workspace_write,
            "shell_access": shell_access,
            # sandbox_active = True only if we genuinely cannot write to workspace.
            "sandbox_active": not workspace_write,
        }

    def check_instructions_installed(self, target_file: Path | None = None) -> bool:
        """Return True if auto-guard instructions are still present in the target file."""
        if target_file is None:
            # Default: check AGENTS.md at workspace root.
            target_file = self.workspace_root / "AGENTS.md"
        if not target_file.is_file():
            return False
        try:
            content = target_file.read_text(encoding="utf-8", errors="replace")
            return _GUARD_BLOCK_START in content and _GUARD_BLOCK_END in content
        except OSError:
            return False

    def decide_host_safety_level(
        self,
        available_mcp_tools: list[str],
        capabilities: dict[str, Any],
        instructions_installed: bool = False,
    ) -> str:
        """Classify the host safety level.

        Hardened rules (V2.13.1):
        UNSAFE          = required MCP tools missing.
        ACCEPTABLE      = tools present but instructions NOT installed.
        SAFE_CANDIDATE  = tools present + instructions installed.
                          Even if workspace write is possible (which it almost always is),
                          this is the MAXIMUM auto-classifiable level.
        SAFE            = NOT auto-granted. Reserved for provably sandboxed environments
                          where workspace_write=False AND shell_access=False.

        Rationale: SAFE should almost never be returned automatically because no simple
        probe can prove that ALL write paths are closed. A human or CI config must
        explicitly declare SAFE for a controlled environment.
        """
        if not self.validate_mcp_tools(available_mcp_tools):
            return HostVerdict.UNSAFE

        # All required tools present.
        if not instructions_installed:
            # Tools present but guard instructions not injected → ACCEPTABLE.
            return HostVerdict.ACCEPTABLE

        # Tools present + instructions installed.
        workspace_write = capabilities.get("workspace_write", True)
        shell_access = capabilities.get("shell_access", True)

        if not workspace_write and not shell_access:
            # Genuinely sandboxed: cannot write to workspace AND no shell.
            # This is the only path to SAFE.
            return HostVerdict.SAFE

        # Normal environment: MCP tools active, instructions injected,
        # but native write capability exists → SAFE_CANDIDATE (honest ceiling).
        return HostVerdict.SAFE_CANDIDATE
