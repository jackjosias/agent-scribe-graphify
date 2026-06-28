#!/usr/bin/env python3
from __future__ import annotations

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
if os.environ.get("AGENT_DISABLE_RELOCATION_PURGE") != "1":
    from runtime.installation_state import ensure_fresh_installation_state

    result = ensure_fresh_installation_state(PROJECT_ROOT)
    if not result.get("ok"):
        raise SystemExit(result.get("verdict", "PROJECT_BOUND_STATE_PURGE_REFUSED"))
runpy.run_path(str(SOURCE_ROOT / ".agent" / "mcp" / "server_ext.py"), run_name="__main__")
