from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INSTALL_MANIFEST_RELATIVE = Path(".agent") / "state" / "install" / "agent-installation.json"
INSTALL_SCHEMA = "agent_installation_v2"
INSTALL_STATUS_PREPARING = "preparing"
INSTALL_STATUS_READY = "ready"

AGENT_INSTALLATION_MANIFEST_CREATED = "AGENT_INSTALLATION_MANIFEST_CREATED"
AGENT_INSTALLATION_CURRENT = "AGENT_INSTALLATION_CURRENT"
AGENT_BUNDLE_RELOCATION_DETECTED = "AGENT_BUNDLE_RELOCATION_DETECTED"
AGENT_INSTALLATION_FINGERPRINT_REFRESH_REQUIRED = "AGENT_INSTALLATION_FINGERPRINT_REFRESH_REQUIRED"
AGENT_INSTALLATION_INIT_INCOMPLETE = "AGENT_INSTALLATION_INIT_INCOMPLETE"
PROJECT_BOUND_STATE_PURGED = "PROJECT_BOUND_STATE_PURGED"
LEGACY_STATE_WITHOUT_INSTALL_MANIFEST_PURGED = "LEGACY_STATE_WITHOUT_INSTALL_MANIFEST_PURGED"
CORRUPT_INSTALLATION_MANIFEST_PURGED = "CORRUPT_INSTALLATION_MANIFEST_PURGED"
PROJECT_BOUND_STATE_PURGE_REFUSED = "PROJECT_BOUND_STATE_PURGE_REFUSED"
TENOR_INIT_FRESH_RUNTIME_READY = "TENOR_INIT_FRESH_RUNTIME_READY"
TENOR_INIT_REQUIRED = "TENOR_INIT_REQUIRED"
TENOR_INIT_GATE_READY = "TENOR_INIT_GATE_READY"

