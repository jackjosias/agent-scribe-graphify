#!/usr/bin/env python3
"""lease_extend_smoke.py — End-to-end smoke test for lease_extend MCP tool.

Validates the full path:
  server_entry.py --call lease_extend --args {...}

Covers:
  [1] lease_extend with nonexistent id → error
  [2] resource_lock_claim via MCP
  [3] resource_lock_status shows HELD
  [4] resource_lock_release via MCP
  [5] resource_lock_status shows FREE after release
  [6] lease_extend with empty id → LEASE_ID_REQUIRED

Note: Action lease issue/consume/happy-path is covered by unit tests
(test_lease_extend.py) because pre_action_guard requires a full MCP
task context (before_task → scribe_query → graphify_query) that cannot
be set up in an isolated smoke test.

Exit: 0 on all pass, 1 on any failure.
Designed to run in CI and African-latency environments (generous timeouts).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import tempfile
from pathlib import Path

# ─── Configuration ────────────────────────────────────────────────────────────

WORKSPACE = Path(os.environ.get("AGENT_SCRIBE_GRAPHIFY_ROOT", Path(__file__).resolve().parents[2]))
ENTRY_SCRIPT = WORKSPACE / ".agent" / "mcp" / "server_entry.py"
AGENT_ID = "smoke-agent-lease-extend"
TASK_ID = "smoke-task-lex-001"
TOOL_TIMEOUT = 30  # seconds per call
RETRY_COUNT = 3

_OK = "\033[32m✓\033[0m"
_FAIL = "\033[31m✗\033[0m"
_failures: list[str] = []
_passes: int = 0


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _env() -> dict[str, str]:
    e = dict(os.environ)
    e["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(WORKSPACE)
    mcp_dir = str(WORKSPACE / ".agent" / "mcp")
    existing = e.get("PYTHONPATH", "")
    e["PYTHONPATH"] = f"{mcp_dir}{os.pathsep}{existing}" if existing else mcp_dir
    return e


def call(tool: str, args: dict) -> dict:
    """Call an MCP tool with retry on transient failure."""
    last: dict = {}
    delay = 0.5
    for attempt in range(RETRY_COUNT):
        try:
            proc = subprocess.run(
                [sys.executable, str(ENTRY_SCRIPT), "--call", tool, "--args", json.dumps(args)],
                cwd=str(WORKSPACE),
                env=_env(),
                text=True,
                capture_output=True,
                timeout=TOOL_TIMEOUT,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            last = {"ok": False, "error": "TIMEOUT"}
            break
        except Exception as exc:
            last = {"ok": False, "error": str(exc)}
            time.sleep(min(delay, 8.0))
            delay *= 2
            continue

        if proc.returncode != 0:
            last = {
                "ok": False, "error": "NON_ZERO_EXIT",
                "exit_code": proc.returncode,
                "stderr": proc.stderr.strip(),
            }
            time.sleep(min(delay, 8.0))
            delay *= 2
            continue

        try:
            outer = json.loads(proc.stdout.strip())
        except json.JSONDecodeError:
            last = {"ok": False, "error": "JSON_DECODE", "raw": proc.stdout.strip()}
            break

        if isinstance(outer, dict) and "content" in outer:
            items = outer.get("content", [])
            if items:
                try:
                    return json.loads(items[0].get("text", "{}"))
                except json.JSONDecodeError:
                    pass
        return outer

    return last


def ensure_agent() -> None:
    """Register agent as active before smoke tests run."""
    # Register the agent explicitly — discipline_ping does NOT auto-register.
    _ = call("register_agent", {
        "agent_id": AGENT_ID,
        "host_tool": "smoke",
        "model_name": "lease-extend-smoke",
    })
    # Confirm the agent is active.
    _ = call("discipline_ping", {"agent_id": AGENT_ID, "phase": "smoke_init"})


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passes
    if condition:
        _passes += 1
        print(f"  {_OK}  [{_passes + len(_failures):02d}] {label}")
    else:
        _failures.append(label)
        print(f"  {_FAIL}  [{_passes + len(_failures):02d}] {label}" + (f" — {detail}" if detail else ""))


# ─── Smoke tests ──────────────────────────────────────────────────────────────

def smoke_01_lease_extend_nonexistent_rejected() -> None:
    """[1] Extend a non-existent lease id → ACTION_LEASE_INVALID."""
    result = call("lease_extend", {
        "lease_id": "nonexistent-lease-id-12345678",
        "agent_id": AGENT_ID,
    })
    rejected = (
        result.get("verdict") == "ACTION_LEASE_INVALID"
        or result.get("code") == "ACTION_LEASE_INVALID"
        or not result.get("ok", True)
    )
    check("[1] nonexistent lease rejected", rejected, str(result))


def smoke_02_resource_lock_claim() -> str:
    """[6] Claim a resource lock."""
    result = call("resource_lock_claim", {
        "agent_id": AGENT_ID,
        "resource": "src/critical_module.py",
        "task_id": TASK_ID,
    })
    ok = result.get("verdict") == "RESOURCE_LOCK_CLAIMED"
    check("[2] resource_lock_claim", ok, str(result))
    return result.get("lock_id", "")


def smoke_03_resource_lock_status_held() -> None:
    """[3] resource_lock_status shows HELD."""
    result = call("resource_lock_status", {"resource": "src/critical_module.py"})
    ok = result.get("verdict") == "RESOURCE_LOCK_HELD" and result.get("locked") is True
    check("[3] resource_lock_status HELD", ok, str(result))


def smoke_04_resource_lock_release() -> None:
    """[4] Release the resource lock."""
    result = call("resource_lock_release", {
        "agent_id": AGENT_ID,
        "resource": "src/critical_module.py",
    })
    ok = result.get("verdict") == "RESOURCE_LOCK_RELEASED"
    check("[4] resource_lock_release", ok, str(result))


def smoke_05_resource_lock_status_free() -> None:
    """[5] resource_lock_status shows FREE after release."""
    result = call("resource_lock_status", {"resource": "src/critical_module.py"})
    ok = result.get("verdict") == "RESOURCE_LOCK_FREE" and result.get("locked") is False
    check("[5] resource_lock_status FREE", ok, str(result))


def smoke_06_lease_extend_empty_id_rejected() -> None:
    """[6] lease_extend with empty lease_id → LEASE_ID_REQUIRED."""
    result = call("lease_extend", {
        "lease_id": "",
        "agent_id": AGENT_ID,
    })
    rejected = (
        result.get("verdict") == "LEASE_ID_REQUIRED"
        or not result.get("ok", True)
    )
    check("[6] lease_extend empty id rejected", rejected, str(result))


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    print(f"\n{'='*60}")
    print(f"  LEASE EXTEND SMOKE — V2.14")
    print(f"  workspace : {WORKSPACE}")
    print(f"  entry     : {ENTRY_SCRIPT}")
    print(f"{'='*60}\n")

    if not ENTRY_SCRIPT.exists():
        print(f"  {_FAIL}  server_entry.py not found at {ENTRY_SCRIPT}")
        return 1

    ensure_agent()

    # Run smokes in order; each is independent (no lease_id dependency).
    smoke_01_lease_extend_nonexistent_rejected()
    smoke_02_resource_lock_claim()
    smoke_03_resource_lock_status_held()
    smoke_04_resource_lock_release()
    smoke_05_resource_lock_status_free()
    smoke_06_lease_extend_empty_id_rejected()

    total = _passes + len(_failures)
    print(f"\n{'='*60}")
    print(f"  Result: {_passes}/{total} passed, {len(_failures)} failed")
    if _failures:
        print(f"  Failed: {', '.join(_failures)}")
        print(f"  SMOKE STATUS: FAIL")
    else:
        print(f"  SMOKE STATUS: LEASE_EXTEND_SMOKE_OK")
    print(f"{'='*60}\n")

    return 0 if not _failures else 1


if __name__ == "__main__":
    sys.exit(main())
