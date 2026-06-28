#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from scribe_output_paths import graphify_out_dir, scribe_out_dir


ACTIVE_SCRIBE_OUT_FILES = {
    "state.json",
    "scribe-dashboard.html",
    "scribe-dashboard-data.json",
    "scribe-doctor-report.md",
}
PROTECTED_SCRIBE_OUT_DIRS = {"archive", "bundle-graph", "locks"}
SCRIBE_NOISE_DIRS = {"commit-plan"}
SCRIBE_NOISE_PATTERNS = (
    "scribe-doctor-*-report.md",
    "scribe-export-*.json",
    "scribe-dashboard-*.png",
    "*-before-*",
    "*-after-*",
    "*-shim-*",
)
DEFAULT_MAX_AST_FILES = 50
AGENT_CACHE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}
AGENT_CACHE_SUFFIXES = {".pyc", ".pyo"}


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    reason: str


def is_scribe_noise_file(path: Path) -> bool:
    name = path.name
    if name in ACTIVE_SCRIBE_OUT_FILES:
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in SCRIBE_NOISE_PATTERNS)


def collect_scribe_noise(project_root: Path) -> list[CleanupCandidate]:
    scribe_out = scribe_out_dir(project_root)
    if not scribe_out.exists():
        return []
    candidates: list[CleanupCandidate] = []
    for path in sorted(scribe_out.iterdir()):
        if path.is_dir():
            if path.name in SCRIBE_NOISE_DIRS and path.name not in PROTECTED_SCRIBE_OUT_DIRS:
                candidates.append(CleanupCandidate(path, "scribe-out generated working directory"))
            continue
        if is_scribe_noise_file(path):
            candidates.append(CleanupCandidate(path, "scribe-out generated noise"))
    return candidates


def ast_cache_dirs(project_root: Path) -> list[Path]:
    dirs: list[Path] = []
    root_ast = graphify_out_dir(project_root) / "cache" / "ast"
    if root_ast.exists():
        dirs.append(root_ast)
    bundle_graph_root = scribe_out_dir(project_root) / "bundle-graph"
    if bundle_graph_root.exists():
        for path in sorted(bundle_graph_root.rglob("ast")):
            if path.is_dir() and path.parent.name == "cache":
                dirs.append(path)
    return dirs


def collect_ast_lru_noise(project_root: Path, max_files: int = DEFAULT_MAX_AST_FILES) -> list[CleanupCandidate]:
    candidates: list[CleanupCandidate] = []
    for ast_dir in ast_cache_dirs(project_root):
        files = sorted((path for path in ast_dir.iterdir() if path.is_file()), key=lambda path: (path.stat().st_mtime, path.name))
        overflow = len(files) - max_files
        if overflow <= 0:
            continue
        for path in files[:overflow]:
            candidates.append(CleanupCandidate(path, f"Graphify AST cache LRU overflow > {max_files}"))
    return candidates


def collect_agent_cache_noise(project_root: Path) -> list[CleanupCandidate]:
    agent_root = project_root / ".agent"
    if not agent_root.exists():
        return []
    candidates: list[CleanupCandidate] = []
    for path in sorted(agent_root.rglob("*")):
        if path.is_dir() and path.name in AGENT_CACHE_DIRS:
            candidates.append(CleanupCandidate(path, "portable .agent cache"))
        elif path.is_file() and path.suffix in AGENT_CACHE_SUFFIXES and not any(part in AGENT_CACHE_DIRS for part in path.parts):
            candidates.append(CleanupCandidate(path, "portable .agent bytecode"))
    return candidates


def build_cleanup_plan(project_root: Path, include_graphify: bool, max_ast_files: int, include_agent_cache: bool = False) -> list[CleanupCandidate]:
    candidates = collect_scribe_noise(project_root)
    if include_graphify:
        candidates.extend(collect_ast_lru_noise(project_root, max_ast_files))
    if include_agent_cache:
        candidates.extend(collect_agent_cache_noise(project_root))
    return candidates


def apply_cleanup(candidates: list[CleanupCandidate]) -> int:
    removed = 0
    for candidate in candidates:
        try:
            if candidate.path.is_dir():
                shutil.rmtree(candidate.path)
            else:
                candidate.path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
    return removed


def count_scribe_noise(project_root: Path) -> int:
    return len(collect_scribe_noise(project_root))


def print_scribe_noise_warning(project_root: Path, threshold: int = 5) -> None:
    count = count_scribe_noise(project_root)
    if count > threshold:
        print(f"  cleanup: {count} scribe-out noise file(s) detected; run `scribe clean --dry-run`.", file=sys.stderr)


def print_plan(candidates: list[CleanupCandidate], project_root: Path) -> None:
    if not candidates:
        print("scribe clean: nothing to remove")
        return
    print("scribe clean plan:")
    for candidate in candidates:
        try:
            rel_path = candidate.path.relative_to(project_root)
        except ValueError:
            rel_path = candidate.path
        print(f"  delete {rel_path}  # {candidate.reason}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scribe clean", description="Clean generated SCRIBE and Graphify noise safely.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="List files that would be deleted.")
    mode.add_argument("--apply", action="store_true", help="Delete generated noise files.")
    parser.add_argument("--graphify", action="store_true", help="Also prune Graphify AST caches with LRU retention.")
    parser.add_argument("--agent-cache", action="store_true", help="Also remove __pycache__ and bytecode from .agent.")
    parser.add_argument("--root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--max-ast-files", type=int, default=DEFAULT_MAX_AST_FILES, help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = Path(args.root).resolve()
    candidates = build_cleanup_plan(project_root, include_graphify=args.graphify, max_ast_files=args.max_ast_files, include_agent_cache=args.agent_cache)
    print_plan(candidates, project_root)
    if not args.apply:
        print("scribe clean: dry run complete")
        return 0
    removed = apply_cleanup(candidates)
    print(f"scribe clean: removed {removed} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
