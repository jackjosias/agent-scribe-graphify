#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from scribe_doctor_lib import run_doctor
from scribe_install import Installer
from scribe_state import AGENT_TYPES, check_sync, update_state_after_write


SEL_ROOT = Path(__file__).resolve().parents[1]
SCRIBE_ROOT = SEL_ROOT.parent
BUNDLE_ROOT = SEL_ROOT
BUNDLE_COMMAND = SCRIBE_ROOT / "scribe"
SCRIBE_PATH = Path("AGENT-MEMOIRE_PROJECT_STATUS.scribe")
TEMPLATE_PATH = BUNDLE_ROOT / "templates" / "scribe.master-template.yaml"
AGENT_GITIGNORE = """__pycache__/
**/__pycache__/
*.pyc
*.pyo
.pytest_cache/
.mypy_cache/
"""
APP_MARKER_FILES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "composer.json",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
}
APP_CODE_EXTENSIONS = {
    ".c",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".py",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
}
IGNORED_APP_CODE_PARTS = {
    ".agent",
    ".git",
    ".next",
    ".venv",
    "build",
    "coverage",
    "dist",
    "graphify-out",
    "node_modules",
    "scribe-out",
    "target",
    "vendor",
}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[Sequence[str], Path], CommandResult]


@dataclass
class BootstrapReport:
    new_project: bool
    actions: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    doctor_code: int = 0
    sync_repaired: bool = False
    graphify_status: str = "unchanged"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def detect_project_name(project_root: Path) -> str:
    package_json = project_root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
        name = data.get("name") if isinstance(data, dict) else None
        if isinstance(name, str) and name.strip():
            return name.strip()
    return project_root.resolve().name or "project"


def detect_stack(project_root: Path) -> str:
    package_json = project_root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
        deps: dict[str, object] = {}
        if isinstance(data, dict):
            for key in ("dependencies", "devDependencies"):
                value = data.get(key)
                if isinstance(value, dict):
                    deps.update(value)
        stack = ["Node.js"]
        if "next" in deps:
            stack.append("Next.js")
        if "express" in deps:
            stack.append("Express")
        if "socket.io" in deps:
            stack.append("Socket.IO")
        if "@prisma/client" in deps or "prisma" in deps:
            stack.append("Prisma")
        return " / ".join(stack)
    if (project_root / "requirements.txt").exists() or (project_root / "pyproject.toml").exists():
        return "Python"
    if (project_root / "Cargo.toml").exists():
        return "Rust"
    if (project_root / "go.mod").exists():
        return "Go"
    return "Unknown"


def render_template(project_root: Path) -> str:
    now = utc_now()
    replacements = {
        "{{PROJECT_NAME}}": detect_project_name(project_root),
        "{{STACK}}": detect_stack(project_root),
        "{{DATE}}": now.date().isoformat(),
        "{{TIMESTAMP}}": now.isoformat().replace("+00:00", "Z"),
    }
    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    for marker, value in replacements.items():
        content = content.replace(marker, value)
    return content


def create_scribe_from_template(project_root: Path) -> Path:
    scribe_path = project_root / SCRIBE_PATH
    if scribe_path.exists():
        return scribe_path
    scribe_path.write_text(render_template(project_root), encoding="utf-8")
    return scribe_path


def default_runner(command: Sequence[str], cwd: Path) -> CommandResult:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def run_installer(project_root: Path, dry_run: bool) -> int:
    installer = Installer(SCRIBE_ROOT, project_root, force=True, dry_run=dry_run, with_root_adapters=False)
    return installer.run()


def has_application_code(project_root: Path) -> bool:
    for marker in APP_MARKER_FILES:
        if (project_root / marker).exists():
            return True
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        relative_parts = set(path.relative_to(project_root).parts)
        if relative_parts & IGNORED_APP_CODE_PARTS:
            continue
        if path.name in {"AGENTS.md", "AGENT-MEMOIRE_PROJECT_STATUS.scribe", ".graphifyignore"}:
            continue
        if path.suffix.lower() in APP_CODE_EXTENSIONS:
            return True
    return False


def write_graphify_placeholder(project_root: Path) -> None:
    graphify_out = project_root / "graphify-out"
    graphify_out.mkdir(parents=True, exist_ok=True)
    (graphify_out / "GRAPH_REPORT.md").write_text(
        "# Graph Report\n\nBootstrap placeholder: no application graph has been built yet.\n",
        encoding="utf-8",
    )
    (graphify_out / "graph.json").write_text("{}\n", encoding="utf-8")


def ensure_graphify(project_root: Path, runner: Runner, skip_graphify: bool) -> tuple[str, list[str], list[str], list[str]]:
    graphify_out = project_root / "graphify-out"
    if graphify_out.exists():
        return "existing", [], [], []
    if skip_graphify:
        return "skipped", [], ["Graphify initialization skipped by flag."], []
    if not has_application_code(project_root):
        write_graphify_placeholder(project_root)
        return "placeholder", ["Graphify: placeholder initialisé. Relancer graphify update . après ajout du code source."], [], []
    if shutil.which("graphify") is None:
        return "missing", [], [], ["Graphify manquant sur projet avec code. Lancer graphify update . d'abord."]

    warnings: list[str] = []
    errors: list[str] = []
    update = runner(("graphify", "update", "."), project_root)
    if update.returncode != 0:
        errors.append("Graphify manquant sur projet avec code. Lancer graphify update . d'abord.")
        if update.stderr.strip():
            errors.append(update.stderr.strip().splitlines()[-1])
        return "missing", [], warnings, errors

    codex = runner(("graphify", "codex", "install"), project_root)
    if codex.returncode != 0:
        warnings.append("`graphify codex install` failed; run it manually after Graphify is available.")
    hooks = runner((str(BUNDLE_COMMAND), "graphify-hooks", "--apply"), project_root)
    if hooks.returncode != 0:
        warnings.append("Graphify hook hardening did not complete; run `scribe graphify-hooks --apply` manually.")
    return "initialized", [], warnings, errors


