#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import shutil
import subprocess
import tempfile
from pathlib import Path

from scribe_output_paths import scribe_out_dir


SEL_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = SEL_ROOT.parent


def find_project_root(path: Path) -> Path:
    for ancestor in path.parents:
        if ancestor.name == ".agent":
            return ancestor.parent
    raise RuntimeError(f"Cannot resolve project root from {path}")


PROJECT_ROOT = find_project_root(BUNDLE_ROOT)
BUNDLE_GRAPH_DIR = scribe_out_dir(PROJECT_ROOT) / "bundle-graph" / BUNDLE_ROOT.name
DEFAULT_BUDGET = 1200
GRAPH_SKIP_DIRS = {"adapters", "graphify-out", "__pycache__", ".pytest_cache", ".mypy_cache", "vendor"}
GRAPH_SKIP_PATTERNS = {"*.pyc", "*.pyo", "*.min.js", "*.min.css", "*.map"}


def should_skip_bundle_graph_path(path: Path) -> bool:
    return any(part in GRAPH_SKIP_DIRS for part in path.parts) or any(
        fnmatch.fnmatch(path.name, pattern) for pattern in GRAPH_SKIP_PATTERNS
    )


def bundle_graph_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if should_skip_bundle_graph_path(Path(name))}


def copy_bundle_without_graph(target: Path) -> None:
    shutil.copytree(BUNDLE_ROOT, target, ignore=bundle_graph_ignore)


def run_graphify_update(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["graphify", "update", str(target)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def bundle_command_hint() -> str:
    try:
        return str((BUNDLE_ROOT.relative_to(PROJECT_ROOT) / "scribe"))
    except ValueError:
        return str(BUNDLE_ROOT / "scribe")


def build_graph() -> int:
    with tempfile.TemporaryDirectory(prefix="scribe-bundle-graph-") as tmp:
        mirror = Path(tmp) / "bundle"
        copy_bundle_without_graph(mirror)
        result = run_graphify_update(mirror)
        if result.returncode != 0:
            print(result.stdout.rstrip())
            return result.returncode
        source_graph = mirror / "graphify-out"
        if not source_graph.exists():
            print("SCRIBE GRAPH: Graphify produced no graphify-out directory.")
            return 1
        BUNDLE_GRAPH_DIR.parent.mkdir(parents=True, exist_ok=True)
        if BUNDLE_GRAPH_DIR.exists():
            shutil.rmtree(BUNDLE_GRAPH_DIR)
        shutil.copytree(source_graph, BUNDLE_GRAPH_DIR)
    print(f"SCRIBE GRAPH: built {BUNDLE_GRAPH_DIR}")
    return 0


def query_graph(text: str, budget: int) -> int:
    graph_path = BUNDLE_GRAPH_DIR / "graph.json"
    if not graph_path.exists():
        print(f"SCRIBE GRAPH: missing bundle graph; run `{bundle_command_hint()} graph --build` first.")
        return 2
    result = subprocess.run(
        ["graphify", "query", text, "--budget", str(budget), "--graph", str(graph_path)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(result.stdout.rstrip())
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(prog="scribe graph", description="Build or query the SCRIBE bundle graph.")
    parser.add_argument("--build", action="store_true", help="Build a separate Graphify graph for the SCRIBE bundle.")
    parser.add_argument("--query", help="Query the bundle graph.")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="Approximate token budget for query output.")
    args = parser.parse_args()

    if args.build:
        status = build_graph()
        if status != 0 or not args.query:
            return status
    if args.query:
        return query_graph(args.query, args.budget)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
