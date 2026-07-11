#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Sequence

from scribe_doctor_lib import run_doctor
from scribe_install import Installer
from scribe_state import AGENT_TYPES, check_sync, update_state_after_write
from scribe_output_paths import graphify_out_dir, migrate_all_legacy_outputs, migrate_legacy_output, scribe_out_dir

SEL_ROOT = Path(__file__).resolve().parents[1]
SCRIBE_ROOT = SEL_ROOT.parent
BUNDLE_ROOT = SEL_ROOT
BUNDLE_COMMAND = SCRIBE_ROOT / "scribe"
SCRIBE_PATH = Path("AGENT-MEMOIRE_PROJECT_STATUS.scribe")
TEMPLATE_PATH = BUNDLE_ROOT / "templates" / "scribe.master-template.yaml"

SCRIBE_MEMORY_ADOPT = "SCRIBE_MEMORY_ADOPT"
SCRIBE_MEMORY_CREATE = "SCRIBE_MEMORY_CREATE"
TENOR_INIT_SAME_PROJECT = "TENOR_INIT_SAME_PROJECT"
TENOR_INIT_PLAN_REQUIRED = "TENOR_INIT_PLAN_REQUIRED"

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
    ".c", ".cpp", ".cs", ".go", ".java", ".js", ".jsx", ".kt", ".php", ".py", ".rs", ".swift", ".ts", ".tsx",
}
IGNORED_APP_CODE_PARTS = {
    ".agent", ".git", ".next", ".venv", "build", "coverage", "dist", "graphify-out", "node_modules", "scribe-out", "outputs", "target", "vendor",
}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[Sequence[str], Path], CommandResult]


@dataclass
class BootstrapReport:
    installation_classification: str
    project_changed: bool
    memory_action: str
    scribe_status: str = "pending"
    actions: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    doctor_code: int = 0
    sync_repaired: bool = False
    graphify_status: str = "unchanged"

    @property
    def new_project(self) -> bool:
        """Compatibility view only; never used as installation authority."""
        return self.project_changed


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


def _atomic_text_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def create_scribe_from_template(project_root: Path) -> Path:
    scribe_path = project_root / SCRIBE_PATH
    if scribe_path.exists():
        return scribe_path
    _atomic_text_write(scribe_path, render_template(project_root))
    return scribe_path


