#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / ".agent" / "mcp" / "server_entry.py"


def fail(message: str) -> None:
    raise SystemExit(f"SCRIBE_RECORD_SMOKE_FAIL: {message}")


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    proc = subprocess.run([sys.executable, str(ENTRY), "--call", name, "--args", json.dumps(args)], cwd=str(ROOT), text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        try:
            return json.loads(proc.stderr or proc.stdout)
        except json.JSONDecodeError:
            fail(f"{name} failed\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}")
    outer = json.loads(proc.stdout)
    if "content" in outer:
        return json.loads(outer["content"][0]["text"])
    return outer


def main() -> int:
    refused = call_tool("scribe_record", {"request": "missing agent", "summary": "must fail", "touched_resources": [], "verdict": "TEST"})
    if refused.get("ok") is not False or "agent_id is required" not in refused.get("error", ""):
        fail(f"scribe_record should refuse missing agent_id: {refused}")

    boot = call_tool("bootstrap", {"host_tool": "scribe-record-smoke", "model_name": "test", "run_legacy_bootstrap": False})
    if boot.get("verdict") != "BOOT_OK_MCP":
        fail(f"bootstrap failed: {boot}")
    agent_id = boot["agent"]["agent_id"]

    record = call_tool("scribe_record", {
        "agent_id": agent_id,
        "request": "record smoke request",
        "summary": "record smoke summary",
        "touched_resources": ["README.md"],
        "resources": ["README.md"],
        "verdict": "SMOKE_VERDICT",
        "record_type": "scar",
        "severity": "high",
        "evidence": "typed smoke evidence",
        "root_cause": "typed memory fields were not previously covered",
        "fix": "extend scribe_record payload",
        "prevention": "keep typed smoke coverage",
        "related_errors": ["SMOKE_TYPED_RECORD"],
        "related_tests": [".agent/scripts/scribe_record_smoke.py"],
        "tags": ["smoke", "scribe_record", "scar"],
    })
    if record.get("verdict") != "SCRIBE_RECORD_WRITTEN":
        fail(f"scribe_record did not write: {record}")
    path = ROOT / record["record"]
    if not path.is_file() or ROOT / ".agent" / "state" / "scribe-out" / "records" not in path.parents:
        fail(f"record path invalid: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("agent_id") != agent_id or payload.get("verdict") != "SMOKE_VERDICT":
        fail(f"record payload invalid: {payload}")
    if payload.get("record_type") != "scar" or payload.get("severity") != "high":
        fail(f"typed record fields invalid: {payload}")
    if payload.get("root_cause") != "typed memory fields were not previously covered" or payload.get("prevention") != "keep typed smoke coverage":
        fail(f"scar details invalid: {payload}")
    print("SCRIBE_RECORD_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
