#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MCP_ROOT = PROJECT_ROOT / ".agent" / "mcp"
if str(MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(MCP_ROOT))

from runtime.runtime_backup_retention import (  # noqa: E402
    cleanup_runtime_backups,
    organize_runtime_backups,
    runtime_backup_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and optionally clean .agent runtime SQLite backups.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Report planned actions without moving or deleting files.")
    mode.add_argument("--apply", action="store_true", help="Apply the requested organize or cleanup action.")
    parser.add_argument("--organize", action="store_true", help="Move top-level coordination.backup-* files into runtime/backups/.")
    parser.add_argument("--cleanup", action="store_true", help="Delete old backups from runtime/backups/ after keep-last.")
    parser.add_argument("--keep-last", type=int, default=20, help="Number of newest backups to keep during cleanup.")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="Project root. Defaults to this repository.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    apply = bool(args.apply)

    try:
        if args.organize:
            result = organize_runtime_backups(args.root.resolve(), apply=apply)
        elif args.cleanup:
            result = cleanup_runtime_backups(args.root.resolve(), keep_last=args.keep_last, apply=apply)
        else:
            result = runtime_backup_report(args.root.resolve(), keep_last=args.keep_last)
            result["apply"] = False
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "verdict": "RUNTIME_BACKUP_DOCTOR_ERROR", "reason": str(exc)}

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