def default_runner(command: Sequence[str], cwd: Path) -> CommandResult:
    try:
        completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False, timeout=180)
    except FileNotFoundError as exc:
        return CommandResult(127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(124, stdout, stderr or "command timed out after 180 seconds")
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
    graphify_out = graphify_out_dir(project_root)
    graphify_out.mkdir(parents=True, exist_ok=True)
    _atomic_text_write(
        graphify_out / "GRAPH_REPORT.md",
        "# Graph Report\n\nBootstrap placeholder: no application graph has been built yet.\n",
    )
    _atomic_text_write(graphify_out / "graph.json", "{}\n")


def ensure_graphify(project_root: Path, runner: Runner, skip_graphify: bool) -> tuple[str, list[str], list[str], list[str]]:
    graphify_out = graphify_out_dir(project_root)
    if (graphify_out / "GRAPH_REPORT.md").exists():
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

    migrate_legacy_output(project_root, "graphify-out")
    codex = runner(("graphify", "codex", "install"), project_root)
    if codex.returncode != 0:
        warnings.append("`graphify codex install` failed; run it manually after Graphify is available.")
    hooks = runner((str(BUNDLE_COMMAND), "graphify-hooks", "--apply"), project_root)
    if hooks.returncode != 0:
        warnings.append("Graphify hook hardening did not complete; run `scribe graphify-hooks --apply` manually.")
    return "initialized", [], warnings, errors


def ensure_scribe_out(project_root: Path) -> None:
    scribe_out = scribe_out_dir(project_root)
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
    _atomic_text_write(path, content)


def ensure_state(project_root: Path, scribe_path: Path, agent: str, agent_type: str, scribe_created: bool) -> bool:
    check = check_sync(scribe_path)
    if check.ok:
        return False
    session = check.snapshot.last_journal_id or "JOURNAL-000"
    changed_ids = [session]
    if scribe_created:
        changed_ids.insert(0, "PAT-GRAPH-001")
    write_kind = "install" if scribe_created else "repair"
    update_state_after_write(scribe_path, agent, agent_type, session, changed_ids, write_kind)
    return True


def _plan_attr(plan: object, name: str, default: Any = None) -> Any:
    if isinstance(plan, dict):
        return plan.get(name, default)
    return getattr(plan, name, default)


def bootstrap_project(
    project_root: Path,
    agent: str = "bootstrap",
    agent_type: str = "cli",
    runner: Runner = default_runner,
    skip_graphify: bool = False,
    dry_run: bool = False,
    *,
    installation_plan: object | None = None,
) -> BootstrapReport:
    project_root = project_root.resolve()
    project_root.mkdir(parents=True, exist_ok=True)
    if installation_plan is None:
        raise RuntimeError(TENOR_INIT_PLAN_REQUIRED)
    planned_root = Path(str(_plan_attr(installation_plan, "project_root", project_root))).resolve()
    if planned_root != project_root:
        raise ValueError(f"installation plan root mismatch: {planned_root} != {project_root}")

    classification = str(_plan_attr(installation_plan, "classification", ""))
    memory_action = str(_plan_attr(installation_plan, "memory_action", ""))
    project_changed = bool(_plan_attr(installation_plan, "project_changed", classification != TENOR_INIT_SAME_PROJECT))
    report = BootstrapReport(
        installation_classification=classification,
        project_changed=project_changed,
        memory_action=memory_action,
    )

    install_code = run_installer(project_root, dry_run=dry_run)
    if install_code != 0:
        report.warnings.append("Rootless bundle install reported conflicts.")
    else:
        report.actions.append("rootless install verified")
    if dry_run:
        report.scribe_status = "dry-run"
        return report

    scribe_path = project_root / SCRIBE_PATH
    scribe_created = False
    if memory_action == SCRIBE_MEMORY_ADOPT:
        if not scribe_path.is_file():
            report.scribe_status = "missing"
            report.errors.append("SCRIBE_MEMORY_ADOPT requested but canonical memory is missing.")
            return report
        report.scribe_status = "adopted"
        report.actions.append("SCRIBE canonical memory adopted")
    elif memory_action == SCRIBE_MEMORY_CREATE:
        if scribe_path.exists():
            report.scribe_status = "adopted"
            report.warnings.append("SCRIBE appeared before creation; preserving and adopting it.")
        else:
            create_scribe_from_template(project_root)
            scribe_created = True
            report.scribe_status = "created"
            report.actions.append("SCRIBE created atomically from master template")
    else:
        report.scribe_status = "invalid-plan"
        report.errors.append(f"Unsupported memory action: {memory_action or '<empty>'}")
        return report

    migrate_all_legacy_outputs(project_root)
    ensure_agent_gitignore(project_root)
    report.actions.append(".agent gitignore ready")
    ensure_scribe_out(project_root)
    report.actions.append("scribe-out ready")

    report.graphify_status, graphify_infos, graphify_warnings, graphify_errors = ensure_graphify(project_root, runner, skip_graphify)
    report.infos.extend(graphify_infos)
    report.warnings.extend(graphify_warnings)
    report.errors.extend(graphify_errors)

    report.doctor_code = run_doctor(scribe_path, scribe_out_dir(project_root) / "scribe-doctor-report.md", suggest_fix=True)
    report.sync_repaired = ensure_state(project_root, scribe_path, agent, agent_type, scribe_created)
    return report


def print_report(report: BootstrapReport) -> None:
    print(f"SCRIBE BOOTSTRAP: {report.installation_classification}")
    print(f"  project_changed: {str(report.project_changed).lower()}")
    print(f"  Graphify: {report.graphify_status}")
    print(f"  SCRIBE: {report.scribe_status} ({report.memory_action})")
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
    if report.scribe_status == "created":
        print('  next: run .agent/workflow/scribe/scribe-rag preflight --tier STANDARD "<plan>"')
        print("  next: inspect canonical Graphify output before application work.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scribe bootstrap", description="Initialize a copied .agent bundle through TENOR authority.")
    parser.add_argument("--root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--agent", default="bootstrap")
    parser.add_argument("--type", dest="agent_type", default="cli", choices=sorted(AGENT_TYPES))
    parser.add_argument("--dry-run", action="store_true", help="Inspect classification and install actions without project mutation.")
    parser.add_argument("--skip-graphify", action="store_true", help=argparse.SUPPRESS)
    return parser


def _load_orchestrator() -> Any:
    mcp_root = Path(__file__).resolve().parents[4] / "mcp"
    if str(mcp_root) not in sys.path:
        sys.path.insert(0, str(mcp_root))
    from runtime import tenor_init_orchestrator
    return tenor_init_orchestrator


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    orchestrator = _load_orchestrator()

    if args.dry_run:
        classification = orchestrator.classify_installation(root)
        memory_action = SCRIBE_MEMORY_ADOPT if (root / SCRIBE_PATH).is_file() else SCRIBE_MEMORY_CREATE
        plan = SimpleNamespace(
            project_root=str(root),
            classification=classification["classification"],
            project_changed=classification["project_changed"],
            memory_action=memory_action,
        )
        report = bootstrap_project(
            root,
            agent=args.agent,
            agent_type=args.agent_type,
            skip_graphify=args.skip_graphify,
            dry_run=True,
            installation_plan=plan,
        )
        print_report(report)
        return 0 if not report.errors else 1

    try:
        with orchestrator.tenor_init_lock(root) as lock:
            lock = orchestrator.refresh_tenor_init_lock(lock, stage="classify_installation")
            plan = orchestrator.prepare_tenor_init(root)
            if not plan.ok:
                print(f"TENOR INIT ERROR: {plan.installation_verdict}", file=sys.stderr)
                return 3
            lock = orchestrator.refresh_tenor_init_lock(lock, stage="bootstrap_project")
            report = bootstrap_project(
                root,
                agent=args.agent,
                agent_type=args.agent_type,
                skip_graphify=args.skip_graphify,
                installation_plan=plan,
            )
            if report.errors or report.doctor_code != 0:
                print_report(report)
                return 1
            lock = orchestrator.refresh_tenor_init_lock(lock, stage="finalize_installation")
            finalized = orchestrator.finalize_tenor_init(root)
            if not finalized.get("ok"):
                report.errors.append(str(finalized.get("verdict") or "TENOR_INIT_FINALIZE_FAILED"))
                print_report(report)
                return 4
    except orchestrator.TenorInitBusy as exc:
        print(f"{orchestrator.TENOR_INIT_ALREADY_RUNNING}: {exc.lock}", file=sys.stderr)
        return 75

    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
