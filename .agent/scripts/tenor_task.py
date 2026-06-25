#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_HOST_ADAPTER = _HERE.parent / "host_adapter"
if str(_HOST_ADAPTER) not in sys.path:
    sys.path.insert(0, str(_HOST_ADAPTER))

from tenor_task_prompt import generate_task_prompt  # type: ignore  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a disciplined TENOR task prompt.",
    )
    parser.add_argument(
        "--task", "-t",
        type=str,
        default="",
        help="Description of the task (required).",
    )
    parser.add_argument(
        "--mode", "-m",
        type=str,
        default="STANDARD",
        choices=["NANO", "QUICK", "STANDARD", "CRITICAL"],
        help="Task mode (default: STANDARD).",
    )
    parser.add_argument(
        "--intent", "-i",
        type=str,
        default="write",
        choices=["read", "write", "refactor", "delete", "test", "debug"],
        help="Task intent (default: write).",
    )
    parser.add_argument(
        "--resource", "-r",
        type=str,
        default="",
        help="Target resource path (optional).",
    )
    parser.add_argument(
        "--model-tier",
        type=str,
        default="large",
        choices=["small", "large", "unknown"],
        help="Model tier (default: large).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output full JSON instead of human-readable prompt.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    result = generate_task_prompt(
        task=args.task,
        mode=args.mode,
        intent=args.intent,
        resource=args.resource,
        model_tier=args.model_tier,
    )

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if not result.get("ok"):
            print(f"ERREUR : {result.get('verdict', 'TENOR_TASK_PROMPT_INVALID')}")
            print(f"Raison : {result.get('reason', 'unknown')}")
            return 1

        print(result.get("prompt", ""))
        print()
        print("---")
        print(f"Verdict : {result['verdict']}")
        print(f"Premieres actions requises : {', '.join(result['required_first_actions'])}")
        print(f"Dernieres actions requises : {', '.join(result['required_finish_actions'])}")
        print(f"Interdictions : {', '.join(result['forbidden'])}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
