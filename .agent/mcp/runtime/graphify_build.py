from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from . import bounded_process, db, graphify_readiness, tenor_init_orchestrator


MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 3600
DEFAULT_TIMEOUT_SECONDS = 180
PROCESS_GRACE_SECONDS = 30
OUTPUT_LIMIT = 20_000

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _readiness_payload(readiness: graphify_readiness.Readiness) -> dict[str, Any]:
    return readiness.to_dict()


def _run_graphify(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    completed = bounded_process.run_bounded(
        command,
        cwd=str(cwd),
        timeout_seconds=timeout,
        output_limit_bytes=OUTPUT_LIMIT,
        merge_stderr=True,
    )
    if completed.timed_out:
        raise subprocess.TimeoutExpired(command, timeout, output=completed.stdout)
    return subprocess.CompletedProcess(command, completed.returncode, stdout=completed.stdout)


def _build_under_lock(
    project_root: Path,
    *,
    timeout_seconds: int,
    allow_fixture: bool | None,
    runner: Runner,
) -> dict[str, Any]:
    ownership = db.workspace_mutation_blockers(project_root)
    if ownership["total"]:
        return {
            "ok": False,
            "verdict": "GRAPHIFY_BUILD_ACTIVE_OWNERSHIP",
            "state": "HARD_STOP",
            "reason": "Release active product claims before rebuilding the project graph.",
            "ownership": ownership,
            "rebuilt": False,
        }

    current = graphify_readiness.inspect_graphify_readiness(
        project_root,
        allow_fixture=allow_fixture,
    )
    if current.ok:
        return {
            "ok": True,
            "verdict": "GRAPHIFY_ALREADY_READY",
            "state": "GRAPHIFY_READY",
            "reason": "The project-bound graph already matches the current workspace fingerprint.",
            "readiness": _readiness_payload(current),
            "rebuilt": False,
        }

    fingerprint_before = graphify_readiness.workspace_fingerprint(project_root)["fingerprint"]

    command = [
        sys.executable,
        str(project_root / ".agent" / "workflow" / "scribe" / "scribe"),
        "graph",
        "--project-build",
        "--timeout",
        str(timeout_seconds),
    ]
    try:
        completed = runner(
            command,
            cwd=project_root,
            timeout=timeout_seconds + PROCESS_GRACE_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return {
            "ok": False,
            "verdict": "GRAPHIFY_BUILD_TIMEOUT",
            "state": "HARD_STOP",
            "reason": f"Graphify exceeded the explicit {timeout_seconds}s build bound.",
            "output": output[-OUTPUT_LIMIT:],
            "rebuilt": False,
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "verdict": "GRAPHIFY_PROJECT_BUILD_FAILED",
            "state": "HARD_STOP",
            "reason": str(exc),
            "returncode": 127,
            "output": "",
            "rebuilt": False,
        }
    except OSError as exc:
        return {
            "ok": False,
            "verdict": "GRAPHIFY_PROJECT_BUILD_FAILED",
            "state": "HARD_STOP",
            "reason": str(exc),
            "returncode": 126,
            "output": "",
            "rebuilt": False,
        }

    output = completed.stdout or ""
    if completed.returncode != 0:
        return {
            "ok": False,
            "verdict": "GRAPHIFY_PROJECT_BUILD_FAILED",
            "state": "HARD_STOP",
            "reason": "The canonical isolated Graphify build returned a non-zero status.",
            "returncode": completed.returncode,
            "output": output[-OUTPUT_LIMIT:],
            "rebuilt": False,
        }

    fingerprint_after = graphify_readiness.workspace_fingerprint(project_root)["fingerprint"]
    if fingerprint_after != fingerprint_before:
        manifest = (
            graphify_readiness.canonical_output_dir(project_root)
            / graphify_readiness.MANIFEST_FILENAME
        )
        invalidation_error = ""
        try:
            manifest.unlink(missing_ok=True)
        except OSError as exc:
            invalidation_error = str(exc)
        return {
            "ok": False,
            "verdict": "GRAPHIFY_WORKSPACE_CHANGED_DURING_BUILD",
            "state": "HARD_STOP",
            "reason": (
                "Project sources changed while Graphify was building; the readiness manifest "
                "was invalidated so a stale graph cannot be accepted."
            ),
            "fingerprint_before": fingerprint_before,
            "fingerprint_after": fingerprint_after,
            "manifest_invalidated": not manifest.exists(),
            "invalidation_error": invalidation_error,
            "output": output[-OUTPUT_LIMIT:],
            "rebuilt": False,
        }

    verified = graphify_readiness.inspect_graphify_readiness(
        project_root,
        allow_fixture=allow_fixture,
    )
    if not verified.ok:
        return {
            "ok": False,
            "verdict": verified.verdict,
            "state": "HARD_STOP",
            "reason": "Graphify completed but the published graph failed readiness revalidation.",
            "output": output[-OUTPUT_LIMIT:],
            "readiness": _readiness_payload(verified),
            "rebuilt": False,
        }

    return {
        "ok": True,
        "verdict": "GRAPHIFY_PROJECT_BUILD_OK",
        "state": "GRAPHIFY_READY",
        "output": output[-OUTPUT_LIMIT:],
        "readiness": _readiness_payload(verified),
        "rebuilt": True,
    }


def build_project_graph(
    project_root: Path | str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    lock_held: bool = False,
    allow_fixture: bool | None = None,
    runner: Runner = _run_graphify,
) -> dict[str, Any]:
    """Build Graphify once for a project and make concurrent callers converge.

    The canonical TENOR INIT path already owns the shared init lock and passes
    ``lock_held=True``. Host-visible MCP callers acquire the same lock here,
    then recheck readiness after waiting so only one process performs the
    expensive build.
    """

    try:
        timeout = int(timeout_seconds)
    except (TypeError, ValueError):
        timeout = 0
    if timeout < MIN_TIMEOUT_SECONDS or timeout > MAX_TIMEOUT_SECONDS:
        return {
            "ok": False,
            "verdict": "GRAPHIFY_BUILD_TIMEOUT_INVALID",
            "state": "HARD_STOP",
            "reason": (
                f"timeout_seconds must be between {MIN_TIMEOUT_SECONDS} "
                f"and {MAX_TIMEOUT_SECONDS}."
            ),
            "rebuilt": False,
        }

    root = Path(project_root).resolve()
    if lock_held:
        return _build_under_lock(
            root,
            timeout_seconds=timeout,
            allow_fixture=allow_fixture,
            runner=runner,
        )

    wait_timeout = float(timeout + PROCESS_GRACE_SECONDS + 30)
    stale_after = float(max(900, timeout + PROCESS_GRACE_SECONDS + 300))
    try:
        with tenor_init_orchestrator.tenor_init_lock(
            root,
            wait_timeout_seconds=wait_timeout,
            stale_after_seconds=stale_after,
        ) as acquired:
            tenor_init_orchestrator.refresh_tenor_init_lock(
                acquired,
                stage="graphify_project_build_single_flight",
            )
            return _build_under_lock(
                root,
                timeout_seconds=timeout,
                allow_fixture=allow_fixture,
                runner=runner,
            )
    except tenor_init_orchestrator.TenorInitBusy as exc:
        return {
            "ok": False,
            "verdict": "GRAPHIFY_BUILD_INIT_BUSY",
            "state": "HARD_STOP",
            "reason": "The shared TENOR init/build lock did not become available within the bound.",
            "owner": exc.lock,
            "rebuilt": False,
        }
