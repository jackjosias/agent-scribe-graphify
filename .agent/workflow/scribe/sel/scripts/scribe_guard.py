#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scribe_doctor_lib import run_doctor


DEFAULT_SCRIBE_PATH = Path("AGENT-MEMOIRE_PROJECT_STATUS.scribe")
REPORT_DIR = Path("scribe-out")
BEFORE_REPORT_PATH = REPORT_DIR / "scribe-doctor-before-report.md"
AFTER_REPORT_PATH = REPORT_DIR / "scribe-doctor-after-report.md"


def print_usage() -> None:
    print("Usage: scribe guard [SCRIBE_PATH] -- <command> [args...]")
    print("Example: scribe guard -- test -f AGENT-MEMOIRE_PROJECT_STATUS.scribe")


def parse_args(raw_args: list[str]) -> tuple[Path, list[str]] | None:
    if "--" not in raw_args:
        return None

    separator_index = raw_args.index("--")
    scribe_args = raw_args[:separator_index]
    command = raw_args[separator_index + 1:]

    if len(scribe_args) > 1 or not command:
        return None

    scribe_path = Path(scribe_args[0]) if scribe_args else DEFAULT_SCRIBE_PATH
    return scribe_path, command


def run_guarded_command(command: list[str]) -> int:
    result = subprocess.run(command, check=False)
    return result.returncode


def main() -> int:
    parsed = parse_args(sys.argv[1:])
    if parsed is None:
        print_usage()
        return 2

    scribe_path, command = parsed
    before_status = run_doctor(scribe_path, BEFORE_REPORT_PATH, suggest_fix=True)
    if before_status != 0:
        print("scribe guard: stopped before command because pre-doctor found errors.", file=sys.stderr)
        return before_status

    command_status = run_guarded_command(command)
    after_status = run_doctor(scribe_path, AFTER_REPORT_PATH, suggest_fix=True)

    if after_status != 0:
        print("scribe guard: command finished but post-doctor found errors.", file=sys.stderr)
        return after_status
    if command_status != 0:
        print(f"scribe guard: command failed with exit code {command_status}.", file=sys.stderr)
        return command_status

    print("scribe guard: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
