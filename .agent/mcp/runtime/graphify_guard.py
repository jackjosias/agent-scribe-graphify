"""Mandatory Graphify guard backed by project-bound readiness evidence."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    from . import graphify_readiness
except ImportError:
    import graphify_readiness  # type: ignore

GRAPHIFY_CLI_NAME = "graphify"
GRAPHIFY_OUT_DIR = "graphify-out"
GRAPHIFY_REQUIRED_FILES = list(graphify_readiness.REQUIRED_FILES)
INSTALL_GUIDE_REL_PATH = ".agent/docs/GRAPHIFY_INSTALL_REQUIRED.md"
_SUBCMD_TIMEOUT = 15
_SUBCMD_RETRY_COUNT = 2
_SUBCMD_RETRY_DELAY = 1.0
_MIN_PYTHON = (3, 10)

VERDICT_READY = graphify_readiness.GRAPHIFY_READY
VERDICT_BINARY_MISSING = "GRAPHIFY_REQUIRED_NOT_INSTALLED"
VERDICT_OUTPUTS_MISSING = graphify_readiness.GRAPHIFY_OUTPUTS_INCOMPLETE
VERDICT_BINARY_FOUND = "GRAPHIFY_BINARY_FOUND"
VERDICT_BINARY_FAILED = "GRAPHIFY_BINARY_CHECK_FAILED"
VERDICT_DIAGNOSTIC_ONLY = "GRAPHIFY_DIAGNOSTIC_ONLY"
VERDICT_GRAPH_INVALID_JSON = graphify_readiness.GRAPHIFY_CORRUPT


class GraphifyGuardError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "verdict": self.code, "reason": self.message, "details": self.details}


def _detect_os() -> str:
    raw = platform.system().lower()
    if raw.startswith("linux"):
        return "linux"
    if raw.startswith("darwin"):
        return "darwin"
    if raw.startswith("windows") or raw.startswith("win"):
        return "windows"
    return "unknown"


def _safe_subprocess(cmd: list[str], timeout: float = _SUBCMD_TIMEOUT, retries: int = _SUBCMD_RETRY_COUNT) -> dict[str, Any]:
    last_error: str | None = None
    delay = _SUBCMD_RETRY_DELAY
    for attempt in range(max(1, retries)):
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, shell=False, check=False)
            return {"ok": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode, "error": None}
        except FileNotFoundError:
            return {"ok": False, "stdout": "", "stderr": "", "returncode": 127, "error": "command not found"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "stdout": "", "stderr": "", "returncode": 124, "error": f"timeout after {timeout}s"}
        except OSError as exc:
            last_error = str(exc)
            if attempt + 1 < max(1, retries):
                time.sleep(min(delay, 4.0))
                delay *= 2
    return {"ok": False, "stdout": "", "stderr": "", "returncode": -1, "error": last_error or "subprocess failed"}


def _graphify_on_path() -> Path | None:
    resolved = shutil.which(GRAPHIFY_CLI_NAME)
    if resolved is None:
        return None
    try:
        return Path(resolved).resolve()
    except OSError:
        return None


def detect_graphify_binary() -> bool:
    return _graphify_on_path() is not None


def get_graphify_version() -> str | None:
    binary = _graphify_on_path()
    if binary is None:
        return None
    for args in ([str(binary), "--version"], [str(binary), "--help"]):
        result = _safe_subprocess(args)
        if result["ok"]:
            value = (result["stdout"] or result["stderr"]).strip()
            return value or None
    return None


def validate_graphify_installation() -> dict[str, Any]:
    binary = _graphify_on_path()
    base = {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_ok": sys.version_info >= _MIN_PYTHON,
        "platform": _detect_os(),
        "binary_path": str(binary) if binary else None,
    }
    if binary is None:
        return {"ok": False, "verdict": VERDICT_BINARY_MISSING, "version": None, **base}
    probe = _safe_subprocess([str(binary), "--version"])
    if not probe["ok"]:
        help_probe = _safe_subprocess([str(binary), "--help"])
        if not help_probe["ok"]:
            return {"ok": False, "verdict": VERDICT_BINARY_FAILED, "version": None, "reason": probe.get("error") or probe.get("stderr") or "binary did not respond", **base}
    return {"ok": True, "verdict": VERDICT_BINARY_FOUND, "version": get_graphify_version(), **base}


def validate_graphify_outputs(workspace_root: Path | str | None = None) -> dict[str, Any]:
    root = Path(workspace_root or Path.cwd()).resolve()
    result = graphify_readiness.inspect_graphify_readiness(root)
    payload = result.to_dict()
    payload["readiness_verdict"] = result.verdict
    if result.ok:
        payload["verdict"] = "GRAPHIFY_OUTPUTS_READY"
    return payload


def _atomic_replace(src: str, dst: Path) -> None:
    """Publish a sibling temp file atomically with a bounded Windows retry.

    On Windows, os.replace can raise PermissionError while the destination is
    read concurrently; we retry a bounded number of times with backoff and then
    re-raise. We never silently fall back to a non-atomic direct write.
    """
    last_exc: OSError | None = None
    for attempt in range(5):
        try:
            os.replace(src, str(dst))
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt < 4:
                time.sleep(0.01 * (2 ** attempt))
                continue
            raise
    if last_exc is not None:
        raise last_exc


def _write_doc_atomic(path: Path, content: str) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_replace(temporary, path)
    except OSError as exc:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass
        return {"ok": False, "verdict": "INSTALL_GUIDE_WRITE_FAILED", "reason": str(exc), "path": str(path)}
    return {"ok": True, "verdict": "INSTALL_GUIDE_WRITTEN", "path": str(path)}


def render_graphify_install_guide(host_type: str = "unknown", os_name: str | None = None) -> str:
    os_name = (os_name or _detect_os()).lower().strip()
    host = (host_type or "unknown").lower().strip()
    if os_name == "windows":
        install = "winget install astral-sh.uv\nuv tool install graphifyy"
    elif os_name == "darwin":
        install = "brew install uv\nuv tool install graphifyy"
    else:
        install = "python3 -m pip install --user pipx\npipx install graphifyy"
    return f"""# Graphify Installation Required

