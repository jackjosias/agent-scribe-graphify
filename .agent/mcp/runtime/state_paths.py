from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional


def project_root_from(start: Optional[Path] = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".agent").is_dir():
            return candidate
    return current


def _move_legacy_path(legacy: Path, target: Path) -> None:
    if target.exists() or not legacy.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    legacy.replace(target)


def prepare_state_dirs(project_root: Optional[Path] = None) -> Dict[str, Path]:
    root = project_root_from(project_root)
    agent = root / ".agent"
    state = agent / "state"
    runtime = state / "runtime"
    scribe_out = state / "scribe-out"
    graphify_out = state / "graphify-out"

    legacy_runtime = agent / "runtime"
    legacy_scribe_out = agent / "scribe-out"
    legacy_graphify_out = agent / "graphify-out"

    if legacy_runtime.exists() and not runtime.exists():
        runtime.parent.mkdir(parents=True, exist_ok=True)
        legacy_runtime.replace(runtime)
    if legacy_scribe_out.exists() and not scribe_out.exists():
        scribe_out.parent.mkdir(parents=True, exist_ok=True)
        legacy_scribe_out.replace(scribe_out)
    if legacy_graphify_out.exists() and not graphify_out.exists():
        graphify_out.parent.mkdir(parents=True, exist_ok=True)
        legacy_graphify_out.replace(graphify_out)

    runtime.mkdir(parents=True, exist_ok=True)
    scribe_out.mkdir(parents=True, exist_ok=True)
    graphify_out.mkdir(parents=True, exist_ok=True)

    return {
        "root": root,
        "agent": agent,
        "state": state,
        "runtime": runtime,
        "db": runtime / "coordination.sqlite",
        "events": runtime / "events.log",
        "scribe_out": scribe_out,
        "graphify_out": graphify_out,
        "legacy_runtime": legacy_runtime,
        "legacy_scribe_out": legacy_scribe_out,
        "legacy_graphify_out": legacy_graphify_out,
    }


def graphify_report_candidates(project_root: Optional[Path] = None) -> list[Path]:
    paths = prepare_state_dirs(project_root)
    root = paths["root"]
    agent = paths["agent"]
    return [
        paths["graphify_out"] / "GRAPH_REPORT.md",
        paths["graphify_out"] / "graph.json",
        agent / "graphify-out" / "GRAPH_REPORT.md",
        agent / "graphify-out" / "graph.json",
        root / "graphify-out" / "GRAPH_REPORT.md",
        root / "graphify-out" / "graph.json",
    ]
