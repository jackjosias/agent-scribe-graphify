#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

WORKSPACE_HYGIENE_OK = "WORKSPACE_HYGIENE_OK"
PARENT_WORKSPACE_POLLUTION_FOUND = "PARENT_WORKSPACE_POLLUTION_FOUND"
PARENT_WORKSPACE_CLEANED = "PARENT_WORKSPACE_CLEANED"
PARENT_WORKSPACE_CLEANUP_REFUSED = "PARENT_WORKSPACE_CLEANUP_REFUSED"

TARGET_NAMES = (".agent", "graphify-out", "scribe-out")


def _git_root(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(current),
            text=True,
            capture_output=True,
            timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return Path(proc.stdout.strip()).resolve()


def _refusal(reason: str, root: Path, parent: Path | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "verdict": PARENT_WORKSPACE_CLEANUP_REFUSED,
        "reason": reason,
        "git_root": str(root),
        "parent": str(parent) if parent is not None else None,
        "would_remove": [],
        "removed": [],
    }


def inspect_parent_workspace(root: Path) -> dict[str, Any]:
    git_root = _git_root(root)
    if git_root is None:
        return _refusal("git root not found", root)
    parent = git_root.parent.resolve()
    if parent == git_root or parent == Path("/"):
        return _refusal("invalid parent for git root", git_root, parent)
    if (parent / ".git").exists():
        return _refusal("parent is itself a git repository", git_root, parent)
    if git_root.parent.resolve() != parent:
        return _refusal("computed parent mismatch", git_root, parent)

    found: dict[str, bool] = {}
    symlinks: list[str] = []
    would_remove: list[str] = []
    for name in TARGET_NAMES:
        path = parent / name
        exists = path.exists() or path.is_symlink()
        found[name] = exists
        if not exists:
            continue
        if path.is_symlink():
            symlinks.append(str(path))
            continue
        if path.parent.resolve() != parent:
            return _refusal("target parent mismatch", git_root, parent)
        would_remove.append(str(path))

    if symlinks:
        return {
            "ok": False,
            "verdict": PARENT_WORKSPACE_CLEANUP_REFUSED,
            "reason": "refusing symlink targets",
            "git_root": str(git_root),
            "parent": str(parent),
            "symlinks": symlinks,
            "found_parent_agent": found[".agent"],
            "found_parent_graphify_out": found["graphify-out"],
            "found_parent_scribe_out": found["scribe-out"],
            "would_remove": [],
            "removed": [],
        }

    verdict = PARENT_WORKSPACE_POLLUTION_FOUND if would_remove else WORKSPACE_HYGIENE_OK
    return {
        "ok": True,
        "verdict": verdict,
        "git_root": str(git_root),
        "parent": str(parent),
        "found_parent_agent": found[".agent"],
        "found_parent_graphify_out": found["graphify-out"],
        "found_parent_scribe_out": found["scribe-out"],
        "would_remove": would_remove,
        "removed": [],
    }


def clean_parent_workspace(root: Path, *, apply: bool = False) -> dict[str, Any]:
    report = inspect_parent_workspace(root)
    if not report.get("ok"):
        return report
    if not apply:
        return report
    removed: list[str] = []
    for raw_path in report.get("would_remove", []):
        path = Path(raw_path)
        if path.name not in TARGET_NAMES or path.is_symlink():
            return _refusal("refusing unexpected target during apply", Path(report["git_root"]), Path(report["parent"]))
        if path.parent.resolve() != Path(report["parent"]).resolve():
            return _refusal("target escaped parent during apply", Path(report["git_root"]), Path(report["parent"]))
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        removed.append(str(path))
    return {
        **report,
        "verdict": PARENT_WORKSPACE_CLEANED if removed else WORKSPACE_HYGIENE_OK,
        "removed": removed,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect and optionally remove parent workspace .agent/graphify-out/scribe-out pollution.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Inspect only. This is the default.")
    mode.add_argument("--apply", action="store_true", help="Remove only the exact polluted parent paths.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2], help="Project root or child path.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = clean_parent_workspace(args.root.resolve(), apply=bool(args.apply))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
