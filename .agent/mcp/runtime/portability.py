"""portability.py — Cross-platform .agent root validator.

Design rationale:
    The user guarantees that .agent will ALWAYS be at the project root.
    This module enforces that guarantee and fails fast with a clear, actionable
    error message when the assumption is violated — instead of silently operating
    on the wrong directory.

    Cross-platform: Linux, macOS, Windows (Python pathlib + subprocess git).
    No external dependencies beyond the standard library.

Portability guarantee (Fix #1):
    agent.json MUST NOT store absolute paths. The workspace root is always
    derived at runtime from one of:
      1. AGENT_SCRIBE_GRAPHIFY_ROOT env var.
      2. __file__ location (this file lives at .agent/mcp/runtime/portability.py,
         so .agent is 3 parents up, and the project root is 4 parents up).
      3. Git toplevel (cross-platform, timeout-guarded).
      4. cwd (last resort).

    This means copying .agent/ to any project on any machine produces a working
    installation without editing any configuration file. True portability, not
    just relocatability via env var.

Fail-fast contract:
    Every public function in this module raises PortabilityError (a subclass of
    RuntimeError) with a human-readable message if the invariant is violated.
    The MCP tools catch this error and return a structured JSON verdict so even
    a small LLM gets a clear "AGENT_NOT_AT_ROOT" signal with the corrective action.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

AGENT_JSON_FILENAME = "agent.json"
AGENT_JSON_SCHEMA_VERSION = "2.14"
ENV_ROOT_KEY = "AGENT_SCRIBE_GRAPHIFY_ROOT"

# Maximum directory levels to traverse upward when searching for git root.
_MAX_TRAVERSE_DEPTH = 20


# ─────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────

class PortabilityError(RuntimeError):
    """Raised when the .agent directory is not at the expected project root.

    Always carries a structured dict so callers can serialise to JSON cleanly.
    """
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.code,
            "ok": False,
            "message": self.message,
            "details": self.details,
        }


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────

def _git_toplevel(start: Path, timeout: float = 5.0) -> Path | None:
    """Return the git repository root containing *start*, or None.

    Works on Linux / macOS / Windows as long as git is on PATH.
    Timeout prevents hanging in network-mounted or exotic environments.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        if proc.returncode == 0:
            return Path(proc.stdout.strip()).resolve()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _find_agent_dir_upward(start: Path) -> Path | None:
    """Traverse upward from *start* looking for a directory that contains .agent/.

    Returns the first directory that contains .agent/, or None if not found within
    MAX_TRAVERSE_DEPTH levels.
    """
    current = start.resolve()
    for _ in range(_MAX_TRAVERSE_DEPTH):
        candidate = current / ".agent"
        if candidate.is_dir():
            return current
        parent = current.parent
        if parent == current:
            break  # Filesystem root reached.
        current = parent
    return None


def _read_agent_json(agent_dir: Path) -> dict[str, Any]:
    """Read and parse .agent/agent.json if it exists, else return {}."""
    path = agent_dir / AGENT_JSON_FILENAME
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_agent_json(agent_dir: Path, data: dict[str, Any]) -> None:
    """Atomically write .agent/agent.json using a temp-file + replace pattern.

    Atomic on POSIX (os.replace is rename(2)).
    On Windows, os.replace is best-effort atomic since Python 3.3+.
    """
    path = agent_dir / AGENT_JSON_FILENAME
    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def get_workspace_root() -> Path:
    """Resolve the project workspace root with highest confidence.

    Fix #1 — True portability:
    workspace_root is NEVER read from agent.json (it is not stored there).
    Resolution order (first found wins):

    1. AGENT_SCRIBE_GRAPHIFY_ROOT environment variable.
       Set by server_entry.py at launch time, or by the user for dev overrides.

    2. __file__-relative traversal.
       This file is at .agent/mcp/runtime/portability.py.
       Therefore: parents[3] == project root on any machine, any OS.
       This works even without git, env var, or any config file.

    3. Upward traversal from cwd looking for a directory containing .agent/.

    4. git rev-parse --show-toplevel from cwd (requires git on PATH).

    5. cwd itself (last resort, least trusted).

    Does NOT raise — always returns a Path. Validation is separate (validate_root).
    """
    # Priority 1: explicit env override (set by server_entry.py, launcher.py).
    env_root = os.environ.get(ENV_ROOT_KEY, "").strip()
    if env_root:
        return Path(env_root).resolve()

    # Priority 2: __file__-relative (most reliable, works without git or config).
    # .agent/mcp/runtime/portability.py → parents: [0]=runtime, [1]=mcp,
    # [2]=.agent, [3]=project_root
    this_file = Path(__file__).resolve()
    try:
        inferred_root = this_file.parents[3]
        agent_dir = inferred_root / ".agent"
        if agent_dir.is_dir():
            # Do NOT read workspace_root from agent.json — it is not stored.
            return inferred_root
    except IndexError:
        pass

    # Priority 3: traverse upward from cwd.
    cwd = Path.cwd().resolve()
    found = _find_agent_dir_upward(cwd)
    if found:
        return found

    # Priority 4: git toplevel.
    git_root = _git_toplevel(cwd)
    if git_root:
        return git_root

    # Priority 5: cwd (last resort).
    return cwd


