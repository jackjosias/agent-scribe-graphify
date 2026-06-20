#!/usr/bin/env python3
from __future__ import annotations

import os
import runpy
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.chdir(PROJECT_ROOT)
runpy.run_path(str(PROJECT_ROOT / ".agent" / "mcp" / "server_ext.py"), run_name="__main__")