def ensure_scribe_out(project_root: Path) -> None:
    scribe_out = project_root / "scribe-out"
    (scribe_out / "locks").mkdir(parents=True, exist_ok=True)
    (scribe_out / "archive").mkdir(parents=True, exist_ok=True)


def ensure_agent_gitignore(project_root: Path) -> None:
    path = project_root / ".agent" / ".gitignore"
    if path.exists() and path.read_text(encoding="utf-8") == AGENT_GITIGNORE:
        return
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        missing = [line for line in AGENT_GITIGNORE.splitlines() if line and line not in existing.splitlines()]
        if not missing:
            return
        content = existing.rstrip() + "\n" + "\n".join(missing) + "\n"
    else:
        content = AGENT_GITIGNORE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def ensure_state(project_root: Path, scribe_path: Path, agent: str, agent_type: str, new_project: bool) -> bool:
    check = check_sync(scribe_path)
    if check.ok:
        return False
    session = check.snapshot.last_journal_id or "JOURNAL-000"
    changed_ids = [session]
    if new_project:
        changed_ids.insert(0, "PAT-GRAPH-001")
    write_kind = "install" if new_project else "repair"
    update_state_after_write(scribe_path, agent, agent_type, session, changed_ids, write_kind)
    return True


def bootstrap_project(
    project_root: Path,
    agent: str = "bootstrap",
    agent_type: str = "cli",
    runner: Runner = default_runner,
    skip_graphify: bool = False,
    dry_run: bool = False,
) -> BootstrapReport:
    project_root.mkdir(parents=True, exist_ok=True)
    scribe_path = project_root / SCRIBE_PATH
    report = BootstrapReport(new_project=not scribe_path.exists())

    install_code = run_installer(project_root, dry_run=dry_run)
    if install_code != 0:
        report.warnings.append("Rootless bundle install reported conflicts.")
    else:
        report.actions.append("rootless install verified")
    if dry_run:
        return report

    if not scribe_path.exists():
        create_scribe_from_template(project_root)
        report.actions.append("SCRIBE created from master template")
    else:
        report.actions.append("SCRIBE existing")

    ensure_agent_gitignore(project_root)
    report.actions.append(".agent gitignore ready")

    ensure_scribe_out(project_root)
    report.actions.append("scribe-out ready")

    report.graphify_status, graphify_infos, graphify_warnings, graphify_errors = ensure_graphify(project_root, runner, skip_graphify)
    report.infos.extend(graphify_infos)
    report.warnings.extend(graphify_warnings)
    report.errors.extend(graphify_errors)

    report.doctor_code = run_doctor(scribe_path, project_root / "scribe-out" / "scribe-doctor-report.md", suggest_fix=True)
    report.sync_repaired = ensure_state(project_root, scribe_path, agent, agent_type, report.new_project)
    return report


def print_report(report: BootstrapReport) -> None:
    status = "NOUVEAU PROJET DETECTE" if report.new_project else "PROJET EXISTANT"
    print(f"SCRIBE BOOTSTRAP: {status}")
    print(f"  Graphify: {report.graphify_status}")
    print(f"  SCRIBE: {'created' if report.new_project else 'existing'}")
    print("  scribe-out: ready")
    print(f"  doctor: {'ok' if report.doctor_code == 0 else 'errors'}")
    print(f"  sync: {'repaired' if report.sync_repaired else 'in-sync'}")
    for action in report.actions:
        print(f"  action: {action}")
    for info in report.infos:
        print(f"  info: {info}")
    for warning in report.warnings:
        print(f"  warning: {warning}", file=sys.stderr)
    for error in report.errors:
        print(f"  error: {error}", file=sys.stderr)
    if report.new_project:
        print("  next: run .agent/workflow/scribe/scribe-rag preflight --tier STANDARD \"<plan>\"")
        print("  next: read graphify-out/GRAPH_REPORT.md before application work.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scribe bootstrap", description="Initialize a copied .agent bundle in a project.")
    parser.add_argument("--root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--agent", default="bootstrap")
    parser.add_argument("--type", dest="agent_type", default="cli", choices=sorted(AGENT_TYPES))
    parser.add_argument("--dry-run", action="store_true", help="Show install actions without creating project memory.")
    parser.add_argument("--skip-graphify", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = bootstrap_project(
        Path(args.root).resolve(),
        agent=args.agent,
        agent_type=args.agent_type,
        skip_graphify=args.skip_graphify,
        dry_run=args.dry_run,
    )
    print_report(report)
    has_conflicts = any("conflicts" in item for item in report.warnings)
    return 0 if report.doctor_code == 0 and not has_conflicts and not report.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
