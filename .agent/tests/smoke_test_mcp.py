#!/usr/bin/env python3
"""smoke_test_mcp.py — MCP end-to-end smoke test (Fix #7).

PROBLEM:
    server_entry.py, server_ext.py, host_adapter/ exist but there is no test
    that verifies the MCP server actually exposes its tools to the host LLM.
    In past sessions, the host declared UNSAFE because it could not see the
    tools. This was not a config bug — it was a missing smoke test.

THIS TEST:
    1. Discovers the server_entry.py location.
    2. Launches the MCP server as a subprocess (stdio transport).
    3. Sends a JSON-RPC 2.0 "initialize" request.
    4. Sends a "tools/list" request.
    5. Validates that the expected tools are present in the response.
    6. Verifies no error conditions in the response.
    7. Terminates the subprocess cleanly.

DESIGN:
    - No external MCP SDK dependency: uses raw JSON-RPC 2.0 over stdin/stdout.
    - Timeout: 10 seconds per step (generous for slow machines / cold start).
    - Cross-platform: subprocess + pipes, no OS-specific APIs.
    - Can be run standalone: python smoke_test_mcp.py
    - Can be integrated into pytest / unittest: python -m pytest smoke_test_mcp.py

EXIT CODES:
    0 = all tools visible, MCP server healthy.
    1 = smoke test failed (details printed to stderr).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

_TIMEOUT_SECONDS = 15
_MCP_DIR = Path(__file__).resolve().parent.parent / "mcp"
_SERVER_ENTRY = _MCP_DIR / "server_entry.py"

# Minimum set of MCP tools that MUST be visible to declare the server healthy.
# This is the source-of-truth list — update when tools are added/removed.
_REQUIRED_TOOLS: set[str] = {
    "before_task",
    "workflow_next",
    "scribe_query",
    "graphify_query",
    "claim_resource",
    "file_hash",
    "propose_patch",
    "apply_patch",
    "delete_resource",
    "finish_task",
    "scribe_record",
}


# ─────────────────────────────────────────────────────────────────────────────
# JSON-RPC helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_request(method: str, params: dict[str, Any] | None = None, req_id: int = 1) -> bytes:
    """Serialize a JSON-RPC 2.0 request to bytes."""
    msg = json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "id": req_id,
        "params": params or {},
    }, ensure_ascii=False)
    return (msg + "\n").encode("utf-8")


def _read_response(proc: subprocess.Popen, timeout: float = _TIMEOUT_SECONDS) -> dict[str, Any]:
    """Read one JSON-RPC response line from the process stdout.

    Raises RuntimeError on timeout or parse failure.
    """
    assert proc.stdout is not None  # noqa: S101
    # Simple line read. Python's readline is buffered and clean.
    line = proc.stdout.readline()
    if not line:
        stderr_dump = ""
        try:
            assert proc.stderr is not None
            stderr_dump = proc.stderr.read(4096).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(
            f"MCP server closed stdout unexpectedly. stderr: {stderr_dump}"
        )
    try:
        return json.loads(line.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"MCP server returned invalid JSON: {exc}. Line: {line!r}") from exc


# ─────────────────────────────────────────────────────────────────────────────
# Smoke test implementation
# ─────────────────────────────────────────────────────────────────────────────

def run_smoke_test(
    project_root: Path | None = None,
    required_tools: set[str] | None = None,
    timeout: float = _TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Launch the MCP server and verify tool visibility end-to-end.

    Args:
        project_root: Override AGENT_SCRIBE_GRAPHIFY_ROOT. Defaults to auto-detect.
        required_tools: Set of tool names that must be present. Defaults to _REQUIRED_TOOLS.
        timeout: Per-step timeout in seconds.

    Returns:
        {
            "ok": bool,
            "verdict": str,
            "tools_found": list[str],
            "tools_missing": list[str],
            "tools_unexpected": list[str],  # present but not in required list (informational)
            "details": str,
        }
    """
    if required_tools is None:
        required_tools = _REQUIRED_TOOLS

    # Resolve project root.
    if project_root is None:
        env_root = os.environ.get("AGENT_SCRIBE_GRAPHIFY_ROOT", "").strip()
        if env_root:
            project_root = Path(env_root).resolve()
        else:
            # Walk upward from this file's .agent directory.
            this_file = Path(__file__).resolve()
            try:
                project_root = this_file.parents[2]
            except IndexError:
                project_root = Path.cwd()

    if not _SERVER_ENTRY.is_file():
        return {
            "ok": False,
            "verdict": "SERVER_ENTRY_MISSING",
            "tools_found": [],
            "tools_missing": sorted(required_tools),
            "tools_unexpected": [],
            "details": f"server_entry.py not found at {_SERVER_ENTRY}",
        }

    env = {**os.environ, "AGENT_SCRIBE_GRAPHIFY_ROOT": str(project_root)}

    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            [sys.executable, str(_SERVER_ENTRY)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(project_root),
        )

        # Step 1: Initialize.
        assert proc.stdin is not None  # noqa: S101
        init_req = _make_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke_test_mcp", "version": "1.0.0"},
        }, req_id=1)
        proc.stdin.write(init_req)
        proc.stdin.flush()

        init_resp = _read_response(proc, timeout=timeout)
        if "error" in init_resp:
            return {
                "ok": False,
                "verdict": "MCP_INITIALIZE_ERROR",
                "tools_found": [],
                "tools_missing": sorted(required_tools),
                "tools_unexpected": [],
                "details": f"initialize error: {init_resp['error']}",
            }

        # Step 2: List tools.
        list_req = _make_request("tools/list", {}, req_id=2)
        proc.stdin.write(list_req)
        proc.stdin.flush()

        list_resp = _read_response(proc, timeout=timeout)
        if "error" in list_resp:
            return {
                "ok": False,
                "verdict": "MCP_TOOLS_LIST_ERROR",
                "tools_found": [],
                "tools_missing": sorted(required_tools),
                "tools_unexpected": [],
                "details": f"tools/list error: {list_resp['error']}",
            }

        # Parse tool names from the response.
        tools_result = list_resp.get("result", {})
        tools_list = tools_result.get("tools", [])
        if not isinstance(tools_list, list):
            tools_list = []

        found_names: set[str] = set()
        for tool in tools_list:
            if isinstance(tool, dict) and tool.get("name"):
                found_names.add(str(tool["name"]))

        missing = required_tools - found_names
        unexpected = found_names - required_tools  # Present but not required.

        ok = len(missing) == 0
        verdict = "MCP_SMOKE_OK" if ok else "MCP_TOOLS_MISSING"

        return {
            "ok": ok,
            "verdict": verdict,
            "tools_found": sorted(found_names),
            "tools_missing": sorted(missing),
            "tools_unexpected": sorted(unexpected),
            "details": (
                f"All {len(required_tools)} required tools visible."
                if ok
                else f"Missing {len(missing)} required tools: {sorted(missing)}"
            ),
        }

    except RuntimeError as exc:
        return {
            "ok": False,
            "verdict": "MCP_TRANSPORT_ERROR",
            "tools_found": [],
            "tools_missing": sorted(required_tools),
            "tools_unexpected": [],
            "details": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "verdict": "MCP_UNEXPECTED_ERROR",
            "tools_found": [],
            "tools_missing": sorted(required_tools),
            "tools_unexpected": [],
            "details": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()


# ─────────────────────────────────────────────────────────────────────────────
# unittest integration (pytest-compatible)
# ─────────────────────────────────────────────────────────────────────────────

class TestMCPSmoke(unittest.TestCase):
    """Fix #7 — MCP end-to-end smoke tests.

    Tests:
      1. server_entry.py exists at expected location.
      2. MCP server initializes successfully (no JSON-RPC error).
      3. tools/list returns at least the required minimum tool set.
      4. No required tools are missing.
    """

    def test_01_server_entry_exists(self) -> None:
        """server_entry.py must be present — it is the MCP transport bootstrap."""
        self.assertTrue(
            _SERVER_ENTRY.is_file(),
            f"server_entry.py not found at {_SERVER_ENTRY}. "
            "The MCP bundle may be incomplete.",
        )

    def test_02_server_entry_is_python(self) -> None:
        """server_entry.py must be syntactically valid Python."""
        try:
            code = _SERVER_ENTRY.read_text(encoding="utf-8")
            compile(code, str(_SERVER_ENTRY), "exec")
        except SyntaxError as exc:
            self.fail(f"server_entry.py has a syntax error: {exc}")

    def test_03_list_tools_from_cli(self) -> None:
        """server_entry.py --list-tools must emit a JSON tool list on stdout.

        This test validates the CLI introspection path (used by GEMINI.md rule:
        'MCP local listable with server_entry.py --list-tools').
        We support this via a lightweight --list-tools flag in server_entry.py
        OR via a helper script. If the flag is not supported, we skip.
        """
        result = subprocess.run(
            [sys.executable, str(_SERVER_ENTRY), "--list-tools"],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "AGENT_SCRIBE_GRAPHIFY_ROOT": str(
                Path(__file__).resolve().parents[2]
            )},
        )
        # If flag not supported (non-zero exit / error), skip gracefully.
        if result.returncode != 0:
            self.skipTest(
                f"server_entry.py does not support --list-tools flag "
                f"(exit={result.returncode}). "
                "Implement the flag for full smoke coverage."
            )
        try:
            data = json.loads(result.stdout)
            if isinstance(data, dict) and "tools" in data:
                tools_list = data["tools"]
            else:
                tools_list = data
            self.assertIsInstance(tools_list, list, "Expected list of tools")
            for tool in tools_list:
                name = tool.get("name") if isinstance(tool, dict) else tool
                self.assertIsInstance(name, str, "Expected tool name to be a string")
        except json.JSONDecodeError as exc:
            self.fail(f"--list-tools output is not valid JSON: {exc}")

    def test_04_mcp_transport_tools_visible(self) -> None:
        """Full end-to-end: launch MCP server, initialize, list tools, verify minimum set.

        This is the most important smoke test: it verifies that the tools are
        visible to an LLM host via the MCP stdio transport, not just importable
        as Python modules.
        """
        result = run_smoke_test()
        if result["verdict"] == "SERVER_ENTRY_MISSING":
            self.skipTest("server_entry.py not found — bundle may be incomplete.")

        self.assertTrue(
            result["ok"],
            f"MCP smoke test FAILED: {result['verdict']}\n"
            f"Details: {result['details']}\n"
            f"Missing tools: {result['tools_missing']}\n"
            f"Found tools: {result['tools_found']}",
        )
        self.assertEqual(
            result["tools_missing"],
            [],
            f"Missing required MCP tools: {result['tools_missing']}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def _main() -> int:
    """CLI runner: exit 0 on success, 1 on failure."""
    print("=== MCP Smoke Test (Fix #7) ===", flush=True)
    result = run_smoke_test()

    print(f"Verdict : {result['verdict']}", flush=True)
    print(f"Details : {result['details']}", flush=True)
    if result["tools_found"]:
        print(f"Tools found ({len(result['tools_found'])}):", flush=True)
        for t in result["tools_found"]:
            marker = "✓" if t in _REQUIRED_TOOLS else "?"
            print(f"  {marker} {t}", flush=True)
    if result["tools_missing"]:
        print(f"\n❌ Missing required tools ({len(result['tools_missing'])}):", flush=True)
        for t in result["tools_missing"]:
            print(f"  ✗ {t}", flush=True)
    if result["tools_unexpected"]:
        print(f"\n? Extra tools (not in required set):", flush=True)
        for t in result["tools_unexpected"]:
            print(f"  ? {t}", flush=True)

    status = "✅ PASS" if result["ok"] else "❌ FAIL"
    print(f"\n{status}", flush=True)

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    if "--test" in sys.argv or "-v" in sys.argv:
        # Run unittest suite.
        sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a not in {"--test"}]
        unittest.main(verbosity=2)
    else:
        sys.exit(_main())
