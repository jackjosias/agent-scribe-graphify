#!/usr/bin/env python3
from __future__ import annotations

import os
import runpy
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(os.environ.get("AGENT_SCRIBE_GRAPHIFY_ROOT", str(SOURCE_ROOT))).resolve()
os.chdir(PROJECT_ROOT)
runpy.run_path(str(SOURCE_ROOT / ".agent" / "mcp" / "server_ext.py"), run_name="__main__")
