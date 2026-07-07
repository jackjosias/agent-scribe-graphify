#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

SYNC_PATHS = (
    ".agent/mcp",
    ".agent/scripts",
    ".agent/rules",
    ".agent/skills",
    ".agent/docs",
    ".agent/host_adapter",
    ".agent/workflow/scribe/rag",
    ".agent/workflow/scribe/sel",
    ".agent/workflow/scribe/scribe",
    ".agent/workflow/scribe/scribe-rag",
)

PRESERVED_PATHS = (
    "AGENT-MEMOIRE_PROJECT_STATUS.scribe",
    ".agent/agent.json",
    ".agent/mcp_config.json",
    ".agent/state",
    ".agent/state/outputs",
    ".agent/workflow/scribe/AGENT-MEMOIRE_PROJECT_STATUS.scribe",
    ".agent/workflow/scribe/sel/state",
    ".agent/workflow/scribe/sel/archive",
    "graphify-out",
    "scribe-out",
    ".codex",
    ".agents",
)

_VALIDATED: bool = False


def _validate_config() -> None:
    global _VALIDATED
    if _VALIDATED:
        return
    sync_set = set(SYNC_PATHS)
    preserve_set = set(PRESERVED_PATHS)
    overlap = sync_set & preserve_set
    if overlap:
        raise AssertionError(f"SYNC_PATHS and PRESERVED_PATHS overlap: {overlap}")
    for preserved in PRESERVED_PATHS:
        for sync_path in SYNC_PATHS:
            if _is_child_of(preserved, sync_path):
                break
        else:
            continue
    _VALIDATED = True

SKIP_MANIFEST = "AGENT_RUNTIME_SYNC_PLAN"
APPLY_MANIFEST = "AGENT_RUNTIME_SYNC_APPLIED"
REFUSED_MANIFEST = "AGENT_RUNTIME_SYNC_REFUSED"
SCHEMA_VERSION = "1.0"