def validate_root(workspace_root: Path | str | None = None) -> dict[str, Any]:
    """Validate that .agent is at the project root.

    Returns a structured dict:
        ok: True  → validation passed
        ok: False → validation failed (details explains why)

    Does NOT raise. Callers that want fail-fast behaviour should check ok=False
    and raise PortabilityError themselves, or call assert_root_valid().

    Checks performed (all cross-platform):
    1. .agent/ directory exists at workspace_root.
    2. workspace_root matches git toplevel (if git available).
    3. .agent/agent.json exists and has correct schema_version (if present).
    4. server_entry.py is at .agent/mcp/server_entry.py.
    """
    if workspace_root is None:
        workspace_root = get_workspace_root()
    root = Path(workspace_root).resolve()
    agent_dir = root / ".agent"

    # Check 1: .agent/ must exist.
    if not agent_dir.is_dir():
        return {
            "ok": False,
            "verdict": "AGENT_DIR_MISSING",
            "workspace_root": str(root),
            "message": (
                f".agent/ directory not found at {root}. "
                "Copy the .agent/ folder to the root of your project before running."
            ),
            "corrective_action": f"Copy .agent/ to {root}/",
        }

    # Check 2: Git toplevel validation (best-effort, non-blocking if git unavailable).
    git_root = _git_toplevel(root)
    git_mismatch = False
    git_mismatch_detail: str = ""
    if git_root is not None and git_root != root:
        git_mismatch = True
        git_mismatch_detail = (
            f"Git root is {git_root} but .agent is under {root}. "
            "This may mean .agent is in a subdirectory. "
            "Move .agent/ to {git_root}/ for full portability.".format(git_root=git_root)
        )

    # Check 3: server_entry.py presence.
    entry_script = agent_dir / "mcp" / "server_entry.py"
    if not entry_script.is_file():
        return {
            "ok": False,
            "verdict": "AGENT_ENTRY_MISSING",
            "workspace_root": str(root),
            "message": (
                f"Missing {entry_script}. The .agent/mcp/ bundle may be incomplete. "
                "Verify the full .agent directory was copied correctly."
            ),
            "corrective_action": "Re-copy .agent/mcp/ from the canonical bundle.",
        }

    # Check 4: agent.json schema version (informational, not blocking).
    aj = _read_agent_json(agent_dir)
    agent_json_present = bool(aj)
    schema_ok = aj.get("schema_version") == AGENT_JSON_SCHEMA_VERSION if aj else False

    result: dict[str, Any] = {
        "ok": True,
        "verdict": "ROOT_VALID",
        "workspace_root": str(root),
        "agent_dir": str(agent_dir),
        "git_root": str(git_root) if git_root else None,
        "git_mismatch": git_mismatch,
        "agent_json_present": agent_json_present,
        "agent_json_schema_ok": schema_ok,
        "schema_version": AGENT_JSON_SCHEMA_VERSION,
    }

    if git_mismatch:
        result["ok"] = False
        result["verdict"] = "AGENT_NOT_AT_GIT_ROOT"
        result["message"] = git_mismatch_detail
        result["corrective_action"] = (
            f"Move .agent/ from {root}/ to {git_root}/ and re-launch."
        )

    return result


