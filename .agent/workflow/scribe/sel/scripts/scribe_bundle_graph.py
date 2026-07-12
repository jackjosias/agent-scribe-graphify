#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from scribe_output_paths import migrate_legacy_output, scribe_out_dir

SEL_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = SEL_ROOT.parent


def find_project_root(path: Path) -> Path:
    for ancestor in (path, *path.parents):
        if ancestor.name == ".agent":
            return ancestor.parent
    raise RuntimeError(f"Cannot resolve project root from {path}")


PROJECT_ROOT = find_project_root(BUNDLE_ROOT)
BUNDLE_GRAPH_DIR = scribe_out_dir(PROJECT_ROOT) / "bundle-graph" / BUNDLE_ROOT.name
DEFAULT_BUDGET = 1200
PROJECT_BUILD_TIMEOUT = 180
GRAPH_SKIP_DIRS = {"adapters", "graphify-out", "__pycache__", ".pytest_cache", ".mypy_cache", "vendor"}
GRAPH_SKIP_PATTERNS = {"*.pyc", "*.pyo", "*.min.js", "*.min.css", "*.map"}


def _load_readiness():
    mcp_root = PROJECT_ROOT / ".agent" / "mcp"
    if str(mcp_root) not in sys.path:
        sys.path.insert(0, str(mcp_root))
    from runtime import graphify_readiness
    return graphify_readiness


def should_skip_bundle_graph_path(path: Path) -> bool:
    return any(part in GRAPH_SKIP_DIRS for part in path.parts) or any(fnmatch.fnmatch(path.name, pattern) for pattern in GRAPH_SKIP_PATTERNS)


def bundle_graph_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if should_skip_bundle_graph_path(Path(name))}


def copy_bundle_without_graph(target: Path) -> None:
    shutil.copytree(BUNDLE_ROOT, target, ignore=bundle_graph_ignore)


def run_graphify_update(target: Path, *, cwd: Path | None = None, timeout: int = PROJECT_BUILD_TIMEOUT) -> subprocess.CompletedProcess[str]:
    command = ["graphify", "update", str(target)]
    try:
        return subprocess.run(
            command,
            cwd=str(cwd or target.parent),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(command, 127, stdout=str(exc))
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return subprocess.CompletedProcess(command, 124, stdout=output + f"\nGraphify timed out after {timeout}s")


def bundle_command_hint() -> str:
    try:
        return str(BUNDLE_ROOT.relative_to(PROJECT_ROOT) / "scribe")
    except ValueError:
        return str(BUNDLE_ROOT / "scribe")


def build_graph() -> int:
    """Build the isolated graph of the SCRIBE engine itself."""
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
    print(f"SCRIBE GRAPH: built isolated bundle graph at {BUNDLE_GRAPH_DIR}")
    return 0


def build_project_graph(timeout: int = PROJECT_BUILD_TIMEOUT) -> int:
    """Build, migrate, bind and revalidate the current project's Graphify graph."""
    print(f"TENOR_GRAPHIFY_BUILD_START root={PROJECT_ROOT} timeout={timeout}s", flush=True)
    result = run_graphify_update(Path("."), cwd=PROJECT_ROOT, timeout=timeout)
    if result.stdout.strip():
        print(result.stdout.rstrip(), flush=True)
    if result.returncode != 0:
        print(f"TENOR_GRAPHIFY_BUILD_FAILED rc={result.returncode}", file=sys.stderr, flush=True)
        return result.returncode

    migrate_legacy_output(PROJECT_ROOT, "graphify-out")
    readiness = _load_readiness()
    manifest = readiness.write_graphify_manifest(PROJECT_ROOT, kind="real", purpose="tenor_project_build")
    if not manifest.get("ok"):
        print(f"TENOR_GRAPHIFY_BIND_FAILED {manifest}", file=sys.stderr, flush=True)
        return 3
    verified = readiness.inspect_graphify_readiness(PROJECT_ROOT)
    if not verified.ok:
        print(f"TENOR_GRAPHIFY_VERIFY_FAILED {verified.to_dict()}", file=sys.stderr, flush=True)
        return 4
    print(
        f"TENOR_GRAPHIFY_READY nodes={verified.node_count} edges={verified.edge_count} "
        f"sources={verified.source_file_count}",
        flush=True,
    )
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
        timeout=60,
    )
    print(result.stdout.rstrip())
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(prog="scribe graph", description="Build/query the engine graph or bind the current project graph.")
    parser.add_argument("--build", action="store_true", help="Build a separate Graphify graph for the SCRIBE bundle.")
    parser.add_argument("--project-build", action="store_true", help="Build and bind the current project graph for TENOR.")
    parser.add_argument("--timeout", type=int, default=PROJECT_BUILD_TIMEOUT, help="Project Graphify build timeout in seconds.")
    parser.add_argument("--query", help="Query the isolated SCRIBE bundle graph.")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="Approximate token budget for query output.")
    args = parser.parse_args()

    if args.timeout < 1 or args.timeout > 3600:
        parser.error("--timeout must be between 1 and 3600 seconds")
    if args.project_build:
        status = build_project_graph(args.timeout)
        if status != 0:
            return status
    if args.build:
        status = build_graph()
        if status != 0 or not args.query:
            return status
    if args.query:
        return query_graph(args.query, args.budget)
    if args.project_build:
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
