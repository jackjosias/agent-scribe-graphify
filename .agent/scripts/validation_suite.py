#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import graphify_readiness

# The suite is sequential because these tests exercise one real runtime and
# intentionally verify lock, claim and database semantics. mcp_smoke is a full
# integration smoke; terrain measurements justify the external 180s budget.
STEPS = (
    ("validation_runtime_lock", [sys.executable, ".agent/mcp/tests/test_validation_runtime_lock.py"]),
    ("installation_state", [sys.executable, ".agent/mcp/tests/test_installation_state.py"]),
    ("tenor_init_orchestrator", [sys.executable, ".agent/mcp/tests/test_tenor_init_orchestrator.py"]),
    ("v216_cross_platform", [sys.executable, ".agent/mcp/tests/test_v216_cross_platform.py"]),
    ("graphify_readiness", [sys.executable, ".agent/mcp/tests/test_graphify_readiness.py"]),
    ("graphify_scribe_bridge", [sys.executable, ".agent/tests/test_graphify_scribe_bridge.py"]),
    ("scribe_bootstrap", [sys.executable, ".agent/workflow/scribe/sel/tests/test_scribe_bootstrap.py"]),
    ("host_adapter_autoguard", [sys.executable, ".agent/mcp/tests/test_host_adapter_autoguard.py"]),
    ("agent_runtime_sync", [sys.executable, ".agent/tests/test_agent_runtime_sync.py"]),
    ("mcp_smoke", [sys.executable, ".agent/scripts/mcp_smoke.py"]),
    ("enforcement_redteam_smoke", [sys.executable, ".agent/scripts/enforcement_redteam_smoke.py"]),
)

_GRAPHIFY_SCOPED_STEPS = {"mcp_smoke", "enforcement_redteam_smoke"}


def run_step(label: str, command: list[str]) -> None:
    print(f"VALIDATION_SUITE_STEP_START {label}", flush=True)
    fixture_scope = (
        graphify_readiness.smoke_fixture_scope(ROOT)
        if label in _GRAPHIFY_SCOPED_STEPS
        else nullcontext()
    )
    with fixture_scope:
        process = subprocess.run(command, cwd=str(ROOT), text=True, check=False)
    if process.returncode != 0:
        raise SystemExit(f"VALIDATION_SUITE_FAIL {label} rc={process.returncode}")
    print(f"VALIDATION_SUITE_STEP_OK {label}", flush=True)


def main() -> int:
    for label, command in STEPS:
        run_step(label, command)
    print("VALIDATION_SUITE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