def assert_root_valid(workspace_root: Path | str | None = None) -> Path:
    """Validate root and raise PortabilityError if invalid.

    Returns the resolved workspace root Path on success.
    This is the fail-fast entry point used by server_entry.py and launcher.py.
    """
    result = validate_root(workspace_root)
    if not result["ok"]:
        raise PortabilityError(
            code=result["verdict"],
            message=result.get("message", "Portability check failed."),
            details=result,
        )
    return Path(result["workspace_root"]).resolve()


def init_agent_json(workspace_root: Path | str, project_name: str = "") -> dict[str, Any]:
    """Create or update .agent/agent.json with portability metadata.

    Fix #1 — TRUE PORTABILITY:
    agent.json MUST NOT contain machine-specific absolute paths.
    The workspace_root is intentionally ABSENT from the stored data.
    Runtime resolution happens via get_workspace_root() every time,
    using __file__ location and env var — never a cached absolute path.

    Stored fields: schema_version, project_root_strategy, agent_root,
    project_name, created_at, updated_at, portable, platform.

    NOT stored: workspace_root, any absolute path.

    Idempotent: if the file already exists, it is only updated if schema_version
    is outdated or project_name has changed.

    Returns the written data dict.
    """
    root = Path(workspace_root).resolve()
    agent_dir = root / ".agent"
    if not agent_dir.is_dir():
        raise PortabilityError(
            "AGENT_DIR_MISSING",
            f".agent/ not found at {root}. Cannot initialise agent.json.",
        )

    existing = _read_agent_json(agent_dir)
    created_at = existing.get(
        "created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    effective_project_name = project_name or existing.get("project_name", root.name)

    # Fix #1: NO absolute workspace_root stored. Only portable metadata.
    data: dict[str, Any] = {
        "schema_version": AGENT_JSON_SCHEMA_VERSION,
        "project_root_strategy": "file_parent_traversal",
        "agent_root": ".agent",
        # workspace_root is intentionally ABSENT — derived at runtime.
        "project_name": effective_project_name,
        "created_at": created_at,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "portable": True,
        "portable_note": (
            "workspace_root is NOT stored here. Copy .agent/ to any project "
            "and it will resolve its root from __file__ location or "
            "AGENT_SCRIBE_GRAPHIFY_ROOT env var."
        ),
    }

    # Migrate existing files that contain an absolute workspace_root.
    if "workspace_root" in existing:
        # Remove the absolute path from the file — it is now computed at runtime.
        existing.pop("workspace_root", None)

    # Write only if something meaningful changed.
    needs_write = (
        existing.get("schema_version") != AGENT_JSON_SCHEMA_VERSION
        or existing.get("portable") is not True
        or "workspace_root" in existing  # legacy cleanup
        or (project_name and existing.get("project_name") != effective_project_name)
    )
    if needs_write:
        _write_agent_json(agent_dir, data)

    return data


def portability_check(workspace_root: Path | str | None = None) -> dict[str, Any]:
    """Full portability report: validate root + agent.json + entry script.

    Returns a structured report suitable for MCP tool output.
    This is the function called by the `portability_check` MCP tool.
    """
    if workspace_root is None:
        workspace_root = get_workspace_root()

    root = Path(workspace_root).resolve()
    validation = validate_root(root)
    agent_dir = root / ".agent"

    report: dict[str, Any] = {
        **validation,
        "checks": {
            "agent_dir_exists": agent_dir.is_dir(),
            "entry_script_exists": (agent_dir / "mcp" / "server_entry.py").is_file(),
            "agent_json_exists": (agent_dir / AGENT_JSON_FILENAME).is_file(),
            "scribe_cli_exists": (agent_dir / "workflow" / "scribe" / "scribe").is_file(),
            "host_adapter_exists": (agent_dir / "host_adapter" / "policy.py").is_file(),
        },
        "platform": sys.platform,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }

    # Provide a simple summary verdict for small LLMs.
    if validation["ok"]:
        passing = sum(1 for v in report["checks"].values() if v)
        total = len(report["checks"])
        report["summary"] = f"PORTABLE OK ({passing}/{total} checks passing)"
    else:
        report["summary"] = f"NOT PORTABLE: {validation.get('verdict')} — {validation.get('message', '')}"

    return report
