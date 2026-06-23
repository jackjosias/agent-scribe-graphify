from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .templates import render_minimal_host_instructions


def is_path_safe(target_file: Path, workspace_root: Path) -> bool:
    try:
        resolved_target = Path(target_file).resolve()
        resolved_root = Path(workspace_root).resolve()
        # Check if target is inside workspace_root
        return resolved_root in resolved_target.parents or resolved_target == resolved_root
    except Exception:
        return False


def remove_old_marked_block(content: str) -> str:
    pattern = re.compile(
        r"<!-- agent-scribe-graphify:auto-guard:start -->.*?<!-- agent-scribe-graphify:auto-guard:end -->",
        re.DOTALL,
    )
    return pattern.sub("", content).strip()


def update_marked_block(content: str, block: str) -> str:
    pattern = re.compile(
        r"<!-- agent-scribe-graphify:auto-guard:start -->.*?<!-- agent-scribe-graphify:auto-guard:end -->",
        re.DOTALL,
    )
    if pattern.search(content):
        return pattern.sub(block, content)
    else:
        if content.strip():
            return f"{content.rstrip()}\n\n{block}\n"
        return f"{block}\n"


def verify_instruction_installation(target_file: Path) -> bool:
    if not target_file.exists():
        return False
    try:
        content = target_file.read_text(encoding="utf-8")
        has_start = "<!-- agent-scribe-graphify:auto-guard:start -->" in content
        has_end = "<!-- agent-scribe-graphify:auto-guard:end -->" in content
        return has_start and has_end
    except Exception:
        return False


def install_host_instructions(
    target_file: Path | str,
    host_type: str,
    workspace_root: Path | str | None = None,
) -> dict[str, Any]:
    target_path = Path(target_file)

    if workspace_root is not None:
        root_path = Path(workspace_root)
        if not is_path_safe(target_path, root_path):
            raise ValueError(f"Path traversal detected: {target_file} is outside workspace {workspace_root}")

    new_block = render_minimal_host_instructions(host_type)

    # Read existing content if file exists
    original_content = ""
    existed = target_path.exists()
    if existed:
        try:
            original_content = target_path.read_text(encoding="utf-8")
        except Exception as exc:
            return {
                "ok": False,
                "error": "READ_FAILED",
                "reason": f"Could not read {target_file}: {exc}",
            }

    updated_content = update_marked_block(original_content, new_block)

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(updated_content, encoding="utf-8")
    except Exception as exc:
        return {
            "ok": False,
            "error": "WRITE_FAILED",
            "reason": f"Could not write to {target_file}: {exc}",
        }

    return {
        "ok": True,
        "existed": existed,
        "installed_at": str(target_path.resolve()),
        "host_type": host_type,
    }
