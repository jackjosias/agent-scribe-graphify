from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class HostVerdict:
    UNSAFE = "UNSAFE"
    ACCEPTABLE = "ACCEPTABLE"
    SAFE_CANDIDATE = "SAFE_CANDIDATE"
    SAFE = "SAFE"


class HostPolicy:
    def __init__(self, workspace_root: Path | str | None = None) -> None:
        if workspace_root is None:
            self.workspace_root = Path(os.getcwd()).resolve()
        else:
            self.workspace_root = Path(workspace_root).resolve()

    def get_required_tools(self) -> list[str]:
        return [
            "discipline_ping",
            "pre_action_guard",
            "workspace_audit",
            "workflow_next",
            "workflow_snapshot",
            "batch_file_hash",
            "resume_task_context",
        ]

    def validate_mcp_tools(self, available_tools: list[str]) -> bool:
        required = self.get_required_tools()
        return all(tool in available_tools for tool in required)

    def classify_host_capabilities(self) -> dict[str, Any]:
        # Detect if direct filesystem writes or execution is possible outside sandbox.
        # We can perform a probe to write to a temporary file in /tmp or outside the workspace.
        direct_fs_write = False
        try:
            probe_path = Path("/tmp") / f"probe_{os.getpid()}.txt"
            probe_path.write_text("probe", encoding="utf-8")
            if probe_path.exists():
                direct_fs_write = True
                probe_path.unlink()
        except Exception:
            direct_fs_write = False

        # In standard hosts, if we run Python scripts directly, we have full shell access.
        shell_access = True
        try:
            # Check if we can run python / git directly.
            import subprocess
            res = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5)
            shell_access = res.returncode == 0
        except Exception:
            shell_access = False

        return {
            "direct_fs_write": direct_fs_write,
            "shell_access": shell_access,
            "sandbox_active": not direct_fs_write,
        }

    def decide_host_safety_level(self, available_mcp_tools: list[str], capabilities: dict[str, Any]) -> str:
        # If required tools are missing, the host is UNSAFE.
        if not self.validate_mcp_tools(available_mcp_tools):
            return HostVerdict.UNSAFE

        # If direct file writing is possible outside sandbox, it cannot be SAFE absolute.
        # It is SAFE_CANDIDATE if all V2.12 controls are active.
        if capabilities.get("direct_fs_write", True):
            return HostVerdict.SAFE_CANDIDATE

        # If sandboxed and no direct fs writes, we are SAFE.
        return HostVerdict.SAFE
