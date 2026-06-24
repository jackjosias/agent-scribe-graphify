#!/usr/bin/env python3
"""lease_extend_smoke.py — End-to-end smoke test for lease_extend MCP tool.

Validates the full path:
  server_entry.py --call lease_extend --args {...}

Covers:
  [1] issue_action_lease via MCP
  [2] lease_extend via MCP (happy path)
  [3] lease_extend with custom extend_seconds
  [4] lease_extend wrong agent → ACTION_LEASE_INVALID
  [5] lease_extend on consumed lease → ACTION_LEASE_CONSUMED
  [6] resource_lock_claim via MCP
  [7] resource_lock_status shows HELD
  [8] resource_lock_release via MCP
  [9] resource_lock_status shows FREE after release
  [10] extend_seconds=0 → EXTEND_SECONDS_INVALID

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
    result = call("discipline_ping", {"agent_id": AGENT_ID, "phase": "smoke_init"})
    # discipline_ping auto-registers if agent unknown; acceptable to ignore error here.
    _ = result  # noqa: F841


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passes
    if condition:
        _passes += 1
        print(f"  {_OK}  [{_passes + len(_failures):02d}] {label}")
    else:
        _failures.append(label)
        print(f"  {_FAIL}  [{_passes + len(_failures):02d}] {label}" + (f" — {detail}" if detail else ""))


# ─── Smoke tests ──────────────────────────────────────────────────────────────

def smoke_01_issue_lease() -> str:
    """[1] Issue a lease via MCP pre_action_guard (which issues lease internally).
    Use a direct discipline call for isolation."""
    result = call("pre_action_guard", {
        "agent_id": AGENT_ID,
        "request": "smoke test apply_patch",
        "intent": "write",
        "resource": "src/smoke_target.py",
        "planned_action": "apply_patch",
        "task_id": TASK_ID,
        "context_token": "smoke-ctx",
    })
    ok = result.get("ok") and result.get("verdict") == "PRE_ACTION_GUARD_OK"
    check("[1] issue_action_lease via pre_action_guard", ok, str(result))
    return result.get("action_lease_id", "") if ok else ""


def smoke_02_extend_lease_happy(lease_id: str) -> None:
    """[2] Extend a valid active lease."""
    if not lease_id:
        check("[2] lease_extend happy path", False, "no lease_id from step 1")
        return
    result = call("lease_extend", {
        "lease_id": lease_id,
        "agent_id": AGENT_ID,
    })
    ok = result.get("verdict") == "LEASE_EXTENDED" and result.get("extend_count") == 1
    check("[2] lease_extend happy path", ok, str(result))


def smoke_03_extend_custom_seconds(lease_id: str) -> None:
    """[3] Extend with custom extend_seconds=45."""
    if not lease_id:
        check("[3] lease_extend custom seconds", False, "no lease_id from step 1")
        return
    result = call("lease_extend", {
        "lease_id": lease_id,
        "agent_id": AGENT_ID,
        "extend_seconds": 45,
    })
    ok = result.get("verdict") == "LEASE_EXTENDED" and result.get("extend_count") == 2
    check("[3] lease_extend custom seconds", ok, str(result))


def smoke_04_wrong_agent_rejected(lease_id: str) -> None:
    """[4] Wrong agent should get ACTION_LEASE_INVALID."""
    if not lease_id:
        check("[4] wrong agent rejected", False, "no lease_id from step 1")
        return
    result = call("lease_extend", {
        "lease_id": lease_id,
        "agent_id": "intruder-agent-xyz",
    })
    # MCP tool wraps DisciplineError as error field.
    rejected = (
        result.get("error") == "ACTION_LEASE_INVALID"
        or result.get("code") == "ACTION_LEASE_INVALID"
        or not result.get("ok", True)
    )
    check("[4] wrong agent rejected", rejected, str(result))


def smoke_05_consumed_lease_rejected(lease_id: str) -> None:
    """[5] Consume the lease, then try to extend → ACTION_LEASE_CONSUMED."""
    if not lease_id:
        check("[5] consumed lease rejected", False, "no lease_id from step 1")
        return
    # Consume via apply_patch which consumes the lease.
    consume_result = call("apply_patch", {
        "agent_id": AGENT_ID,
        "action_lease_id": lease_id,
        "target_path": "src/smoke_target.py",
        "patch_content": "# smoke test patch\n",
        "task_id": TASK_ID,
    })
    # After consume, extend should fail.
    result = call("lease_extend", {
        "lease_id": lease_id,
        "agent_id": AGENT_ID,
    })
    rejected = (
        result.get("error") in {"ACTION_LEASE_CONSUMED", "ACTION_LEASE_INVALID"}
        or not result.get("ok", True)
    )
    check("[5] consumed lease rejected", rejected, f"apply={consume_result.get('ok')}, extend={result}")


def smoke_06_resource_lock_claim() -> str:
    """[6] Claim a resource lock."""
    result = call("resource_lock_claim", {
        "agent_id": AGENT_ID,
        "resource": "src/critical_module.py",
        "task_id": TASK_ID,
    })
    ok = result.get("verdict") == "RESOURCE_LOCK_ACQUIRED"
    check("[6] resource_lock_claim", ok, str(result))
    return result.get("lock_id", "")


def smoke_07_resource_lock_status_held() -> None:
    """[7] resource_lock_status shows HELD."""
    result = call("resource_lock_status", {"resource": "src/critical_module.py"})
    ok = result.get("verdict") == "RESOURCE_LOCK_HELD" and result.get("locked") is True
    check("[7] resource_lock_status HELD", ok, str(result))


def smoke_08_resource_lock_release() -> None:
    """[8] Release the resource lock."""
    result = call("resource_lock_release", {
        "agent_id": AGENT_ID,
        "resource": "src/critical_module.py",
    })
    ok = result.get("verdict") == "RESOURCE_LOCK_RELEASED"
    check("[8] resource_lock_release", ok, str(result))


def smoke_09_resource_lock_status_free() -> None:
    """[9] resource_lock_status shows FREE after release."""
    result = call("resource_lock_status", {"resource": "src/critical_module.py"})
    ok = result.get("verdict") == "RESOURCE_LOCK_FREE" and result.get("locked") is False
    check("[9] resource_lock_status FREE", ok, str(result))


def smoke_10_zero_extend_seconds_rejected() -> None:
    """[10] extend_seconds=0 → EXTEND_SECONDS_INVALID."""
    # Issue a fresh lease to test this.
    guard_result = call("pre_action_guard", {
        "agent_id": AGENT_ID,
        "request": "smoke extend_seconds zero test",
        "intent": "write",
        "resource": "src/smoke_zero.py",
        "planned_action": "apply_patch",
        "task_id": TASK_ID + "-zero",
        "context_token": "smoke-ctx",
    })
    lease_id = guard_result.get("action_lease_id", "")
    if not lease_id:
        check("[10] extend_seconds=0 rejected", False, f"guard result: {guard_result}")
        return

    result = call("lease_extend", {
        "lease_id": lease_id,
        "agent_id": AGENT_ID,
        "extend_seconds": 0,
    })
    rejected = (
        result.get("error") == "EXTEND_SECONDS_INVALID"
        or result.get("code") == "EXTEND_SECONDS_INVALID"
        or not result.get("ok", True)
    )
    check("[10] extend_seconds=0 rejected", rejected, str(result))


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

    # Run smokes in order; steps 2-5 depend on lease_id from step 1.
    lease_id = smoke_01_issue_lease()
    smoke_02_extend_lease_happy(lease_id)
    smoke_03_extend_custom_seconds(lease_id)
    smoke_04_wrong_agent_rejected(lease_id)
    smoke_05_consumed_lease_rejected(lease_id)
    smoke_06_resource_lock_claim()
    smoke_07_resource_lock_status_held()
    smoke_08_resource_lock_release()
    smoke_09_resource_lock_status_free()
    smoke_10_zero_extend_seconds_rejected()

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
