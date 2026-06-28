#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from scribe_output_paths import scribe_out_dir

from scribe_doctor_lib import run_doctor


DEFAULT_REPORT_PATH = scribe_out_dir(Path.cwd()) / "scribe-doctor-report.md"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="scribe doctor",
        description="Validate AGENT-MEMOIRE_PROJECT_STATUS.scribe without mutating it.",
    )
    parser.add_argument(
        "scribe_path",
        nargs="?",
        default="AGENT-MEMOIRE_PROJECT_STATUS.scribe",
        help="Path to the SCRIBE YAML file.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_REPORT_PATH),
        help="Markdown report path.",
    )
    parser.add_argument(
        "--suggest-fix",
        action="store_true",
        help="Include non-mutating repair suggestions in the report.",
    )
    args = parser.parse_args()

    return run_doctor(
        scribe_path=Path(args.scribe_path),
        report_path=Path(args.output),
        suggest_fix=args.suggest_fix,
    )


if __name__ == "__main__":
    raise SystemExit(main())
