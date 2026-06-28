"""graphify_guard.py — Graphify Mandatory Guard (V2.15).

Doctrine:
    Graphify is a MANDATORY dependency of the agentic workflow.
    Graphify + SCRIBE = core structural + causal memory.
    If Graphify is absent → blocking state, no write/refactor/delete.
    No silent fallback to grep/ripgrep.

Design:
    Pure functions + subprocess calls with timeouts.
    Stateless — no shared state, safe under concurrent load.
    Cross-platform: Linux, macOS, Windows.
    Every public function returns a structured dict with "ok" and "verdict" keys.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .state_paths import prepare_state_dirs
except Exception:
    try:
        from state_paths import prepare_state_dirs  # type: ignore
    except Exception:
        prepare_state_dirs = None  # type: ignore

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

GRAPHIFY_CLI_NAME = "graphify"
GRAPHIFY_OUT_DIR = "graphify-out"
GRAPHIFY_REQUIRED_FILES = ["graph.json", "GRAPH_REPORT.md", "graph.html"]
INSTALL_GUIDE_REL_PATH = ".agent/docs/GRAPHIFY_INSTALL_REQUIRED.md"

# Timeouts for subprocess calls (seconds) — generous for African-latency networks.
_SUBCMD_TIMEOUT = 15
_SUBCMD_RETRY_COUNT = 2
_SUBCMD_RETRY_DELAY = 1.0

# Required Python version for graphifyy.
_MIN_PYTHON = (3, 10)

# Verdict constants — used by callers to pattern-match.
VERDICT_READY = "GRAPHIFY_READY"
VERDICT_BINARY_MISSING = "GRAPHIFY_REQUIRED_NOT_INSTALLED"
VERDICT_OUTPUTS_MISSING = "GRAPHIFY_OUTPUTS_MISSING"
VERDICT_BINARY_FOUND = "GRAPHIFY_BINARY_FOUND"
VERDICT_BINARY_FAILED = "GRAPHIFY_BINARY_CHECK_FAILED"
VERDICT_DIAGNOSTIC_ONLY = "GRAPHIFY_DIAGNOSTIC_ONLY"
VERDICT_GRAPH_INVALID_JSON = "GRAPHIFY_GRAPH_JSON_INVALID"


# ─────────────────────────────────────────────────────────────────────────────
# Exception
# ─────────────────────────────────────────────────────────────────────────────

class GraphifyGuardError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "verdict": self.code, "reason": self.message, "details": self.details}


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _detect_os() -> str:
    """Return normalized OS name: 'linux', 'darwin', 'windows', or 'unknown'."""
    raw = platform.system().lower()
    if raw.startswith("linux"):
        return "linux"
    if raw.startswith("darwin"):
        return "darwin"
    if raw.startswith("windows") or raw.startswith("win"):
        return "windows"
    return "unknown"


def _is_ubuntu_or_debian() -> bool:
    """Best-effort detection of Ubuntu or Debian via /etc/os-release."""
    os_release = Path("/etc/os-release")
    if not os_release.is_file():
        return False
    try:
        text = os_release.read_text(encoding="utf-8", errors="replace")
        return "ubuntu" in text.lower() or "debian" in text.lower()
    except OSError:
        return False


def _safe_subprocess(
    cmd: list[str],
    timeout: float = _SUBCMD_TIMEOUT,
    retries: int = _SUBCMD_RETRY_COUNT,
) -> dict[str, Any]:
    """Run a subprocess with timeout and retry.

    Returns dict with keys:
        ok: bool
        stdout: str
        stderr: str
        returncode: int
        error: str | None  — set only if subprocess could not start or timed out
    """
    last_error: str | None = None
    delay = _SUBCMD_RETRY_DELAY

    for attempt in range(max(1, retries)):
        try:
            proc = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=timeout,
                shell=False,
            )
            return {
                "ok": proc.returncode == 0,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "returncode": proc.returncode,
                "error": None,
            }
        except FileNotFoundError:
            return {
                "ok": False, "stdout": "", "stderr": "",
                "returncode": 127, "error": "command not found",
            }
        except subprocess.TimeoutExpired:
            last_error = f"timeout after {timeout}s"
            break  # Timeout is not retriable — the process is hung.
        except OSError as exc:
            last_error = str(exc)
            if attempt < retries - 1:
                time.sleep(min(delay, 4.0))
                delay *= 2
            continue

    return {
        "ok": False, "stdout": "", "stderr": "",
        "returncode": -1, "error": last_error or "subprocess failed",
    }


def _graphify_on_path() -> Path | None:
    """Return the resolved Path to graphify CLI, or None if not found.

    Uses shutil.which which is cross-platform and respects PATHEXT on Windows.
    """
    resolved = shutil.which(GRAPHIFY_CLI_NAME)
    if resolved is None:
        return None
    try:
        return Path(resolved).resolve()
    except OSError:
        return None


def _write_doc_atomic(path: Path, content: str) -> dict[str, Any]:
    """Write content to path atomically via temp file + rename.

    Returns dict with verdict and path on success, error dict on failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        return {
            "ok": False,
            "verdict": "INSTALL_GUIDE_WRITE_FAILED",
            "reason": str(exc),
            "path": str(path),
        }
    return {
        "ok": True,
        "verdict": "INSTALL_GUIDE_WRITTEN",
        "path": str(path),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Binary detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_graphify_binary() -> bool:
    """Return True if the graphify CLI is available on PATH.

    Cross-platform: uses shutil.which which handles PATHEXT on Windows.
    Thread-safe: pure read-only operation.
    """
    return _graphify_on_path() is not None


def get_graphify_version() -> str | None:
    """Return the graphify version string (e.g. '0.5.0') or None if unavailable.

    Runs `graphify --version` with a timeout. If --version fails, falls back
    to `graphify --help` to confirm the binary is real. Returns the version
    string or None if both commands fail.
    """
    binary = _graphify_on_path()
    if binary is None:
        return None
    result = _safe_subprocess([str(binary), "--version"])
    if result["ok"]:
        version_str = result["stdout"].strip() or result["stderr"].strip()
        return version_str if version_str else None
    help_result = _safe_subprocess([str(binary), "--help"])
    if help_result["ok"]:
        return None
    return None


def _graphify_binary_responsive() -> bool:
    """Return True if the graphify binary responds to --version or --help.

    Binary must be on PATH and respond to at least one of the two probes.
    This prevents a fake/unrelated binary on PATH from being accepted.
    """
    binary = _graphify_on_path()
    if binary is None:
        return False
    version_check = _safe_subprocess([str(binary), "--version"])
    if version_check["ok"]:
        return True
    help_check = _safe_subprocess([str(binary), "--help"])
    return help_check["ok"]


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Installation validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_graphify_installation() -> dict[str, Any]:
    """Check whether graphify CLI is installed and responsive.

    Returns structured dict:
        ok: bool
        verdict: GRAPHIFY_READY | GRAPHIFY_REQUIRED_NOT_INSTALLED | GRAPHIFY_BINARY_CHECK_FAILED | GRAPHIFY_BINARY_FOUND
        binary_path: str | None
        version: str | None
        python_version: str
        platform: str
    """
    binary = _graphify_on_path()
    python_ok = sys.version_info >= _MIN_PYTHON

    base: dict[str, Any] = {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_ok": python_ok,
        "platform": _detect_os(),
        "binary_path": str(binary) if binary else None,
    }

    if binary is None:
        return {
            "ok": False,
            "verdict": VERDICT_BINARY_MISSING,
            "version": None,
            **base,
        }

    responsive = _graphify_binary_responsive()
    if not responsive:
        return {
            "ok": False,
            "verdict": VERDICT_BINARY_FAILED,
            "version": None,
            "reason": (
                f"Binary found at {binary} but does not respond to --version or --help. "
                "This may be a different command shadowing the real graphify CLI."
            ),
            **base,
        }

    version = get_graphify_version()
    return {
        "ok": True,
        "verdict": VERDICT_BINARY_FOUND,
        "version": version,
        **base,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Output validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_graphify_outputs(workspace_root: Path | str | None = None) -> dict[str, Any]:
    """Check that graphify-out/ contains all required output files.

    Checks:
        - graphify-out/ directory exists
        - graph.json exists, is non-empty, and contains valid JSON
          with at least a 'nodes' key (Graphify output structure)
        - GRAPH_REPORT.md exists and is non-empty
        - graph.html exists and is non-empty

    Returns structured dict with per-file status and overall verdict.
    """
    if workspace_root is None:
        workspace_root = Path.cwd()
    root = Path(workspace_root).resolve()
    candidates: list[Path] = []
    if prepare_state_dirs is not None:
        try:
            paths = prepare_state_dirs(root)
            candidates.append(paths["graphify_out"])
        except Exception:
            pass
    candidates.extend([root / GRAPHIFY_OUT_DIR])
    out_dir = next((candidate for candidate in candidates if candidate.is_dir()), candidates[0])

    if not out_dir.is_dir():
        return {
            "ok": False,
            "verdict": VERDICT_OUTPUTS_MISSING,
            "reason": f"'{GRAPHIFY_OUT_DIR}/' directory not found at {out_dir}",
            "files": {},
            "output_dir": str(out_dir),
            "checked_output_dirs": [str(candidate) for candidate in candidates],
        }

    file_status: dict[str, dict[str, Any]] = {}
    all_present = True
    any_present = False
    graph_json_issues: list[str] = []

    for filename in GRAPHIFY_REQUIRED_FILES:
        path = out_dir / filename
        exists = path.is_file()
        size = path.stat().st_size if exists else 0
        non_empty = size > 0
        entry: dict[str, Any] = {
            "exists": exists,
            "size_bytes": size,
            "non_empty": non_empty,
        }
        if exists and non_empty:
            any_present = True

            if filename == "graph.json":
                try:
                    raw = path.read_text(encoding="utf-8", errors="replace")
                    parsed = json.loads(raw)
                    has_nodes = isinstance(parsed, dict) and "nodes" in parsed
                    entry["valid_json"] = True
                    entry["has_nodes_key"] = has_nodes
                    if not has_nodes:
                        graph_json_issues.append("graph.json is valid JSON but missing 'nodes' key")
                except json.JSONDecodeError as exc:
                    entry["valid_json"] = False
                    entry["parse_error"] = str(exc)
                    graph_json_issues.append(f"graph.json is not valid JSON: {exc}")

        file_status[filename] = entry
        if not exists or not non_empty:
            all_present = False

    if all_present and not graph_json_issues:
        return {
            "ok": True,
            "verdict": "GRAPHIFY_OUTPUTS_READY",
            "output_dir": str(out_dir),
            "files": file_status,
        }

    missing_files = [f for f, s in file_status.items() if not s["exists"]]
    empty_files = [f for f, s in file_status.items() if s["exists"] and not s["non_empty"]]

    reason_parts = []
    if missing_files:
        reason_parts.append(f"missing: {', '.join(missing_files)}")
    if empty_files:
        reason_parts.append(f"empty: {', '.join(empty_files)}")
    reason_parts.extend(graph_json_issues)

    if graph_json_issues:
        return {
            "ok": False,
            "verdict": VERDICT_GRAPH_INVALID_JSON,
            "reason": "; ".join(reason_parts),
            "output_dir": str(out_dir),
            "files": file_status,
            "next_actions": [
                f"Run `{GRAPHIFY_CLI_NAME} .` to regenerate the knowledge graph",
                "Check graphify-out/graph.json for corruption",
            ],
        }

    return {
        "ok": False,
        "verdict": VERDICT_OUTPUTS_MISSING,
        "reason": "; ".join(reason_parts) if reason_parts else "graphify-out is incomplete",
        "output_dir": str(out_dir),
        "files": file_status,
        "next_actions": [
            f"Run `{GRAPHIFY_CLI_NAME} .` in the project root",
            f"Verify {GRAPHIFY_OUT_DIR}/graph.json exists",
            f"Verify {GRAPHIFY_OUT_DIR}/GRAPH_REPORT.md exists",
            f"Verify {GRAPHIFY_OUT_DIR}/graph.html exists",
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Install guide rendering
# ─────────────────────────────────────────────────────────────────────────────

def render_graphify_install_guide(
    host_type: str = "unknown",
    os_name: str | None = None,
) -> str:
    """Render a Markdown install guide for Graphify.

    Args:
        host_type: Agent host platform ('opencode', 'codex', 'gemini', 'claude', 'unknown').
        os_name: Override OS detection ('linux', 'darwin', 'windows', 'unknown').
                 If None, auto-detect.

    Returns a complete Markdown string ready to write to a .md file.
    """
    if os_name is None:
        os_name = _detect_os()

    normalized_host = (host_type or "unknown").strip().lower()
    normalized_os = os_name.lower().strip()

    # ── OS-specific install instructions ──────────────────────────────────
    if normalized_os == "linux":
        is_debian_like = _is_ubuntu_or_debian()
        if is_debian_like:
            os_install = """### Ubuntu / Debian

```bash
# Update package index
sudo apt update

# Install Python 3 and pipx
sudo apt install -y python3 python3-pip pipx

# Install uv (recommended)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install graphifyy via uv
uv tool install graphifyy

# Verify installation
graphify --version
```"""
        else:
            os_install = """### Linux (generic)

```bash
# Ensure Python 3.10+ is installed
python3 --version

# Install uv (recommended)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install graphifyy via uv
uv tool install graphifyy

# Alternative via pipx
pipx install graphifyy

# Verify installation
graphify --version
```"""
    elif normalized_os == "darwin":
        os_install = """### macOS

```bash
# Install Python 3.12 and uv via Homebrew
brew install python@3.12 uv

# Install graphifyy via uv
uv tool install graphifyy

# Verify installation
graphify --version
```"""
    elif normalized_os == "windows":
        os_install = """### Windows (PowerShell)

```powershell
# Install uv (package manager for Python)
winget install astral-sh.uv

# Install graphifyy via uv
uv tool install graphifyy

# Verify installation
graphify --version

# Run graphify on your project
graphify .
```"""
    else:
        os_install = """### Cross-platform (generic)

```bash
# Prerequisites: Python 3.10+ installed

# Recommended: install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install graphifyy via uv
uv tool install graphifyy

# Alternative via pipx
pipx install graphifyy

# Last resort via pip
pip install graphifyy

# Verify installation
graphify --version
```"""

    # ── Host-specific platform registration ───────────────────────────────
    host_install: str
    if normalized_host == "opencode":
        host_install = """### OpenCode

```bash
# Register the graphify skill for OpenCode
graphify install --platform opencode

# Or equivalently:
graphify opencode install
```"""
    elif normalized_host == "codex":
        host_install = """### OpenAI Codex CLI

```bash
# Register the graphify skill for Codex
graphify install --platform codex
```"""
    elif normalized_host == "gemini":
        host_install = """### Gemini CLI

```bash
# Register the graphify skill for Gemini CLI
graphify install --platform gemini
```"""
    elif normalized_host in ("claude", "claude code"):
        host_install = """### Claude Code

```bash
# Register the graphify skill for Claude Code
graphify install --platform claude
```"""
    else:
        host_install = """### Host-specific registration

Depending on your agent host, run the appropriate command:

```bash
# OpenCode
graphify install --platform opencode

# OpenAI Codex CLI
graphify install --platform codex

# Gemini CLI
graphify install --platform gemini

# Claude Code
graphify install --platform claude

# Or register for all supported platforms
graphify install --project
```"""

    # ── Build the full guide ──────────────────────────────────────────────
    return f"""# Graphify Installation Guide

> Auto-generated by graphify_guard.py — {time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}

## Why Graphify is required

Graphify is the **structural memory** of this agentic workflow. It transforms
your codebase into a queryable knowledge graph. Without it, the system cannot
safely perform write, refactor, or delete operations because it lacks a
structural map of the project.

**Graphify + SCRIBE = core of the .agent system.**
- Graphify → what/where/how (structural graph)
- SCRIBE   → why/pain/decisions (causal memory)

## Prerequisites

- Python {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}+
- `uv` (recommended) or `pipx`
- Internet connection for first install

> **Note:** The official PyPI package is **`graphifyy`** (double 'y').
> The CLI command remains **`graphify`**.

{os_install}

{host_install}

## Build the graph

After installation, generate the knowledge graph for your project:

```bash
# From the project root (where .agent/ lives)
cd /path/to/your/project
graphify .
```

This produces:

```
{GRAPHIFY_OUT_DIR}/
├── graph.html
├── GRAPH_REPORT.md
└── graph.json
```

## Verify installation

```bash
# Check CLI is available
graphify --version

# Run this guard check
python3 .agent/scripts/graphify_required_check.py
```

## Troubleshooting

| Symptom | Solution |
|---------|----------|
| `graphify: command not found` | Run `uv tool install graphifyy` or `pipx install graphifyy` |
| `graphify-out/` missing | Run `graphify .` from project root |
| `graph.json` missing | Run `graphify .` — it generates all three output files |
| Permission denied | Ensure the project directory is writable |
| uv not installed | See OS-specific install section above |
"""


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Write install guide to disk
# ─────────────────────────────────────────────────────────────────────────────

def write_graphify_install_guide(
    workspace_root: Path | str | None = None,
    host_type: str = "unknown",
) -> dict[str, Any]:
    """Write the Graphify install guide to .agent/docs/GRAPHIFY_INSTALL_REQUIRED.md.

    Creates parent directories if they don't exist.
    Uses atomic write (temp file + rename) to prevent partial writes.

    Returns dict with ok/verdict/path on success, error dict on failure.
    """
    if workspace_root is None:
        workspace_root = Path.cwd()
    root = Path(workspace_root).resolve()
    target = root / INSTALL_GUIDE_REL_PATH

    content = render_graphify_install_guide(host_type=host_type)
    result = _write_doc_atomic(target, content)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Main check entry point
# ─────────────────────────────────────────────────────────────────────────────

def check_graphify_required(
    workspace_root: Path | str | None = None,
    host_type: str = "unknown",
    auto_write_guide: bool = True,
) -> dict[str, Any]:
    """Full Graphify mandatory guard check.

    Orchestrates:
    1. validate_graphify_installation() — is the CLI available?
    2. validate_graphify_outputs() — are the output files present?

    If Graphify is missing or outputs are incomplete:
    - Writes the install guide to disk (if auto_write_guide=True)
    - Returns a blocking verdict with next_actions

    If everything is ready:
    - Returns GRAPHIFY_READY verdict

    Returns structured dict always containing "ok", "verdict", "blocking", "write_allowed".
    """
    if workspace_root is None:
        workspace_root = Path.cwd()
    root = Path(workspace_root).resolve()
    normalized_host = (host_type or "unknown").strip().lower()

    # Step 1: Binary check.
    install_result = validate_graphify_installation()
    binary_ok = install_result.get("ok") is True
    binary_verdict = install_result.get("verdict", "")

    if not binary_ok:
        install_guide_path: str | None = None
        if auto_write_guide:
            write_result = write_graphify_install_guide(root, normalized_host)
            if write_result.get("ok"):
                install_guide_path = write_result.get("path")

        if binary_verdict == VERDICT_BINARY_MISSING:
            return {
                "ok": False,
                "verdict": VERDICT_BINARY_MISSING,
                "blocking": True,
                "write_allowed": False,
                "reason": "Graphify CLI is not installed. Graphify is mandatory for this .agent workflow.",
                "binary": install_result,
                "install_guide": install_guide_path or str(root / INSTALL_GUIDE_REL_PATH),
                "next_actions": [
                    "Install Graphify with: uv tool install graphifyy",
                    "Or: pipx install graphifyy",
                    "Run: graphify install --platform " + normalized_host,
                    "Run: graphify .",
                    "Re-run: graphify_required_check",
                ],
                "diagnostic_only": True,
            }

        if binary_verdict == VERDICT_BINARY_FAILED:
            return {
                "ok": False,
                "verdict": VERDICT_BINARY_FAILED,
                "blocking": True,
                "write_allowed": False,
                "reason": install_result.get("reason", "Graphify binary is not responding correctly."),
                "binary": install_result,
                "install_guide": install_guide_path or str(root / INSTALL_GUIDE_REL_PATH),
                "next_actions": [
                    "Check PATH: ensure the real graphify CLI is the first match",
                    "Run: which graphify && graphify --version",
                    "Re-install: uv tool install graphifyy --force",
                    "Re-run: graphify_required_check",
                ],
                "diagnostic_only": True,
            }

    # Step 2: Output files check.
    outputs_result = validate_graphify_outputs(root)
    outputs_ok = outputs_result.get("ok") is True
    outputs_verdict = outputs_result.get("verdict", "")

    if not outputs_ok:
        if outputs_verdict == VERDICT_GRAPH_INVALID_JSON:
            return {
                "ok": False,
                "verdict": VERDICT_GRAPH_INVALID_JSON,
                "blocking": True,
                "write_allowed": False,
                "reason": outputs_result.get("reason", "graph.json is corrupted or invalid."),
                "binary": install_result,
                "outputs": outputs_result,
                "install_guide": str(root / INSTALL_GUIDE_REL_PATH),
                "next_actions": [
                    "Run: graphify .",
                    "Check graphify-out/graph.json for valid JSON content",
                ],
                "diagnostic_only": True,
            }

        return {
            "ok": False,
            "verdict": VERDICT_OUTPUTS_MISSING,
            "blocking": True,
            "write_allowed": False,
            "reason": "Graphify is installed but graphify-out is missing or incomplete.",
            "binary": install_result,
            "outputs": outputs_result,
            "install_guide": str(root / INSTALL_GUIDE_REL_PATH),
            "next_actions": [
                "Run: graphify .",
                "Verify graphify-out/graph.json exists",
                "Verify graphify-out/GRAPH_REPORT.md exists",
                "Verify graphify-out/graph.html exists",
            ],
            "diagnostic_only": True,
        }

    # Step 3: Everything ready.
    return {
        "ok": True,
        "verdict": VERDICT_READY,
        "blocking": False,
        "write_allowed": True,
        "binary": install_result,
        "outputs": outputs_result,
    }
