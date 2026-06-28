#!/usr/bin/env python3
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

SCRIBE_OUT_NAME = "scribe-out"
GRAPHIFY_OUT_NAME = "graphify-out"


def project_root_from_path(path: Path) -> Path:
    resolved = path.resolve()
    for current in (resolved, *resolved.parents):
        if (current / ".agent").is_dir():
            return current
    return resolved if resolved.is_dir() else resolved.parent


def outputs_root(project_root: Path) -> Path:
    return project_root.resolve() / ".agent" / "state" / "outputs"


def scribe_out_dir(project_root: Path) -> Path:
    return outputs_root(project_root) / SCRIBE_OUT_NAME


def graphify_out_dir(project_root: Path) -> Path:
    return outputs_root(project_root) / GRAPHIFY_OUT_NAME


def output_dir(project_root: Path, name: str) -> Path:
    if name == SCRIBE_OUT_NAME:
        return scribe_out_dir(project_root)
    if name == GRAPHIFY_OUT_NAME:
        return graphify_out_dir(project_root)
    raise ValueError(f"Unsupported output directory: {name}")


def legacy_output_dir(project_root: Path, name: str) -> Path:
    if name not in {SCRIBE_OUT_NAME, GRAPHIFY_OUT_NAME}:
        raise ValueError(f"Unsupported legacy output directory: {name}")
    return project_root.resolve() / name


def ensure_output_dirs(project_root: Path) -> tuple[Path, Path]:
    scribe_dir = scribe_out_dir(project_root)
    graph_dir = graphify_out_dir(project_root)
    scribe_dir.mkdir(parents=True, exist_ok=True)
    graph_dir.mkdir(parents=True, exist_ok=True)
    return scribe_dir, graph_dir


def _migration_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _move_child(source: Path, destination: Path, quarantine_root: Path) -> None:
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return
    quarantine_root.mkdir(parents=True, exist_ok=True)
    fallback = quarantine_root / source.name
    for index in range(1, 1000):
        if not fallback.exists():
            shutil.move(str(source), str(fallback))
            return
        fallback = quarantine_root / f"{source.name}.legacy-{index}"
    raise RuntimeError(f"unable to allocate legacy migration target for {source}")


def migrate_legacy_output(project_root: Path, name: str) -> Path:
    canonical = output_dir(project_root, name)
    legacy = legacy_output_dir(project_root, name)
    canonical.mkdir(parents=True, exist_ok=True)
    if not legacy.exists():
        return canonical
    if legacy.is_symlink():
        return canonical
    if legacy.resolve() == canonical.resolve():
        return canonical
    quarantine_root = canonical / "_legacy_migrated" / _migration_stamp()
    for child in sorted(legacy.iterdir(), key=lambda item: item.name):
        _move_child(child, canonical / child.name, quarantine_root)
    try:
        legacy.rmdir()
    except OSError:
        marker = quarantine_root / "legacy_directory_not_empty.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"Legacy directory retained: {legacy}\n", encoding="utf-8")
    return canonical


def migrate_all_legacy_outputs(project_root: Path) -> tuple[Path, Path]:
    return (
        migrate_legacy_output(project_root, SCRIBE_OUT_NAME),
        migrate_legacy_output(project_root, GRAPHIFY_OUT_NAME),
    )
