from __future__ import annotations

import shutil
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
    shutil.move(str(legacy), str(target))


def prepare_state_dirs(project_root: Optional[Path] = None) -> Dict[str, Path]:
    root = project_root_from(project_root)
    agent = root / ".agent"
    state = agent / "state"
    runtime = state / "runtime"
    scribe_out = root / "scribe-out"
    graphify_out = root / "graphify-out"

    legacy_runtime = agent / "runtime"
    legacy_scribe_out = state / "scribe-out"
    legacy_scribe_out_agent = agent / "scribe-out"
    legacy_graphify_out = state / "graphify-out"
    legacy_graphify_out_agent = agent / "graphify-out"

    _move_legacy_path(legacy_runtime, runtime)
    _move_legacy_path(legacy_scribe_out, scribe_out)
    _move_legacy_path(legacy_scribe_out_agent, scribe_out)
    _move_legacy_path(legacy_graphify_out, graphify_out)
    _move_legacy_path(legacy_graphify_out_agent, graphify_out)

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
