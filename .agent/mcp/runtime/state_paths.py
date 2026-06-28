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


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_legacy_output(legacy: Path, root: Path, agent: Path) -> bool:
    if not legacy.exists() or legacy.is_symlink():
        return False
    try:
        resolved = legacy.resolve(strict=True)
        resolved_root = root.resolve(strict=True)
        resolved_agent = agent.resolve(strict=True)
    except FileNotFoundError:
        return False
    return _is_relative_to(resolved, resolved_root) or _is_relative_to(resolved, resolved_agent)


def _unique_target(target: Path) -> Path:
    if not target.exists():
        return target
    for index in range(1, 1000):
        candidate = target.with_name(f"{target.name}.legacy-{index}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"unable to allocate legacy output target for {target}")


def _move_legacy_path(legacy: Path, target: Path, root: Path, agent: Path) -> None:
    if not _safe_legacy_output(legacy, root, agent):
        return
    target = _unique_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(legacy), str(target))


def prepare_state_dirs(project_root: Optional[Path] = None) -> Dict[str, Path]:
    root = project_root_from(project_root)
    agent = root / ".agent"
    state = agent / "state"
    runtime = state / "runtime"
    outputs = state / "outputs"
    scribe_out = outputs / "scribe-out"
    graphify_out = outputs / "graphify-out"

    legacy_runtime = agent / "runtime"
    legacy_scribe_out_root = root / "scribe-out"
    legacy_graphify_out_root = root / "graphify-out"
    legacy_scribe_out = state / "scribe-out"
    legacy_scribe_out_agent = agent / "scribe-out"
    legacy_graphify_out = state / "graphify-out"
    legacy_graphify_out_agent = agent / "graphify-out"

    _move_legacy_path(legacy_runtime, runtime, root, agent)
    _move_legacy_path(legacy_scribe_out_root, scribe_out, root, agent)
    _move_legacy_path(legacy_scribe_out, scribe_out, root, agent)
    _move_legacy_path(legacy_scribe_out_agent, scribe_out, root, agent)
    _move_legacy_path(legacy_graphify_out_root, graphify_out, root, agent)
    _move_legacy_path(legacy_graphify_out, graphify_out, root, agent)
    _move_legacy_path(legacy_graphify_out_agent, graphify_out, root, agent)

    runtime.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)
    scribe_out.mkdir(parents=True, exist_ok=True)
    graphify_out.mkdir(parents=True, exist_ok=True)

    return {
        "root": root,
        "agent": agent,
        "state": state,
        "runtime": runtime,
        "outputs": outputs,
        "db": runtime / "coordination.sqlite",
        "events": runtime / "events.log",
        "scribe_out": scribe_out,
        "graphify_out": graphify_out,
        "legacy_runtime": legacy_runtime,
        "legacy_scribe_out_root": legacy_scribe_out_root,
        "legacy_graphify_out_root": legacy_graphify_out_root,
        "legacy_scribe_out": legacy_scribe_out,
        "legacy_graphify_out": legacy_graphify_out,
    }


def graphify_report_candidates(project_root: Optional[Path] = None) -> list[Path]:
    paths = prepare_state_dirs(project_root)
    root = paths["root"]
    return [
        paths["graphify_out"] / "GRAPH_REPORT.md",
        paths["graphify_out"] / "graph.json",
        root / "graphify-out" / "GRAPH_REPORT.md",
        root / "graphify-out" / "graph.json",
    ]
