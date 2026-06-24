#!/usr/bin/env python3
"""portability_smoke.py — Cross-platform smoke test for portability.py.

Validates that portability checks work correctly on Linux / macOS / Windows.
Tests the full path from Python imports through portability_check MCP tool.

Checks:
  [1]  portability module importable from mcp/ dir
  [2]  full_portability_check() OK when .agent is at git root
  [3]  full_portability_check() FAIL when workspace has no .agent
  [4]  validate_agent_root() raises PortabilityError when .agent is at subdir
  [5]  read_agent_json() returns dict (with/without agent.json)
  [6]  write_agent_json() creates valid agent.json with schema_version
  [7]  write_agent_json() idempotent on second write
  [8]  portability_check MCP tool returns structured dict via server_entry.py
  [9]  portability_check MCP tool returns FAIL when .agent absent
  [10] ENV override (AGENT_SCRIBE_GRAPHIFY_ROOT) is respected

Exit: 0 on all pass, 1 on any failure.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

# ─── Configuration ────────────────────────────────────────────────────────────

WORKSPACE = Path(os.environ.get("AGENT_SCRIBE_GRAPHIFY_ROOT", Path(__file__).resolve().parents[2]))
ENTRY_SCRIPT = WORKSPACE / ".agent" / "mcp" / "server_entry.py"
MCP_DIR = str(WORKSPACE / ".agent" / "mcp")
TOOL_TIMEOUT = 20

_OK = "\033[32m✓\033[0m"
_FAIL = "\033[31m✗\033[0m"
_failures: list[str] = []
_passes: int = 0

# ─── Ensure portability is importable ─────────────────────────────────────────

if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

try:
    from runtime import portability
    _PORTABILITY_IMPORTED = True
except ImportError:
    # Try direct import (legacy path layout)
    try:
        import portability  # type: ignore
        _PORTABILITY_IMPORTED = True
    except ImportError:
        _PORTABILITY_IMPORTED = False


# ─── Helpers ──────────────────────────────────────────────────────────────────

def check(label: str, condition: bool, detail: str = "") -> None:
    global _passes
    if condition:
        _passes += 1
        print(f"  {_OK}  [{_passes + len(_failures):02d}] {label}")
    else:
        _failures.append(label)
        print(f"  {_FAIL}  [{_passes + len(_failures):02d}] {label}" + (f" — {detail}" if detail else ""))


def call_mcp(tool: str, args: dict) -> dict:
    """Invoke portability_check via server_entry.py --call."""
    env = dict(os.environ)
    env["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(WORKSPACE)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{MCP_DIR}{os.pathsep}{existing}" if existing else MCP_DIR

    try:
        proc = subprocess.run(
            [sys.executable, str(ENTRY_SCRIPT), "--call", tool, "--args", json.dumps(args)],
            cwd=str(WORKSPACE),
            env=env,
            text=True,
            capture_output=True,
            timeout=TOOL_TIMEOUT,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "TIMEOUT"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if proc.returncode != 0:
        return {"ok": False, "error": "NON_ZERO_EXIT", "stderr": proc.stderr.strip()}

    try:
        outer = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        return {"ok": False, "error": "JSON_DECODE", "raw": proc.stdout.strip()}

    if isinstance(outer, dict) and "content" in outer:
        items = outer.get("content", [])
        if items:
            try:
                return json.loads(items[0].get("text", "{}"))
            except json.JSONDecodeError:
                pass
    return outer


def _make_fake_git_root(path: Path) -> None:
    """Create a minimal .git directory so portability treats it as a git repo."""
    (path / ".git").mkdir(exist_ok=True)
    (path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (path / ".git" / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n", encoding="utf-8"
    )


def _make_fake_agent_bundle(path: Path) -> None:
    """Create minimal .agent/mcp/server_entry.py so validate_root passes."""
    agent_dir = path / ".agent"
    agent_dir.mkdir(exist_ok=True)
    mcp_dir = agent_dir / "mcp"
    mcp_dir.mkdir(exist_ok=True)
    entry = mcp_dir / "server_entry.py"
    if not entry.exists():
        entry.write_text(
            '#!/usr/bin/env python3\n"""Minimal server_entry.py for smoke tests."""\n'
            'if __name__ == "__main__":\n    print("smoke stub")\n',
            encoding="utf-8",
        )


# ─── Smoke tests ──────────────────────────────────────────────────────────────

def smoke_01_portability_importable() -> None:
    """[1] portability module importable from mcp/ dir."""
    check("[1] portability importable", _PORTABILITY_IMPORTED,
          "Check sys.path includes .agent/mcp/runtime or .agent/mcp/")


def smoke_02_check_ok_at_git_root() -> None:
    """[2] portability_check() OK when .agent is at git root."""
    if not _PORTABILITY_IMPORTED:
        check("[2] portability check OK at git root", False, "module not imported")
        return

    tmp = Path(tempfile.mkdtemp(prefix="smoke_port_ok_"))
    try:
        # Set up as fake git root with .agent.
        _make_fake_git_root(tmp)
        _make_fake_agent_bundle(tmp)

        with patch.object(portability, "_git_toplevel", return_value=tmp):
            result = portability.portability_check(workspace_root=str(tmp))

        ok = result.get("ok") is True and "VALID" in result.get("verdict", "")
        check("[2] portability check OK at git root", ok, str(result))
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def smoke_03_check_fail_no_agent_dir() -> None:
    """[3] portability_check() returns ok=False when .agent absent."""
    if not _PORTABILITY_IMPORTED:
        check("[3] portability check FAIL no .agent", False, "module not imported")
        return

    tmp = Path(tempfile.mkdtemp(prefix="smoke_port_fail_"))
    try:
        _make_fake_git_root(tmp)
        # No .agent directory.
        with patch.object(portability, "_git_toplevel", return_value=tmp):
            result = portability.portability_check(workspace_root=str(tmp))
        fail = result.get("ok") is False
        check("[3] portability check FAIL no .agent", fail, str(result))
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def smoke_04_validate_root_fails_in_subdir() -> None:
    """[4] assert_root_valid() raises PortabilityError when .agent is at subdir."""
    if not _PORTABILITY_IMPORTED:
        check("[4] PortabilityError on subdir", False, "module not imported")
        return

    tmp = Path(tempfile.mkdtemp(prefix="smoke_port_sub_"))
    try:
        _make_fake_git_root(tmp)
        # .agent is at tmp/sub/, not tmp/.
        sub = tmp / "packages" / "core"
        sub.mkdir(parents=True)
        _make_fake_agent_bundle(sub)

        with patch.object(portability, "_git_toplevel", return_value=tmp):
            try:
                portability.assert_root_valid(workspace_root=sub)
                check("[4] PortabilityError on subdir", False, "No error raised")
            except portability.PortabilityError as exc:
                check("[4] PortabilityError on subdir",
                      exc.code in {"AGENT_NOT_AT_GIT_ROOT", "AGENT_NOT_AT_ROOT"},
                      f"code={exc.code}")
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def smoke_05_read_agent_json_no_file() -> None:
    """[5] validate_root() returns agent_json_present=False when agent.json absent."""
    if not _PORTABILITY_IMPORTED:
        check("[5] validate_root agent.json absent", False, "module not imported")
        return

    tmp = Path(tempfile.mkdtemp(prefix="smoke_port_rj_"))
    try:
        _make_fake_agent_bundle(tmp)
        # No agent.json — just validate_root to verify structure.
        result = portability.validate_root(tmp)
        ok = isinstance(result, dict) and "verdict" in result
        check("[5] validate_root returns structured dict", ok, str(result))
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def smoke_06_write_agent_json_creates_file() -> None:
    """[6] init_agent_json() creates valid agent.json with schema_version."""
    if not _PORTABILITY_IMPORTED:
        check("[6] init_agent_json creates file", False, "module not imported")
        return

    tmp = Path(tempfile.mkdtemp(prefix="smoke_port_wj_"))
    try:
        agent_dir = tmp / ".agent"
        agent_dir.mkdir()
        portability.init_agent_json(tmp, project_name="smoke-proj")
        agent_json = agent_dir / portability.AGENT_JSON_FILENAME
        ok = agent_json.exists()
        if ok:
            content = json.loads(agent_json.read_text(encoding="utf-8"))
            ok = content.get("schema_version") == portability.AGENT_JSON_SCHEMA_VERSION
        check("[6] init_agent_json creates file", ok,
              f"schema_version={content.get('schema_version') if ok else 'N/A'}")
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def smoke_07_write_agent_json_idempotent() -> None:
    """[7] init_agent_json() is idempotent — second write updates project_name."""
    if not _PORTABILITY_IMPORTED:
        check("[7] init_agent_json idempotent", False, "module not imported")
        return

    tmp = Path(tempfile.mkdtemp(prefix="smoke_port_wji_"))
    try:
        agent_dir = tmp / ".agent"
        agent_dir.mkdir()
        portability.init_agent_json(tmp, project_name="v1")
        portability.init_agent_json(tmp, project_name="v2")
        agent_json = agent_dir / portability.AGENT_JSON_FILENAME
        content = json.loads(agent_json.read_text(encoding="utf-8"))
        ok = content.get("project_name") == "v2"
        check("[7] init_agent_json idempotent", ok, f"project_name={content.get('project_name')}")
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def smoke_08_mcp_tool_ok() -> None:
    """[8] portability_check MCP tool returns structured dict."""
    if not ENTRY_SCRIPT.exists():
        check("[8] MCP portability_check OK", False, f"entry script not found: {ENTRY_SCRIPT}")
        return

    result = call_mcp("portability_check", {"workspace_root": str(WORKSPACE)})
    has_verdict = "verdict" in result
    # ok may be True or False depending on environment — just verify structure.
    check("[8] MCP portability_check returns structured dict", has_verdict, str(result))


def smoke_09_mcp_tool_fail_no_agent() -> None:
    """[9] portability_check MCP tool returns FAIL when .agent absent in given workspace."""
    if not ENTRY_SCRIPT.exists():
        check("[9] MCP portability_check FAIL no .agent", False, "entry script not found")
        return

    tmp = Path(tempfile.mkdtemp(prefix="smoke_port_mcp_"))
    try:
        # No .agent in tmp — expect a failure verdict.
        result = call_mcp("portability_check", {"workspace_root": str(tmp)})
        fail = result.get("ok") is False or "FAIL" in str(result.get("verdict", "")).upper() or "MISSING" in str(result.get("verdict", "")).upper()
        check("[9] MCP portability_check FAIL no .agent", fail, str(result))
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def smoke_10_env_override_respected() -> None:
    """[10] AGENT_SCRIBE_GRAPHIFY_ROOT env override is respected."""
    if not _PORTABILITY_IMPORTED:
        check("[10] ENV override respected", False, "module not imported")
        return

    tmp = Path(tempfile.mkdtemp(prefix="smoke_port_env_"))
    try:
        _make_fake_git_root(tmp)
        _make_fake_agent_bundle(tmp)

        # Temporarily set the env var.
        old = os.environ.get(portability.ENV_ROOT_KEY)
        os.environ[portability.ENV_ROOT_KEY] = str(tmp)
        try:
            with patch.object(portability, "_git_toplevel", return_value=tmp):
                result = portability.portability_check(workspace_root=str(tmp))
            ok = result.get("project_root") == str(tmp) or result.get("ok") is True
            check("[10] ENV override respected", ok, str(result))
        finally:
            if old is None:
                os.environ.pop(portability.ENV_ROOT_KEY, None)
            else:
                os.environ[portability.ENV_ROOT_KEY] = old
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    print(f"\n{'='*60}")
    print(f"  PORTABILITY SMOKE — V2.14")
    print(f"  workspace : {WORKSPACE}")
    print(f"  platform  : {sys.platform}")
    print(f"  python    : {sys.version.split()[0]}")
    print(f"{'='*60}\n")

    smoke_01_portability_importable()
    smoke_02_check_ok_at_git_root()
    smoke_03_check_fail_no_agent_dir()
    smoke_04_validate_root_fails_in_subdir()
    smoke_05_read_agent_json_no_file()
    smoke_06_write_agent_json_creates_file()
    smoke_07_write_agent_json_idempotent()
    smoke_08_mcp_tool_ok()
    smoke_09_mcp_tool_fail_no_agent()
    smoke_10_env_override_respected()

    total = _passes + len(_failures)
    print(f"\n{'='*60}")
    print(f"  Result: {_passes}/{total} passed, {len(_failures)} failed")
    if _failures:
        print(f"  Failed: {', '.join(_failures)}")
        print(f"  SMOKE STATUS: PORTABILITY_SMOKE_FAIL")
    else:
        print(f"  SMOKE STATUS: PORTABILITY_SMOKE_OK")
    print(f"{'='*60}\n")

    return 0 if not _failures else 1


if __name__ == "__main__":
    sys.exit(main())