class SyncError(RuntimeError):
    def __init__(self, reason: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


def sha256_file(path: Path) -> str:
    if path.is_symlink():
        raise SyncError("symlink_file_refused", {"path": str(path)})
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_files(root: Path) -> list[Path]:
    if root.is_symlink():
        raise SyncError("symlink_tree_refused", {"path": str(root)})
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    files: list[Path] = []
    for child in sorted(root.rglob("*")):
        if child.is_symlink():
            raise SyncError("symlink_tree_refused", {"path": str(child)})
        if child.is_file():
            files.append(child)
    return files


def tree_digest(path: Path) -> str:
    if path.is_symlink():
        raise SyncError("symlink_digest_refused", {"path": str(path)})
    if not path.exists():
        return "missing"
    if path.is_file():
        return f"file:{sha256_file(path)}"
    if path.is_dir():
        h = hashlib.sha256()
        for file_path in iter_files(path):
            rel = file_path.relative_to(path).as_posix()
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            h.update(sha256_file(file_path).encode("ascii"))
            h.update(b"\0")
        return f"dir:{h.hexdigest()}"
    raise SyncError("unsupported_path_type", {"path": str(path)})


def safe_child(root: Path, rel_path: str) -> Path:
    if not rel_path or Path(rel_path).is_absolute():
        raise SyncError("invalid_relative_path", {"path": rel_path})
    parts = Path(rel_path).parts
    if any(part == ".." for part in parts):
        raise SyncError("path_traversal_refused", {"path": rel_path})
    candidate = root / rel_path
    resolved_root = root.resolve()
    resolved_parent = candidate.parent.resolve()
    if resolved_parent != resolved_root and resolved_root not in resolved_parent.parents:
        raise SyncError("path_escape_refused", {"root": str(root), "path": rel_path})
    return candidate


def classify_target(target_root: Path) -> dict[str, Any]:
    if not target_root.exists() or not target_root.is_dir():
        raise SyncError("target_root_missing", {"target": str(target_root)})
    agent_dir = target_root / ".agent"
    if agent_dir.is_symlink():
        raise SyncError("target_agent_symlink_refused", {"target": str(agent_dir)})
    if not agent_dir.is_dir():
        raise SyncError("target_agent_missing", {"target": str(agent_dir)})
    return {
        "target_root": str(target_root.resolve()),
        "agent_dir": str(agent_dir.resolve()),
        "preserved_paths": [path for path in PRESERVED_PATHS if (target_root / path).exists()],
    }


def _path_action(source_root: Path, target_root: Path, rel_path: str) -> dict[str, Any]:
    source = safe_child(source_root, rel_path)
    target = safe_child(target_root, rel_path)
    if source.is_symlink():
        raise SyncError("source_symlink_refused", {"path": str(source)})
    if target.is_symlink():
        raise SyncError("target_symlink_refused", {"path": str(target)})
    if not source.exists():
        return {"path": rel_path, "action": "source_missing"}
    source_digest = tree_digest(source)
    target_digest = tree_digest(target)
    if target_digest == "missing":
        action = "create"
    elif source_digest == target_digest:
        action = "unchanged"
    else:
        action = "replace"
    return {
        "path": rel_path,
        "action": action,
        "source_digest": source_digest,
        "target_digest": target_digest,
    }


def build_plan(source_root: Path, target_root: Path) -> dict[str, Any]:
    source_root = source_root.resolve()
    target_root = target_root.resolve()
    if source_root == target_root:
        raise SyncError("source_target_same_refused", {"root": str(source_root)})
    if not (source_root / ".agent").is_dir():
        raise SyncError("source_agent_missing", {"source": str(source_root)})
    target_info = classify_target(target_root)
    actions = [_path_action(source_root, target_root, rel_path) for rel_path in SYNC_PATHS]
    return {
        "schema": SCHEMA_VERSION,
        "verdict": SKIP_MANIFEST,
        "source_root": str(source_root),
        "target": target_info,
        "sync_paths": list(SYNC_PATHS),
        "preserved_paths": list(PRESERVED_PATHS),
        "actions": actions,
        "summary": {
            "create": sum(1 for item in actions if item["action"] == "create"),
            "replace": sum(1 for item in actions if item["action"] == "replace"),
            "unchanged": sum(1 for item in actions if item["action"] == "unchanged"),
            "source_missing": sum(1 for item in actions if item["action"] == "source_missing"),
        },
    }


def _is_child_of(child: str, parent: str) -> bool:
    child_parts = Path(child).parts
    parent_parts = Path(parent).parts
    return len(child_parts) > len(parent_parts) and child_parts[:len(parent_parts)] == parent_parts


def _restore_preserved_children(target_root: Path, backup_root: Path, rel_path: str) -> None:
    for preserved in PRESERVED_PATHS:
        if not _is_child_of(preserved, rel_path):
            continue
        preserved_target = safe_child(target_root, preserved)
        preserved_backup = safe_child(backup_root, preserved)
        if not preserved_backup.exists():
            continue
        if preserved_target.exists():
            if preserved_target.is_dir():
                shutil.rmtree(preserved_target)
            else:
                preserved_target.unlink()
        preserved_target.parent.mkdir(parents=True, exist_ok=True)
        if preserved_backup.is_dir():
            shutil.copytree(preserved_backup, preserved_target, symlinks=False)
        elif preserved_backup.is_file():
            shutil.copy2(preserved_backup, preserved_target)


def backup_existing_path(target_root: Path, rel_path: str, backup_root: Path) -> str | None:
    target = safe_child(target_root, rel_path)
    if not target.exists():
        return None
    if target.is_symlink():
        raise SyncError("target_symlink_refused", {"path": str(target)})
    backup = safe_child(backup_root, rel_path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    if target.is_dir():
        shutil.copytree(target, backup, symlinks=False)
    elif target.is_file():
        shutil.copy2(target, backup)
    else:
        raise SyncError("unsupported_backup_path", {"path": str(target)})
    return str(backup)


def replace_path(source_root: Path, target_root: Path, rel_path: str) -> None:
    source = safe_child(source_root, rel_path)
    target = safe_child(target_root, rel_path)
    if source.is_symlink() or target.is_symlink():
        raise SyncError("symlink_replace_refused", {"path": rel_path})
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
        else:
            raise SyncError("unsupported_replace_path", {"path": str(target)})
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=False)
    elif source.is_file():
        shutil.copy2(source, target)
    else:
        raise SyncError("source_path_missing", {"path": str(source)})


def write_manifest(target_root: Path, payload: dict[str, Any]) -> Path:
    manifest = target_root / ".agent" / "state" / "runtime-sync" / "last-sync.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def apply_plan(source_root: Path, target_root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    source_root = source_root.resolve()
    target_root = target_root.resolve()
    backup_root = target_root / ".agent" / "state" / "runtime-sync" / "backups" / time.strftime("%Y%m%d-%H%M%S")
    applied: list[dict[str, Any]] = []
    for item in plan["actions"]:
        action = item["action"]
        rel_path = item["path"]
        if action in {"unchanged", "source_missing"}:
            applied.append({"path": rel_path, "action": action, "backup": None})
            continue
        backup = backup_existing_path(target_root, rel_path, backup_root)
        replace_path(source_root, target_root, rel_path)
        _restore_preserved_children(target_root, backup_root, rel_path)
        applied.append({"path": rel_path, "action": action, "backup": backup})
    result = dict(plan)
    result["verdict"] = APPLY_MANIFEST
    result["applied_at"] = int(time.time())
    result["backup_root"] = str(backup_root)
    result["applied"] = applied
    manifest = write_manifest(target_root, result)
    result["manifest_path"] = str(manifest)
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely sync .agent runtime code into a target project without overwriting project state.")
    parser.add_argument("--source", default=str(Path(__file__).resolve().parents[2]), help="agent-scribe-graphify source repo root")
    parser.add_argument("--target", required=True, help="target project root containing .agent")
    parser.add_argument("--apply", action="store_true", help="apply the plan; default is dry-run")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _validate_config()
    args = parse_args(argv or sys.argv[1:])
    try:
        source_root = Path(args.source)
        target_root = Path(args.target)
        plan = build_plan(source_root, target_root)
        payload = apply_plan(source_root, target_root, plan) if args.apply else plan
        print(json.dumps({"ok": True, **payload}, indent=2, sort_keys=True))
        return 0
    except SyncError as exc:
        print(json.dumps({"ok": False, "verdict": REFUSED_MANIFEST, "reason": exc.reason, "details": exc.details}, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
