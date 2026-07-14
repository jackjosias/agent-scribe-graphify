#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(os.environ.get("AGENT_SCRIBE_GRAPHIFY_ROOT", str(SOURCE_ROOT))).resolve()
os.chdir(PROJECT_ROOT)
MCP_ROOT = SOURCE_ROOT / ".agent" / "mcp"
if str(MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(MCP_ROOT))

from runtime.installation_state import inspect_installation_state, portable_tenor_init_action  # noqa: E402


def _emit_gate_failure(gate: dict[str, object]) -> None:
    action = portable_tenor_init_action(PROJECT_ROOT)
    payload = {
        "ok": False,
        "verdict": gate.get("verdict", "TENOR_INIT_REQUIRED"),
        "project_root": str(PROJECT_ROOT),
        "detection": gate.get("detection", {}),
        "next_action": gate.get("next_action") or action["display"],
        "next_action_argv": gate.get("next_action_argv") or action["argv"],
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr, flush=True)


def main() -> int:
    gate = inspect_installation_state(PROJECT_ROOT)
    if not gate.get("ready"):
        _emit_gate_failure(gate)
        return 78
    runpy.run_path(str(SOURCE_ROOT / ".agent" / "mcp" / "server_ext.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
