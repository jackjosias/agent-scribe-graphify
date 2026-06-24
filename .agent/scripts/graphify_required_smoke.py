#!/usr/bin/env python3
"""graphify_required_smoke.py — End-to-end smoke for Graphify Mandatory Guard.

Scenarios:
  [1] Graphify binary missing      → GRAPHIFY_REQUIRED_NOT_INSTALLED, write blocked
  [2] Graphify installed, outputs missing → GRAPHIFY_OUTPUTS_MISSING, write blocked
  [3] Graphify ready               → GRAPHIFY_READY, write allowed
  [4] Install guide contains graphifyy (double y)
  [5] Install guide mentions opencode platform
  [6] No silent grep/ripgrep fallback in module
  [7] diagnostic_only flag set when graphify missing

Exit: 0 on all pass, 1 on any failure.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# ─── Configuration ────────────────────────────────────────────────────────────

_MCP_RUNTIME = Path(__file__).resolve().parent.parent / "mcp" / "runtime"
if str(_MCP_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_MCP_RUNTIME))

import graphify_guard as gg

_OK = "\033[32m\u2713\033[0m"
_FAIL = "\033[31m\u2717\033[0m"
_failures: list[str] = []
_passes: int = 0


# ─── Helpers ──────────────────────────────────────────────────────────────────

def check(label: str, condition: bool, detail: str = "") -> None:
    global _passes
    if condition:
        _passes += 1
        print(f"  {_OK}  [{_passes + len(_failures):02d}] {label}")
    else:
        _failures.append(label)
        print(f"  {_FAIL}  [{_passes + len(_failures):02d}] {label}" + (f" \u2014 {detail}" if detail else ""))


def make_fake_workspace() -> Path:
    """Create a temp directory with a .agent/ skeleton (no graphify-out)."""
    tmp = Path(tempfile.mkdtemp(prefix="graphify-smoke-"))
    (tmp / ".agent" / "docs").mkdir(parents=True)
    (tmp / ".agent" / "mcp" / "runtime").mkdir(parents=True)
    (tmp / ".agent" / "state" / "runtime").mkdir(parents=True)
    return tmp


def make_graphify_out(ws: Path) -> None:
    """Create a valid graphify-out/ with all required files."""
    out = ws / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text('{"nodes": [], "edges": []}', encoding="utf-8")
    (out / "GRAPH_REPORT.md").write_text("# Graph Report\n", encoding="utf-8")
    (out / "graph.html").write_text("<html></html>\n", encoding="utf-8")


def fake_graphify_binary(ws: Path) -> Path:
    """Create a fake `graphify` script on a temp PATH."""
    bindir = ws / "bin"
    bindir.mkdir()
    script = bindir / "graphify"
    script.write_text("#!/bin/sh\necho '0.5.0'\n", encoding="utf-8")
    script.chmod(0o755)
    return bindir


# ─── Smoke 1: Binary missing ─────────────────────────────────────────────────

def smoke_01_binary_missing_blocks_write() -> None:
    tmp = make_fake_workspace()
    try:
        # Ensure graphify is NOT on PATH for this test.
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = "/dev/null"  # Force empty PATH.
        try:
            result = gg.check_graphify_required(tmp, host_type="opencode", auto_write_guide=False)
        finally:
            os.environ["PATH"] = old_path

        ok = (
            result.get("ok") is False
            and result.get("verdict") == gg.VERDICT_BINARY_MISSING
            and result.get("blocking") is True
            and result.get("write_allowed") is False
            and result.get("diagnostic_only") is True
        )
        check("[1] binary missing blocks write", ok, str(result.get("verdict", "")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── Smoke 2: Binary present, outputs missing ────────────────────────────────

def smoke_02_outputs_missing_blocks_write() -> None:
    tmp = make_fake_workspace()
    try:
        bindir = fake_graphify_binary(tmp)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bindir) + os.pathsep + old_path
        try:
            result = gg.check_graphify_required(tmp, host_type="opencode", auto_write_guide=False)
        finally:
            os.environ["PATH"] = old_path

        ok = (
            result.get("ok") is False
            and result.get("verdict") == gg.VERDICT_OUTPUTS_MISSING
            and result.get("blocking") is True
            and result.get("write_allowed") is False
        )
        check("[2] outputs missing blocks write", ok, str(result.get("verdict", "")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── Smoke 3: Everything ready ───────────────────────────────────────────────

def smoke_03_ready_allows_write() -> None:
    tmp = make_fake_workspace()
    make_graphify_out(tmp)
    try:
        bindir = fake_graphify_binary(tmp)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bindir) + os.pathsep + old_path
        try:
            result = gg.check_graphify_required(tmp, host_type="opencode", auto_write_guide=False)
        finally:
            os.environ["PATH"] = old_path

        ok = (
            result.get("ok") is True
            and result.get("verdict") == gg.VERDICT_READY
            and result.get("blocking") is False
            and result.get("write_allowed") is True
        )
        check("[3] ready allows write", ok, str(result.get("verdict", "")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── Smoke 4: Guide contains graphifyy (double y) ────────────────────────────

def smoke_04_guide_mentions_graphifyy() -> None:
    guide = gg.render_graphify_install_guide(host_type="opencode", os_name="linux")
    ok = "graphifyy" in guide and "graphify" in guide
    check("[4] guide mentions graphifyy (double y)", ok)
    if not ok:
        # Show first 200 chars for debugging.
        check("[4] detail", False, guide[:200])


# ─── Smoke 5: Guide mentions opencode platform ───────────────────────────────

def smoke_05_guide_mentions_opencode() -> None:
    guide = gg.render_graphify_install_guide(host_type="opencode")
    ok = "opencode" in guide.lower() and "--platform opencode" in guide
    check("[5] guide mentions opencode platform", ok)


# ─── Smoke 6: No silent grep/ripgrep fallback ────────────────────────────────

def smoke_06_no_silent_grep_fallback() -> None:
    """Verify the guard module never mentions grep or ripgrep as a Graphify replacement."""
    module_path = Path(gg.__file__).resolve()
    content = module_path.read_text(encoding="utf-8")
    # The module may mention grep in comments explaining why NOT to use it.
    # We check that no function returns a verdict suggesting grep as a Graphify alternative.
    has_grep_replacement = False
    for line in content.splitlines():
        stripped = line.strip().lower()
        if "grep" in stripped or "ripgrep" in stripped:
            # Allow comments that explain WHY not to use grep.
            if "no silent fallback" in stripped or "doctrine" in stripped or "fallback" in stripped:
                continue
            has_grep_replacement = True
    check("[6] no silent grep/ripgrep fallback", not has_grep_replacement,
          "grep/ripgrep found outside doctrine comments" if has_grep_replacement else "")


# ─── Smoke 7: Diagnostic mode flag ───────────────────────────────────────────

def smoke_07_diagnostic_flag_when_missing() -> None:
    tmp = make_fake_workspace()
    try:
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = "/dev/null"
        try:
            result = gg.check_graphify_required(tmp, host_type="opencode", auto_write_guide=False)
        finally:
            os.environ["PATH"] = old_path

        ok = result.get("diagnostic_only") is True
        check("[7] diagnostic_only flag set when graphify missing", ok)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── Smoke 8: graph.json invalid JSON ──────────────────────────────────────────

def smoke_08_graph_json_invalid_blocks() -> None:
    tmp = make_fake_workspace()
    try:
        bindir = fake_graphify_binary(tmp)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bindir) + os.pathsep + old_path
        try:
            out = tmp / "graphify-out"
            out.mkdir()
            (out / "graph.json").write_text("INVALID_JSON{{{", encoding="utf-8")
            (out / "GRAPH_REPORT.md").write_text("# Report\n", encoding="utf-8")
            (out / "graph.html").write_text("<html></html>\n", encoding="utf-8")
            result = gg.check_graphify_required(tmp, host_type="opencode", auto_write_guide=False)
        finally:
            os.environ["PATH"] = old_path

        ok = (
            result.get("ok") is False
            and result.get("verdict") == gg.VERDICT_GRAPH_INVALID_JSON
            and result.get("blocking") is True
        )
        check("[8] graph.json invalid JSON blocks write", ok, str(result.get("verdict", "")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── Smoke 9: graph.json missing nodes key ─────────────────────────────────────

def smoke_09_graph_json_missing_nodes_blocks() -> None:
    tmp = make_fake_workspace()
    try:
        bindir = fake_graphify_binary(tmp)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bindir) + os.pathsep + old_path
        try:
            out = tmp / "graphify-out"
            out.mkdir()
            (out / "graph.json").write_text('{"edges": []}', encoding="utf-8")
            (out / "GRAPH_REPORT.md").write_text("# Report\n", encoding="utf-8")
            (out / "graph.html").write_text("<html></html>\n", encoding="utf-8")
            result = gg.check_graphify_required(tmp, host_type="opencode", auto_write_guide=False)
        finally:
            os.environ["PATH"] = old_path

        ok = (
            result.get("ok") is False
            and result.get("verdict") == gg.VERDICT_GRAPH_INVALID_JSON
            and result.get("blocking") is True
        )
        check("[9] graph.json missing nodes key blocks write", ok, str(result.get("verdict", "")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── Smoke 10: Binary exists but unresponsive ─────────────────────────────────

def smoke_10_binary_unresponsive_blocks() -> None:
    """Create a fake graphify that always fails → GRAPHIFY_BINARY_CHECK_FAILED."""
    tmp = make_fake_workspace()
    try:
        bindir = tmp / "bin"
        bindir.mkdir()
        script = bindir / "graphify"
        script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        script.chmod(0o755)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bindir) + os.pathsep + old_path
        try:
            result = gg.check_graphify_required(tmp, host_type="opencode", auto_write_guide=False)
        finally:
            os.environ["PATH"] = old_path

        ok = (
            result.get("ok") is False
            and result.get("verdict") == gg.VERDICT_BINARY_FAILED
            and result.get("blocking") is True
        )
        check("[10] unresponsive binary blocks write", ok, str(result.get("verdict", "")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    print(f"\n{'='*60}")
    print(f"  GRAPHIFY REQUIRED SMOKE — V2.15.1")
    print(f"  module  : {gg.__file__}")
    print(f"  platform: {sys.platform}")
    print(f"  python  : {sys.version.split()[0]}")
    print(f"{'='*60}\n")

    smoke_01_binary_missing_blocks_write()
    smoke_02_outputs_missing_blocks_write()
    smoke_03_ready_allows_write()
    smoke_04_guide_mentions_graphifyy()
    smoke_05_guide_mentions_opencode()
    smoke_06_no_silent_grep_fallback()
    smoke_07_diagnostic_flag_when_missing()
    smoke_08_graph_json_invalid_blocks()
    smoke_09_graph_json_missing_nodes_blocks()
    smoke_10_binary_unresponsive_blocks()

    total = _passes + len(_failures)
    print(f"\n{'='*60}")
    print(f"  Result: {_passes}/{total} passed, {len(_failures)} failed")
    if _failures:
        print(f"  Failed: {', '.join(_failures)}")
        print(f"  SMOKE STATUS: GRAPHIFY_REQUIRED_SMOKE_FAIL")
    else:
        print(f"  SMOKE STATUS: GRAPHIFY_REQUIRED_SMOKE_OK")
    print(f"{'='*60}\n")

    return 0 if not _failures else 1


if __name__ == "__main__":
    sys.exit(main())
