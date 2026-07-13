#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Sequence

from scribe_doctor_lib import run_doctor
from scribe_install import Installer, replace_managed_block
from scribe_output_paths import graphify_out_dir, migrate_all_legacy_outputs, scribe_out_dir
from scribe_state import AGENT_TYPES, check_sync, update_state_after_write

SEL_ROOT = Path(__file__).resolve().parents[1]
SCRIBE_ROOT = SEL_ROOT.parent
BUNDLE_ROOT = SEL_ROOT
SCRIBE_PATH = Path("AGENT-MEMOIRE_PROJECT_STATUS.scribe")
TEMPLATE_PATH = BUNDLE_ROOT / "templates" / "scribe.master-template.yaml"
SCRIBE_MEMORY_ADOPT = "SCRIBE_MEMORY_ADOPT"
SCRIBE_MEMORY_CREATE = "SCRIBE_MEMORY_CREATE"
TENOR_INIT_SAME_PROJECT = "TENOR_INIT_SAME_PROJECT"
TENOR_INIT_PLAN_REQUIRED = "TENOR_INIT_PLAN_REQUIRED"
GRAPHIFY_PROJECT_BUILD_COMMAND = ".agent/workflow/scribe/scribe graph --project-build --timeout 180"
AGENT_GITIGNORE = """__pycache__/
**/__pycache__/
*.pyc
*.pyo
.pytest_cache/
.mypy_cache/
"""
APP_MARKER_FILES = {
    "package.json", "pyproject.toml", "requirements.txt", "Cargo.toml", "go.mod",
    "composer.json", "pom.xml", "build.gradle", "build.gradle.kts",
}
APP_CODE_EXTENSIONS = {
    ".c", ".cpp", ".cs", ".go", ".java", ".js", ".jsx", ".kt", ".php",
    ".py", ".rs", ".swift", ".ts", ".tsx", ".vue", ".svelte",
}
IGNORED_APP_CODE_PARTS = {
    ".agent", ".git", ".next", ".venv", "build", "coverage", "dist",
    "graphify-out", "node_modules", "scribe-out", "outputs", "target", "vendor",
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
    graphify_verdict: str = ""

    @property
    def new_project(self) -> bool:
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
        dependencies: dict[str, object] = {}
        if isinstance(data, dict):
            for key in ("dependencies", "devDependencies"):
                value = data.get(key)
                if isinstance(value, dict):
                    dependencies.update(value)
        stack = ["Node.js"]
        for package, label in (("next", "Next.js"), ("express", "Express"), ("socket.io", "Socket.IO")):
            if package in dependencies:
                stack.append(label)
        if "@prisma/client" in dependencies or "prisma" in dependencies:
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


def _atomic_replace(src: str, dst: Path) -> None:
    """Publish a sibling temp file atomically with a bounded Windows retry.

    On Windows, os.replace can raise PermissionError while the destination is
    read concurrently; we retry a bounded number of times with backoff and then
    re-raise. We never silently fall back to a non-atomic direct write.
    """
    last_exc: OSError | None = None
    for attempt in range(5):
        try:
            os.replace(src, str(dst))
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt < 4:
                time.sleep(0.01 * (2 ** attempt))
                continue
            raise
    if last_exc is not None:
        raise last_exc


def _atomic_text_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_replace(temporary, path)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


def create_scribe_from_template(project_root: Path) -> Path:
    path = project_root / SCRIBE_PATH
    if not path.exists():
        _atomic_text_write(path, render_template(project_root))
    return path


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
    return Installer(SCRIBE_ROOT, project_root, force=True, dry_run=dry_run, with_root_adapters=False).run()


def _detect_managed_drift(project_root: Path, report: BootstrapReport) -> None:
    """Read-only bundle drift detection for SAME_PROJECT sessions.

    Surfaces drift in generated managed files as a warning. It never repairs:
    bundle repair stays explicit and separate (run the installer with --force).
    Any failure here is non-fatal — drift detection must never break bootstrap.
    """
    try:
        from scribe_install_templates import (
            AGENTS_END,
            AGENTS_START,
            GRAPHIFY_END,
            GRAPHIFY_START,
            LEGACY_AGENTS_MARKERS,
            LEGACY_GRAPHIFY_MARKERS,
            render_agents_block,
            render_graphify_block,
            render_scribe_rule,
        )
    except Exception:  # noqa: BLE001 — drift detection must never break bootstrap
        return
    managed = [
        (project_root / "AGENTS.md", AGENTS_START, AGENTS_END, render_agents_block(), LEGACY_AGENTS_MARKERS),
        (project_root / ".graphifyignore", GRAPHIFY_START, GRAPHIFY_END, render_graphify_block(), LEGACY_GRAPHIFY_MARKERS),
        (project_root / ".agent" / "rules" / "scribe.md", None, None, render_scribe_rule(), ()),
    ]
    for path, start, end, block, legacy in managed:
        if not path.is_file():
            continue
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:
            continue
        expected = block if start is None else replace_managed_block(existing, start, end, block, legacy)
        if existing != expected:
            report.warnings.append(
                f"Bundle drift detected in {path.relative_to(project_root)}; "
                f"repair is explicit and separate: run `scribe install --force`. No silent repair applied."
            )


def has_application_code(project_root: Path) -> bool:
    if any((project_root / marker).exists() for marker in APP_MARKER_FILES):
        return True
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative_parts = set(path.relative_to(project_root).parts)
        except ValueError:
            continue
        if relative_parts & IGNORED_APP_CODE_PARTS:
            continue
        if path.name in {"AGENTS.md", "AGENT-MEMOIRE_PROJECT_STATUS.scribe", ".graphifyignore"}:
            continue
        if path.suffix.lower() in APP_CODE_EXTENSIONS:
            return True
    return False


def _load_graphify_readiness():
    mcp_root = Path(__file__).resolve().parents[4] / "mcp"
    if str(mcp_root) not in sys.path:
        sys.path.insert(0, str(mcp_root))
    from runtime import graphify_readiness
    return graphify_readiness


def write_graphify_placeholder(project_root: Path) -> None:
    readiness = _load_graphify_readiness()
    output = graphify_out_dir(project_root)
    output.mkdir(parents=True, exist_ok=True)
    _atomic_text_write(output / "GRAPH_REPORT.md", "# Graph Report\n\nBootstrap placeholder: no application graph has been built yet.\n")
    _atomic_text_write(output / "graph.json", '{"nodes":[],"edges":[]}\n')
    _atomic_text_write(output / "graph.html", "<html><body>Empty project Graphify placeholder.</body></html>\n")
    manifest = readiness.write_graphify_manifest(project_root, kind="empty_project", purpose="tenor_empty_project")
    if not manifest.get("ok"):
        raise RuntimeError(f"cannot bind empty-project Graphify placeholder: {manifest}")


def ensure_graphify(project_root: Path, runner: Runner, skip_graphify: bool) -> tuple[str, list[str], list[str], list[str]]:
    del runner
    readiness = _load_graphify_readiness()
    current = readiness.inspect_graphify_readiness(project_root)
    if current.ok:
        status = "placeholder" if current.verdict == readiness.GRAPHIFY_EMPTY_PROJECT_READY else "ready"
        return status, [f"Graphify: {current.verdict} nodes={current.node_count} edges={current.edge_count}"], [], []
    if skip_graphify:
        return "skipped", [], [f"Graphify check skipped explicitly; current verdict={current.verdict}."], []
    if not has_application_code(project_root):
        write_graphify_placeholder(project_root)
        verified = readiness.inspect_graphify_readiness(project_root)
        if not verified.ok:
            return "invalid", [], [], [f"Graphify empty-project placeholder failed verification: {verified.verdict} — {verified.reason}"]
        return "placeholder", ["Graphify: empty-project placeholder bound to the current root."], [], []
    return (
        "build_required",
        [],
        [],
        [
            f"Graphify not ready: {current.verdict} — {current.reason}",
            f"Run `{GRAPHIFY_PROJECT_BUILD_COMMAND}`, then rerun TENOR INIT.",
        ],
    )


def ensure_scribe_out(project_root: Path) -> None:
    output = scribe_out_dir(project_root)
    (output / "locks").mkdir(parents=True, exist_ok=True)
    (output / "archive").mkdir(parents=True, exist_ok=True)


def ensure_agent_gitignore(project_root: Path) -> None:
    path = project_root / ".agent" / ".gitignore"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        missing = [line for line in AGENT_GITIGNORE.splitlines() if line and line not in existing.splitlines()]
        if not missing:
            return
        content = existing.rstrip() + "\n" + "\n".join(missing) + "\n"
    else:
        content = AGENT_GITIGNORE
    _atomic_text_write(path, content)


def ensure_state(scribe_path: Path, agent: str, agent_type: str, scribe_created: bool) -> bool:
    check = check_sync(scribe_path)
    if check.ok:
        return False
    session = check.snapshot.last_journal_id or "JOURNAL-000"
    changed_ids = [session]
    if scribe_created:
        changed_ids.insert(0, "PAT-GRAPH-001")
    update_state_after_write(scribe_path, agent, agent_type, session, changed_ids, "install" if scribe_created else "repair")
    return True


def _plan_attr(plan: object, name: str, default: Any = None) -> Any:
    return plan.get(name, default) if isinstance(plan, dict) else getattr(plan, name, default)


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
    report = BootstrapReport(classification, project_changed, memory_action)

    if classification == TENOR_INIT_SAME_PROJECT:
        # SAME_PROJECT session init is tracked-file read-only.
        # Never call the forced installer; never rewrite tracked config/docs.
        # Runtime mutations under .agent/state (presences, locks, proof store,
        # runtime reports, manifests) remain allowed below.
        report.actions.append("SAME_PROJECT read-only session init (installer skipped)")
        _detect_managed_drift(project_root, report)
    else:
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
    if classification == TENOR_INIT_SAME_PROJECT:
        # .agent/.gitignore is a tracked file; on SAME_PROJECT it is already
        # installed and must not be rewritten. Bundle repair stays explicit.
        report.actions.append(".agent gitignore skipped (SAME_PROJECT read-only)")
    else:
        ensure_agent_gitignore(project_root)
        report.actions.append(".agent gitignore ready")
    ensure_scribe_out(project_root)
    report.actions.append("scribe-out ready")

    status, infos, warnings, errors = ensure_graphify(project_root, runner, skip_graphify)
    report.graphify_status = status
    report.infos.extend(infos)
    report.warnings.extend(warnings)
    report.errors.extend(errors)
    if infos:
        report.graphify_verdict = infos[0]
    elif errors:
        report.graphify_verdict = errors[0]

    report.doctor_code = run_doctor(scribe_path, scribe_out_dir(project_root) / "scribe-doctor-report.md", suggest_fix=True)
    report.sync_repaired = ensure_state(scribe_path, agent, agent_type, scribe_created)
    return report


def print_report(report: BootstrapReport) -> None:
    print(f"SCRIBE BOOTSTRAP: {report.installation_classification}")
    print(f"  project_changed: {str(report.project_changed).lower()}")
    print(f"  Graphify: {report.graphify_status}")
    print(f"  SCRIBE: {report.scribe_status} ({report.memory_action})")
    print("  scribe-out: ready")
    print(f"  doctor: {'ok' if report.doctor_code == 0 else 'errors'}")
    print(f"  sync: {'repaired' if report.sync_repaired else 'in-sync'}")
    for label, values, stream in (("action", report.actions, sys.stdout), ("info", report.infos, sys.stdout), ("warning", report.warnings, sys.stderr), ("error", report.errors, sys.stderr)):
        for value in values:
            print(f"  {label}: {value}", file=stream)
    if report.scribe_status == "created":
        print('  next: run .agent/workflow/scribe/scribe-rag preflight --tier STANDARD "<plan>"')
    if report.graphify_status == "build_required":
        print(f"  next: {GRAPHIFY_PROJECT_BUILD_COMMAND}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scribe bootstrap", description="Initialize a copied .agent bundle through TENOR authority.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--agent", default="bootstrap")
    parser.add_argument("--type", dest="agent_type", default="cli", choices=sorted(AGENT_TYPES))
    parser.add_argument("--dry-run", action="store_true")
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
        report = bootstrap_project(
            root,
            agent=args.agent,
            agent_type=args.agent_type,
            skip_graphify=args.skip_graphify,
            dry_run=True,
            installation_plan=SimpleNamespace(
                project_root=str(root),
                classification=classification["classification"],
                project_changed=classification["project_changed"],
                memory_action=memory_action,
            ),
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
            report = bootstrap_project(root, agent=args.agent, agent_type=args.agent_type, skip_graphify=args.skip_graphify, installation_plan=plan)
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
