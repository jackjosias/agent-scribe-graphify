"""Host safety policy for the V2.16 TENOR operating layer."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


class HostVerdict:
    UNSAFE = "UNSAFE"
    ACCEPTABLE = "ACCEPTABLE"
    SAFE_CANDIDATE = "SAFE_CANDIDATE"
    SAFE = "SAFE"


_REQUIRED_TOOLS = (
    "file_hash",
    "tenor_init_bridge",
    "portability_check",
    "graphify_required_check",
    "graphify_project_build",
    "tenor_task_start",
    "tenor_apply_changeset",
    "tenor_activity",
    "tenor_task_control",
)
_GUARD_BLOCK_START = "<!-- agent-scribe-graphify:auto-guard:start -->"
_GUARD_BLOCK_END = "<!-- agent-scribe-graphify:auto-guard:end -->"


class HostPolicy:
    def __init__(self, workspace_root: Path | str | None = None) -> None:
        self.workspace_root = Path(workspace_root or os.getcwd()).resolve()

    def get_required_tools(self) -> list[str]:
        return list(_REQUIRED_TOOLS)

    def validate_mcp_tools(self, available_tools: list[str]) -> bool:
        present = {str(tool) for tool in available_tools}
        return all(tool in present for tool in _REQUIRED_TOOLS)

    def get_missing_tools(self, available_tools: list[str]) -> list[str]:
        present = {str(tool) for tool in available_tools}
        return [tool for tool in _REQUIRED_TOOLS if tool not in present]

    def classify_host_capabilities(self) -> dict[str, Any]:
        """Probe the real workspace risk surface with bounded, cleaned operations."""
        workspace_write = False
        probe = self.workspace_root / ".agent" / "state" / f".host-probe-{os.getpid()}"
        try:
            probe.parent.mkdir(parents=True, exist_ok=True)
            with probe.open("x", encoding="utf-8") as handle:
                handle.write("probe\n")
                handle.flush()
                os.fsync(handle.fileno())
            workspace_write = probe.is_file()
        except (FileExistsError, OSError):
            workspace_write = False
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass

        shell_access = False
        try:
            result = subprocess.run(
                ["git", "--version"],
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
                check=False,
            )
            shell_access = result.returncode == 0
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            shell_access = False

        return {
            "workspace_write": workspace_write,
            "shell_access": shell_access,
            "sandbox_active": not workspace_write and not shell_access,
        }

    def check_instructions_installed(self, target_file: Path | None = None) -> bool:
        path = target_file or (self.workspace_root / "AGENTS.md")
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return _GUARD_BLOCK_START in content and _GUARD_BLOCK_END in content

    def decide_host_safety_level(
        self,
        available_mcp_tools: list[str],
        capabilities: dict[str, Any],
        instructions_installed: bool = False,
    ) -> str:
        if not self.validate_mcp_tools(available_mcp_tools):
            return HostVerdict.UNSAFE
        if not instructions_installed:
            return HostVerdict.ACCEPTABLE
        if not capabilities.get("workspace_write", True) and not capabilities.get("shell_access", True):
            return HostVerdict.SAFE
        return HostVerdict.SAFE_CANDIDATE
