#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent.parent
_ENTRY = _PROJECT_ROOT / ".agent" / "mcp" / "server_entry.py"

if not _ENTRY.exists():
    # Fallback: try relative to .agent/scripts
    _FALLBACK = _HERE.parent / "mcp" / "server_entry.py"
    if _FALLBACK.exists():
        _ENTRY = _FALLBACK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compatibility diagnostic for the TENOR bridge. Normal host sessions "
            "must call the host-visible MCP tool directly."
        ),
    )
    parser.add_argument(
        "--agent-session-id", "-a",
        type=str,
        default="",
        required=True,
        help="Agent session ID from TENOR INIT SCRIBE-CHECK output.",
    )
    parser.add_argument(
        "--host-tool", "-t",
        type=str,
        default="unknown",
        help="Host tool identifier (opencode, codex, claude, etc.).",
    )
    parser.add_argument(
        "--model-name", "-m",
        type=str,
        default="",
        help="Model name (optional).",
    )
    parser.add_argument(
        "--proof-token", "-p",
        type=str,
        default="",
        help="Proof token from TENOR INIT SCRIBE-CHECK output.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output raw JSON result from MCP tool.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if not _ENTRY.exists():
        print(f"ERREUR : MCP entry script not found at {_ENTRY}", file=sys.stderr)
        return 1

    call_args = json.dumps({
        "agent_session_id": args.agent_session_id,
        "host_tool": args.host_tool,
        "model_name": args.model_name,
        "proof_token": args.proof_token,
    })

    try:
        proc = subprocess.run(
            [sys.executable, str(_ENTRY), "--call", "tenor_init_bridge", "--args", call_args],
            cwd=str(_PROJECT_ROOT),
            text=True, capture_output=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        print("ERREUR : MCP tool call timed out after 30s", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERREUR : subprocess failed: {exc}", file=sys.stderr)
        return 1

    if proc.returncode != 0:
        print(f"ERREUR : MCP tool exited with code {proc.returncode}", file=sys.stderr)
        print(proc.stderr.strip(), file=sys.stderr)
        return 1

    try:
        outer = json.loads(proc.stdout.strip())
    except json.JSONDecodeError as exc:
        print(f"ERREUR : JSON parse failed: {exc}", file=sys.stderr)
        print(proc.stdout.strip(), file=sys.stderr)
        return 1

    # Unwrap MCP content envelope
    if isinstance(outer, dict) and "content" in outer:
        content_list = outer["content"]
        if content_list and isinstance(content_list, list):
            text_val = content_list[0].get("text", "")
            try:
                outer = json.loads(text_val)
            except json.JSONDecodeError:
                pass

    if args.as_json:
        print(json.dumps(outer, ensure_ascii=False, indent=2))
    elif outer.get("ok"):
        verdict = outer.get("verdict", "TENOR_INIT_READY")
        aid = outer.get("agent_session_id", args.agent_session_id)
        steps = outer.get("steps", [])
        print(f"Verdict : {verdict}")
        print(f"Agent   : {aid}")
        print(f"Etapes  : {len(steps)}")
        for s in steps:
            step_name = s.get("step", "?")
            step_ok = s.get("ok", False)
            icon = "OK" if step_ok else "FAIL"
            detail = s.get("verdict") or s.get("status") or s.get("error") or ""
            print(f"  [{icon}] {step_name}{' : ' + detail if detail else ''}")
        print(f"Terminal: {bool(outer.get('terminal'))}")
        if not all(s.get("ok") for s in steps):
            print("ATTENTION : certaines etapes ont echoue.")
            return 1
    else:
        print(f"ERREUR : {outer.get('verdict', 'UNKNOWN')}")
        print(f"Raison : {outer.get('reason', 'unknown')}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
