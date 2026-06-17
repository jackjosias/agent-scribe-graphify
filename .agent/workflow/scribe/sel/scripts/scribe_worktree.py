#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path


GENERATED_PREFIXES = (
    ".next/",
    "dist/",
    "build/",
    "coverage/",
    "graphify-out/",
    ".agent/workflow/scribe/sel/graphify-out/",
    ".agent/workflow/scribe/rag/graphify-out/",
    "scribe-out/",
    "node_modules/",
    "backend/dist/",
    "backend/node_modules/",
    "frontend/.next/",
    "frontend/node_modules/",
)
GENERATED_SUFFIXES = (".pyc", ".pyo", ".tsbuildinfo")
SOURCE_HINT_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".yml", ".yaml", ".toml", ".prisma")
SOURCE_HINT_FILENAMES = {".graphifyignore", "root.graphifyignore", "scribe"}
SURFACE_MAP = {
    "auth": (
        "src/auth/",
        "src/middleware/",
        "backend/src/domain/auth/",
        "backend/src/application/auth/",
        "backend/src/presentation/api/routes/auth",
        "backend/src/presentation/api/middlewares/auth",
    ),
    "websocket": (
        "src/websocket/",
        "src/socket/",
        "backend/src/presentation/websocket/",
        "backend/src/socket/",
    ),
    "frontend": (
        "frontend/src/",
        "src/app/",
        "src/core/",
        "src/shared/",
    ),
    "tests": (
        "tests/",
        "backend/tests/",
        "frontend/tests/",
        "*.test.ts",
        "*.test.tsx",
        "*.spec.ts",
        "*.spec.tsx",
        "*.test.js",
        "*.spec.js",
    ),
    "scribe": (
        "AGENT-MEMOIRE_PROJECT_STATUS.scribe",
        "AGENTS.md",
        ".agent/",
    ),
    "integration": (
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    ),
}


@dataclass(frozen=True)
class StatusItem:
    code: str
    path: str

    @property
    def tracked(self) -> bool:
        return self.code != "??"


def git_status() -> list[StatusItem]:
    result = subprocess.run(["git", "status", "--short", "--untracked-files=all"], check=False, text=True, stdout=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError("git status failed")
    return [parse_status_line(line) for line in result.stdout.splitlines() if line.strip()]


def parse_status_line(line: str) -> StatusItem:
    code = line[:2]
    path = line[3:]
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return StatusItem(code=code, path=path)


def is_generated(path: str) -> bool:
    normalized = path.lstrip("/")
    return normalized.endswith(GENERATED_SUFFIXES) or any(normalized.startswith(prefix) for prefix in GENERATED_PREFIXES)


def is_source_candidate(path: str) -> bool:
    item = Path(path)
    return item.name in SOURCE_HINT_FILENAMES or item.suffix in SOURCE_HINT_SUFFIXES or path.endswith(".old") or "/" not in path


def classify(items: list[StatusItem]) -> tuple[list[StatusItem], list[StatusItem], list[StatusItem], list[StatusItem]]:
    tracked = [item for item in items if item.tracked]
    generated = [item for item in items if not item.tracked and is_generated(item.path)]
    untracked_source = [
        item for item in items if not item.tracked and not is_generated(item.path) and is_source_candidate(item.path)
    ]
    other = [
        item for item in items if not item.tracked and not is_generated(item.path) and not is_source_candidate(item.path)
    ]
    return tracked, untracked_source, generated, other


def matches_surface(path: str, pattern: str) -> bool:
    normalized = path.lstrip("/")
    if any(char in pattern for char in "*?["):
        return fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(Path(normalized).name, pattern)
    return normalized == pattern or normalized.startswith(pattern)


def owned_surface(path: str) -> str | None:
    for surface, patterns in SURFACE_MAP.items():
        if any(matches_surface(path, pattern) for pattern in patterns):
            return surface
    return None


def check_surface_violations(surface: str, agent_id: str, items: list[StatusItem] | None = None) -> list[dict[str, str]]:
    changed_items = items if items is not None else git_status()
    violations: list[dict[str, str]] = []
    for item in changed_items:
        if is_generated(item.path):
            continue
        owner = owned_surface(item.path)
        if owner and owner != surface:
            violations.append({"file": item.path, "belongs_to": owner, "claimed_by": agent_id})
    return violations


def print_surface_violations(violations: list[dict[str, str]], limit: int) -> None:
    print(f"  surface_violations: {len(violations)}")
    for violation in violations[:limit]:
        print(
            "  BLOCK "
            f"{violation['file']} belongs_to={violation['belongs_to']} "
            f"claimed_by={violation['claimed_by']}"
        )
    if len(violations) > limit:
        print(f"  ... {len(violations) - limit} more")


def print_group(title: str, items: list[StatusItem], limit: int) -> None:
    print(f"{title}: {len(items)}")
    for item in items[:limit]:
        print(f"  {item.code} {item.path}")
    if len(items) > limit:
        print(f"  ... {len(items) - limit} more")


def main() -> int:
    parser = argparse.ArgumentParser(prog="scribe worktree", description="Classify Git worktree changes for agents.")
    parser.add_argument("--strict", action="store_true", help="Fail when tracked or untracked source changes exist.")
    parser.add_argument("--limit", type=int, default=40, help="Maximum rows per group.")
    parser.add_argument("--surface", choices=sorted(SURFACE_MAP), help="Exclusive surface claimed by this agent.")
    parser.add_argument("--agent", help="Agent identifier used in surface violation reports.")
    args = parser.parse_args()
    if bool(args.surface) != bool(args.agent):
        parser.error("--surface and --agent must be used together")

    items = git_status()
    tracked, untracked_source, generated, other = classify(items)

    print("SCRIBE WORKTREE")
    print_group("  tracked_changes", tracked, args.limit)
    print_group("  untracked_source_candidates", untracked_source, args.limit)
    print_group("  generated_noise", generated, args.limit)
    print_group("  other_untracked", other, args.limit)
    surface_violations = check_surface_violations(args.surface, args.agent, tracked + untracked_source) if args.surface else []
    if args.surface:
        print_surface_violations(surface_violations, args.limit)

    if surface_violations:
        print("  verdict: SURFACE_VIOLATION")
        return 1
    if args.strict and (tracked or untracked_source):
        print("  verdict: DIRTY")
        return 1
    print("  verdict: REVIEW" if tracked or untracked_source else "  verdict: CLEAN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
