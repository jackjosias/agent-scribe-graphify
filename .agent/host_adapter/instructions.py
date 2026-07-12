from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from .templates import render_minimal_host_instructions

_START = "<!-- agent-scribe-graphify:auto-guard:start -->"
_END = "<!-- agent-scribe-graphify:auto-guard:end -->"
_BLOCK_PATTERN = re.compile(re.escape(_START) + r".*?" + re.escape(_END), re.DOTALL)


def is_path_safe(target_file: Path, workspace_root: Path) -> bool:
    try:
        target = Path(target_file).resolve(strict=False)
        root = Path(workspace_root).resolve(strict=True)
        return target == root or root in target.parents
    except (OSError, RuntimeError):
        return False


def remove_old_marked_block(content: str) -> str:
    return _BLOCK_PATTERN.sub("", content).strip()


def update_marked_block(content: str, block: str) -> str:
    if _BLOCK_PATTERN.search(content):
        return _BLOCK_PATTERN.sub(block, content)
    if content.strip():
        return f"{content.rstrip()}\n\n{block}\n"
    return f"{block}\n"


def verify_instruction_installation(target_file: Path) -> bool:
    try:
        content = target_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return content.count(_START) == 1 and content.count(_END) == 1 and _BLOCK_PATTERN.search(content) is not None


def _atomic_text_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def install_host_instructions(
    target_file: Path | str,
    host_type: str,
    workspace_root: Path | str | None = None,
) -> dict[str, Any]:
    target = Path(target_file)
    if workspace_root is not None and not is_path_safe(target, Path(workspace_root)):
        raise ValueError(f"Path traversal detected: {target_file} is outside workspace {workspace_root}")

    existed = target.exists()
    try:
        original = target.read_text(encoding="utf-8") if existed else ""
    except OSError as exc:
        return {"ok": False, "error": "READ_FAILED", "reason": f"Could not read {target_file}: {exc}"}

    updated = update_marked_block(original, render_minimal_host_instructions(host_type))
    changed = updated != original
    try:
        if changed:
            _atomic_text_write(target, updated)
    except OSError as exc:
        return {"ok": False, "error": "WRITE_FAILED", "reason": f"Could not write to {target_file}: {exc}"}

    if not verify_instruction_installation(target):
        return {
            "ok": False,
            "error": "VERIFY_FAILED",
            "reason": f"Managed host instruction block was not installed exactly once in {target}",
        }
    return {
        "ok": True,
        "existed": existed,
        "changed": changed,
        "installed_at": str(target.resolve()),
        "host_type": host_type,
    }
