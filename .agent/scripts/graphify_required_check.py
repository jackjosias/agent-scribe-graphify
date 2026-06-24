#!/usr/bin/env python3
"""graphify_required_check.py — CLI for Graphify Mandatory Guard.

Usage:
    python3 .agent/scripts/graphify_required_check.py [--json] [--host TYPE] [--write-guide]

Exits 0 if Graphify is ready, 1 if not ready (blocking).

Outputs:
    --json      → single JSON line with the full check result
    (default)   → human-readable summary
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add mcp/runtime to sys.path so we can import graphify_guard.
_MCP_RUNTIME = Path(__file__).resolve().parent.parent / "mcp" / "runtime"
if str(_MCP_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_MCP_RUNTIME))

try:
    import graphify_guard as gg
except ImportError as exc:
    # Fallback: try from the parent (scripts) context.
    _SCRIPTS_DIR = Path(__file__).resolve().parent
    _AGENT_DIR = _SCRIPTS_DIR.parent
    _MCP_DIR = _AGENT_DIR / "mcp"
    if str(_MCP_DIR) not in sys.path:
        sys.path.insert(0, str(_MCP_DIR))
    if str(_AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(_AGENT_DIR))
    try:
        from runtime import graphify_guard as gg  # type: ignore
    except ImportError:
        print(json.dumps({
            "ok": False,
            "verdict": "GRAPHIFY_GUARD_MODULE_MISSING",
            "error": f"Cannot import graphify_guard: {exc}",
        }))
        sys.exit(2)


def print_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Graphify Mandatory Guard Check")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    parser.add_argument("--host", type=str, default="unknown", help="Agent host type (opencode, codex, gemini, claude)")
    parser.add_argument("--write-guide", action="store_true", default=True, help="Write install guide if missing (default: true)")
    parser.add_argument("--no-write-guide", action="store_false", dest="write_guide", help="Skip writing install guide")
    args = parser.parse_args()

    result = gg.check_graphify_required(
        workspace_root=None,  # auto-detect from cwd
        host_type=args.host,
        auto_write_guide=args.write_guide,
    )

    if args.json:
        print_json(result)
        return 0 if result.get("ok") else 1

    verdict = result.get("verdict", "UNKNOWN")
    ok = result.get("ok", False)
    blocking = result.get("blocking", False)

    if ok and verdict == gg.VERDICT_READY:
        binary = result.get("binary", {})
        version = binary.get("version") or "unknown"
        print(f"GRAPHIFY_READY — version={version}")
        print(f"  Binary  : {binary.get('binary_path') or 'unknown'}")
        print(f"  Outputs : {result.get('outputs', {}).get('output_dir', '')}")
        return 0

    if verdict == gg.VERDICT_BINARY_MISSING:
        print("GRAPHIFY_REQUIRED_NOT_INSTALLED")
        print("  Graphify CLI is not installed. Install it to proceed with write/refactor/delete operations.")
        print(f"  Install guide: {result.get('install_guide', 'N/A')}")
        print("  Next actions:")
        for action in result.get("next_actions", []):
            print(f"    - {action}")
        return 1

    if verdict == gg.VERDICT_GRAPH_INVALID_JSON:
        print("GRAPHIFY_GRAPH_JSON_INVALID")
        print("  graph.json is corrupted or missing required 'nodes' key.")
        for action in result.get("next_actions", []):
            print(f"    - {action}")
        return 1

    if verdict == gg.VERDICT_BINARY_FAILED:
        print("GRAPHIFY_BINARY_CHECK_FAILED")
        print("  Graphify binary found but does not respond to --version or --help.")
        for action in result.get("next_actions", []):
            print(f"    - {action}")
        return 1

    if verdict == gg.VERDICT_OUTPUTS_MISSING:
        print("GRAPHIFY_OUTPUTS_MISSING")
        print("  Graphify is installed but graphify-out/ is missing or incomplete.")
        for action in result.get("next_actions", []):
            print(f"    - {action}")
        return 1

    print(f"GRAPHIFY_CHECK_FAILED — verdict={verdict}, ok={ok}, blocking={blocking}")
    if not args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    sys.exit(main())