Graphify is structural context compression for the LLM workflow. A write is
blocked unless its outputs are valid, bound to this project root, non-stub and
current for the workspace.

## Platform

- OS: `{os_name}`
- Host: `{host}`
- Python required: `{_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}+`

```text
{install}
```

Then, from the project root:

```text
.agent/workflow/scribe/scribe graph --project-build --timeout 180
.agent/workflow/scribe/scribe tenor-init --type cli --host <host-id>
```

The generated files must live in `.agent/state/outputs/graphify-out/` and include
`graph.json`, `GRAPH_REPORT.md`, `graph.html`, and the project-bound
`{graphify_readiness.MANIFEST_FILENAME}` readiness manifest.
"""


def write_graphify_install_guide(workspace_root: Path | str | None = None, host_type: str = "unknown") -> dict[str, Any]:
    root = Path(workspace_root or Path.cwd()).resolve()
    return _write_doc_atomic(root / INSTALL_GUIDE_REL_PATH, render_graphify_install_guide(host_type=host_type))


def check_graphify_required(workspace_root: Path | str | None = None, host_type: str = "unknown", auto_write_guide: bool = True) -> dict[str, Any]:
    root = Path(workspace_root or Path.cwd()).resolve()
    installation = validate_graphify_installation()
    readiness = graphify_readiness.inspect_graphify_readiness(root)
    if readiness.ok:
        return {
            "ok": True,
            "verdict": VERDICT_READY,
            "blocking": False,
            "write_allowed": True,
            "binary": installation,
            "outputs": readiness.to_dict(),
        }

    guide = str(root / INSTALL_GUIDE_REL_PATH)
    if auto_write_guide:
        write_result = write_graphify_install_guide(root, host_type)
        if write_result.get("ok"):
            guide = str(write_result["path"])
    next_actions = [
        readiness.next_action or graphify_readiness.PROJECT_BUILD_ACTION,
        ".agent/workflow/scribe/scribe tenor-init --type cli --host <host-id>",
    ]
    return {
        "ok": False,
        "verdict": readiness.verdict,
        "blocking": True,
        "write_allowed": False,
        "reason": readiness.reason,
        "binary": installation,
        "outputs": readiness.to_dict(),
        "install_guide": guide,
        "next_actions": next_actions,
        "diagnostic_only": True,
    }