_ATOMIC_REPLACE_ATTEMPTS = 10
_ATOMIC_REPLACE_INITIAL_BACKOFF_SECONDS = 0.01
_MANIFEST_TRANSACTION_LOCK = threading.RLock()
_LEGACY_STATE_NAMES = {
    "runtime",
    "proof",
    "locks",
    "sessions",
    "agents",
    "redteam",
    "backups",
}
# Outputs contain canonical/generated evidence that must survive a runtime purge.
# Graphify content is preserved for losslessness but must still pass the existing
# root/fingerprint readiness checks before TENOR_INIT_READY can be emitted.
_PRESERVED_STATE_DIR_NAMES = {"outputs"}
_PROJECT_MARKERS = (
    ".git",
    "README.md",
    "AGENTS.md",
    "AGENT-MEMOIRE_PROJECT_STATUS.scribe",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _git_root(project_root: Path) -> Path | None:
    if (project_root / ".git").exists():
        return project_root.resolve()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(project_root),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return Path(proc.stdout.strip()).resolve()


def _manifest_path(project_root: Path) -> Path:
    return project_root.resolve() / INSTALL_MANIFEST_RELATIVE


def _state_dir(project_root: Path) -> Path:
    return project_root.resolve() / ".agent" / "state"


def _replace_with_retry(source: Path, target: Path) -> None:
    """Replace atomically while tolerating short Windows sharing violations."""

    delay = _ATOMIC_REPLACE_INITIAL_BACKOFF_SECONDS
    for attempt in range(_ATOMIC_REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 >= _ATOMIC_REPLACE_ATTEMPTS:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.25)


def _atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.{os.getpid()}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _state_contains_project_bound_data(state_dir: Path) -> bool:
    if not state_dir.exists():
        return False
    try:
        children = tuple(state_dir.iterdir())
    except OSError:
        return True
    for child in children:
        if child.name == "install":
            continue
        if child.name in _LEGACY_STATE_NAMES:
            return True
        if child.is_file() and (
            child.suffix in {".sqlite", ".lock"}
            or child.name.endswith((".sqlite-wal", ".sqlite-shm"))
            or ".backup-" in child.name
        ):
            return True
        if child.is_dir():
            return True
    return False


def _validate_project_bound_state_dir(project_root: Path) -> tuple[bool, str]:
    root = project_root.resolve()
    agent_dir = root / ".agent"
    state_dir = agent_dir / "state"
    if agent_dir.name != ".agent" or state_dir.name != "state":
        return False, "state path must be <project_root>/.agent/state"
    if state_dir.parent != agent_dir:
        return False, "state parent must be .agent"
    if root == Path(root.anchor) or state_dir == Path(state_dir.anchor) or state_dir == root or state_dir == Path.home():
        return False, "refusing dangerous state path"
    if state_dir.exists() and state_dir.is_symlink():
        return False, "state directory must not be a symlink"
    if not (agent_dir / "mcp" / "server_entry.py").is_file():
        return False, "server_entry.py marker missing"
    try:
        resolved_agent = agent_dir.resolve(strict=True)
        resolved_state_parent = state_dir.parent.resolve(strict=True)
    except FileNotFoundError:
        return False, ".agent directory missing"
    if resolved_state_parent != resolved_agent:
        return False, "state parent resolves outside .agent"
    resolved_state = state_dir.resolve(strict=False)
    if not _is_relative_to(resolved_state, resolved_agent) or resolved_state == resolved_agent:
        return False, "state resolves outside .agent"
    return True, "ok"


def load_installation_manifest(project_root: Path) -> dict[str, Any] | None:
    path = _manifest_path(project_root)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("installation manifest must be a JSON object")
    schema = data.get("schema")
    if schema not in {"agent_installation_v1", INSTALL_SCHEMA}:
        raise ValueError("unsupported installation manifest schema")
    return data


def current_installation_fingerprint(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    git = _git_root(root)
    markers = {marker: (root / marker).exists() for marker in _PROJECT_MARKERS}
    material = {
        "project_root": str(root),
        "git_root": str(git) if git else "",
        "markers": markers,
    }
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        **material,
        "agent_dir": str(root / ".agent"),
        "project_name": root.name,
        "project_fingerprint": f"sha256:{digest}",
    }


def write_installation_manifest(project_root: Path, *, init_status: str = INSTALL_STATUS_READY) -> dict[str, Any]:
    if init_status not in {INSTALL_STATUS_PREPARING, INSTALL_STATUS_READY}:
        raise ValueError(f"invalid installation init_status: {init_status}")
    with _MANIFEST_TRANSACTION_LOCK:
        now = _utc_now()
        fingerprint = current_installation_fingerprint(project_root)
        try:
            existing = load_installation_manifest(project_root)
        except (OSError, ValueError, json.JSONDecodeError):
            existing = None
        data = {
            "schema": INSTALL_SCHEMA,
            "project_root": fingerprint["project_root"],
            "git_root": fingerprint["git_root"],
            "agent_dir": fingerprint["agent_dir"],
            "project_name": fingerprint["project_name"],
            "project_markers": fingerprint["markers"],
            "project_fingerprint": fingerprint["project_fingerprint"],
            "init_status": init_status,
            "created_at": (existing or {}).get("created_at") or now,
            "last_seen_at": now,
            "ready_at": now if init_status == INSTALL_STATUS_READY else (existing or {}).get("ready_at") or "",
        }
        _atomic_json_write(_manifest_path(project_root), data)
        return data


def detect_agent_relocation(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    state_dir = _state_dir(root)
    try:
        manifest = load_installation_manifest(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "ok": True,
            "verdict": "CORRUPT_INSTALLATION_MANIFEST",
            "reason": str(exc),
            "relocated": True,
            "purge_required": True,
            "state_dir": str(state_dir),
        }
    if manifest is None:
        legacy_state = _state_contains_project_bound_data(state_dir)
        return {
            "ok": True,
            "verdict": "LEGACY_STATE_WITHOUT_INSTALL_MANIFEST" if legacy_state else "INSTALLATION_MANIFEST_MISSING_EMPTY_STATE",
            "relocated": False,
            "purge_required": legacy_state,
            "state_dir": str(state_dir),
        }
    fingerprint = current_installation_fingerprint(root)
    if str(manifest.get("project_root") or "") != fingerprint["project_root"]:
        return {
            "ok": True,
            "verdict": AGENT_BUNDLE_RELOCATION_DETECTED,
            "relocated": True,
            "purge_required": True,
            "previous_project_root": manifest.get("project_root"),
            "current_project_root": fingerprint["project_root"],
            "state_dir": str(state_dir),
        }
    if str(manifest.get("project_fingerprint") or "") != fingerprint["project_fingerprint"]:
        return {
            "ok": True,
            "verdict": AGENT_INSTALLATION_FINGERPRINT_REFRESH_REQUIRED,
            "relocated": False,
            "purge_required": False,
            "state_dir": str(state_dir),
        }
    if str(manifest.get("init_status") or INSTALL_STATUS_READY) != INSTALL_STATUS_READY:
        return {
            "ok": True,
            "verdict": AGENT_INSTALLATION_INIT_INCOMPLETE,
            "relocated": False,
            "purge_required": False,
            "state_dir": str(state_dir),
        }
    return {
        "ok": True,
        "verdict": AGENT_INSTALLATION_CURRENT,
        "relocated": False,
        "purge_required": False,
        "state_dir": str(state_dir),
    }


def inspect_installation_state(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    detection = detect_agent_relocation(root)
    ready = detection.get("verdict") == AGENT_INSTALLATION_CURRENT
    action = portable_tenor_init_action(root)
    return {
        "ok": True,
        "ready": ready,
        "verdict": TENOR_INIT_GATE_READY if ready else TENOR_INIT_REQUIRED,
        "project_root": str(root),
        "detection": detection,
        "next_action": action["display"] if not ready else "",
        "next_action_argv": action["argv"] if not ready else [],
    }


def portable_tenor_init_action(project_root: Path | None = None) -> dict[str, Any]:
    """Return a current-interpreter action without assuming a `python` alias."""

    interpreter = sys.executable or ("python" if os.name == "nt" else "python3")
    argv = [interpreter, ".agent/workflow/scribe/scribe", "tenor-init", "--type", "cli"]
    display = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    return {
        "argv": argv,
        "display": display,
        "cwd": str(project_root.resolve()) if project_root is not None else ".",
    }


def _remove_state_child(child: Path) -> None:
    if child.is_symlink() or child.is_file():
        child.unlink()
    else:
        shutil.rmtree(child)


def purge_project_bound_state(project_root: Path, *, attempts: int = 5, initial_backoff_seconds: float = 0.05) -> dict[str, Any]:
    root = project_root.resolve()
    state_dir = _state_dir(root)
    valid, reason = _validate_project_bound_state_dir(root)
    if not valid:
        return {
            "ok": False,
            "verdict": PROJECT_BOUND_STATE_PURGE_REFUSED,
            "reason": reason,
            "state_dir": str(state_dir),
        }
    last_error = ""
    for attempt in range(max(1, attempts)):
        try:
            preserved: list[str] = []
            state_dir.mkdir(parents=True, exist_ok=True)
            children = tuple(state_dir.iterdir())
            # Validate every preserved surface before deleting any project-bound
            # runtime child. Unsafe output paths therefore fail closed without a
            # partial purge.
            for child in children:
                if child.name in _PRESERVED_STATE_DIR_NAMES and (child.is_symlink() or not child.is_dir()):
                    raise OSError(f"refusing unsafe preserved state path: {child}")
            for child in children:
                if child.name in _PRESERVED_STATE_DIR_NAMES:
                    preserved.append(str(child.relative_to(root)))
                    continue
                _remove_state_child(child)
            (state_dir / "install").mkdir(parents=True, exist_ok=True)
            return {
                "ok": True,
                "verdict": PROJECT_BOUND_STATE_PURGED,
                "state_dir": str(state_dir),
                "install_dir": str(state_dir / "install"),
                "preserved_state_dirs": preserved,
                "attempts": attempt + 1,
            }
        except OSError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < max(1, attempts):
                time.sleep(initial_backoff_seconds * (2**attempt))
    return {
        "ok": False,
        "verdict": PROJECT_BOUND_STATE_PURGE_REFUSED,
        "reason": last_error or "state purge failed",
        "state_dir": str(state_dir),
        "attempts": max(1, attempts),
    }


def ensure_fresh_installation_state(project_root: Path, *, allow_purge: bool = True) -> dict[str, Any]:
    root = project_root.resolve()
    detection = detect_agent_relocation(root)
    purge_report: dict[str, Any] | None = None
    if detection.get("purge_required"):
        if not allow_purge:
            return {
                "ok": False,
                "verdict": "AGENT_INSTALLATION_PURGE_REQUIRED",
                "detection": detection,
            }
        purge_report = purge_project_bound_state(root)
        if not purge_report.get("ok"):
            return {**purge_report, "detection": detection}
    manifest = write_installation_manifest(root, init_status=INSTALL_STATUS_PREPARING)
    initial_verdict = str(detection.get("verdict") or "")
    if purge_report is not None:
        if initial_verdict == AGENT_BUNDLE_RELOCATION_DETECTED:
            verdict = AGENT_BUNDLE_RELOCATION_DETECTED
        elif initial_verdict == "CORRUPT_INSTALLATION_MANIFEST":
            verdict = CORRUPT_INSTALLATION_MANIFEST_PURGED
        else:
            verdict = LEGACY_STATE_WITHOUT_INSTALL_MANIFEST_PURGED
    elif initial_verdict == "INSTALLATION_MANIFEST_MISSING_EMPTY_STATE":
        verdict = AGENT_INSTALLATION_MANIFEST_CREATED
    else:
        verdict = AGENT_INSTALLATION_CURRENT
    return {
        "ok": True,
        "verdict": verdict,
        "runtime_verdict": TENOR_INIT_FRESH_RUNTIME_READY,
        "manifest_path": str(_manifest_path(root)),
        "manifest": manifest,
        "detection": detection,
        "purge": purge_report,
    }


def finalize_installation_state(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    with _MANIFEST_TRANSACTION_LOCK:
        manifest = write_installation_manifest(root, init_status=INSTALL_STATUS_READY)
        gate = inspect_installation_state(root)
        if not gate.get("ready"):
            return {
                "ok": False,
                "verdict": TENOR_INIT_REQUIRED,
                "manifest": manifest,
                "gate": gate,
            }
        return {
            "ok": True,
            "verdict": TENOR_INIT_GATE_READY,
            "manifest_path": str(_manifest_path(root)),
            "manifest": manifest,
            "gate": gate,
        }
