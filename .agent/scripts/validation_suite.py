#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STEPS = (
    ("validation_runtime_lock", [sys.executable, ".agent/mcp/tests/test_validation_runtime_lock.py"]),
    ("agent_runtime_sync", [sys.executable, ".agent/tests/test_agent_runtime_sync.py"]),
    ("mcp_smoke", [sys.executable, ".agent/scripts/mcp_smoke.py"]),
    ("enforcement_redteam_smoke", [sys.executable, ".agent/scripts/enforcement_redteam_smoke.py"]),
)


def run_step(label: str, command: list[str]) -> None:
    print(f"VALIDATION_SUITE_STEP_START {label}", flush=True)
    proc = subprocess.run(command, cwd=str(ROOT), text=True)
    if proc.returncode != 0:
        raise SystemExit(f"VALIDATION_SUITE_FAIL {label} rc={proc.returncode}")
    print(f"VALIDATION_SUITE_STEP_OK {label}", flush=True)


def main() -> int:
    for label, command in STEPS:
        run_step(label, command)
    print("VALIDATION_SUITE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
