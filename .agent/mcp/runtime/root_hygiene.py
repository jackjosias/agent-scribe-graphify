from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

ROOT_HYGIENE_OK = "ROOT_HYGIENE_OK"
ROOT_HYGIENE_WARN = "ROOT_HYGIENE_WARN"
ROOT_HYGIENE_FAIL = "ROOT_HYGIENE_FAIL"
NESTED_AGENT_ROOT_DETECTED = "NESTED_AGENT_ROOT_DETECTED"
PARENT_AGENT_ROOT_DETECTED = "PARENT_AGENT_ROOT_DETECTED"
GENERATED_OUTPUT_OUTSIDE_GIT_ROOT = "GENERATED_OUTPUT_OUTSIDE_GIT_ROOT"
GIT_ROOT_NOT_FOUND = "GIT_ROOT_NOT_FOUND"
PROJECT_MARKERS_MISSING = "PROJECT_MARKERS_MISSING"
PROJECT_MARKERS_WEAK = "PROJECT_MARKERS_WEAK"

REQUIRED_MARKERS = (".git", ".agent", "AGENTS.md", "AGENT-MEMOIRE_PROJECT_STATUS.scribe")
SOFT_MARKERS = ("README.md",)
GENERATED_NAMES = ("graphify-out", "scribe-out")
SECRET_HINTS = ("token", "secret", "password", "passwd", "key")


def _as_path(start: Path | None = None) -> Path:
    return (start or Path.cwd()).resolve()


def _string(path: Path | None) -> str | None:
    return str(path.resolve()) if path is not None else None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _safe_path(path: Path) -> str:
    value = str(path.resolve())
    lowered = value.lower()
    if any(hint in lowered for hint in SECRET_HINTS):
        return "<redacted-sensitive-path>"
    return value


def resolve_git_root(start: Path | None = None) -> Path | None:
    current = _as_path(start)
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(current), text=True, capture_output=True, timeout=5,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return Path(value).resolve() if value else None


def resolve_project_root(start: Path | None = None) -> Path:
    git_root = resolve_git_root(start)
    if git_root is not None:
        return git_root
    current = _as_path(start)
    for candidate in (current, *current.parents):
        if (candidate / ".agent").is_dir() and (candidate / "AGENTS.md").exists():
            return candidate
    return current


def _nearby_parents(root: Path, max_parent_depth: int) -> list[Path]:
    parents: list[Path] = []
    current = root.resolve()
    for _ in range(max(0, max_parent_depth)):
        parent = current.parent
        if parent == current:
            break
        parents.append(parent)
        current = parent
    return parents


def _warning(code: str, path: Path, message: str) -> dict[str, str]:
    return {"code": code, "path": _safe_path(path), "message": message}


def inspect_root_hygiene(start: Path | None = None, max_parent_depth: int = 4) -> dict[str, Any]:
    current = _as_path(start)
    git_root = resolve_git_root(current)
    project_root = resolve_project_root(current)
    agent_dir = project_root / ".agent"
    runtime_dir = agent_dir / "state" / "runtime"
    warnings: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    if git_root is None:
        errors.append({"code": GIT_ROOT_NOT_FOUND, "path": _safe_path(current), "message": "No .git root was found from the start path."})
    missing = [marker for marker in REQUIRED_MARKERS if not (project_root / marker).exists()]
    if missing:
        errors.append({"code": PROJECT_MARKERS_MISSING, "path": _safe_path(project_root), "message": "Missing project markers: " + ", ".join(missing)})
    soft_missing = [marker for marker in SOFT_MARKERS if not (project_root / marker).exists()]
    if soft_missing and not missing:
        warnings.append(_warning(PROJECT_MARKERS_WEAK, project_root, "Missing optional project markers: " + ", ".join(soft_missing)))

    if git_root is not None:
        for parent in _nearby_parents(git_root, max_parent_depth):
            parent_agent = parent / ".agent"
            if parent_agent.exists():
                warnings.append(_warning(PARENT_AGENT_ROOT_DETECTED, parent_agent, "Parent .agent exists outside the git root."))
            for name in GENERATED_NAMES:
                generated = parent / name
                if generated.exists():
                    warnings.append(_warning(GENERATED_OUTPUT_OUTSIDE_GIT_ROOT, generated, f"Parent {name} exists outside the git root."))
        for agent in git_root.glob("**/.agent"):
            if agent == agent_dir:
                continue
            if _is_relative_to(agent, git_root):
                warnings.append(_warning(NESTED_AGENT_ROOT_DETECTED, agent, "Nested .agent exists inside the git root."))

    allow_warnings = os.environ.get("AGENT_ALLOW_ROOT_HYGIENE_WARNINGS") == "1"
    if errors:
        verdict = ROOT_HYGIENE_FAIL
    elif warnings and not allow_warnings:
        verdict = ROOT_HYGIENE_WARN
    else:
        verdict = ROOT_HYGIENE_OK

    return {
        "verdict": verdict,
        "cwd": _safe_path(current),
        "git_root": _safe_path(git_root) if git_root is not None else None,
        "project_root": _safe_path(project_root),
        "agent_dir": _safe_path(agent_dir),
        "runtime_dir": _safe_path(runtime_dir),
        "warnings": warnings,
        "errors": errors,
        "destructive_cleanup": False,
    }


def assert_safe_project_root(start: Path | None = None, strict: bool = False, max_parent_depth: int = 4) -> dict[str, Any]:
    report = inspect_root_hygiene(start, max_parent_depth=max_parent_depth)
    if strict and (report["errors"] or report["verdict"] == ROOT_HYGIENE_FAIL):
        raise RuntimeError(report["verdict"])
    if strict and report["warnings"] and os.environ.get("AGENT_ALLOW_ROOT_HYGIENE_WARNINGS") != "1":
        raise RuntimeError(ROOT_HYGIENE_WARN)
    return report


def format_root_hygiene_report(report: dict[str, Any]) -> str:
    lines = [
        f"verdict: {report.get('verdict')}",
        f"cwd: {report.get('cwd')}",
        f"git_root: {report.get('git_root')}",
        f"project_root: {report.get('project_root')}",
        f"agent_dir: {report.get('agent_dir')}",
        f"runtime_dir: {report.get('runtime_dir')}",
        f"destructive_cleanup: {report.get('destructive_cleanup')}",
    ]
    for item in report.get("warnings", []):
        lines.append(f"warning[{item.get('code')}]: {item.get('path')} — {item.get('message')}")
    for item in report.get("errors", []):
        lines.append(f"error[{item.get('code')}]: {item.get('path')} — {item.get('message')}")
    return "\n".join(lines)
