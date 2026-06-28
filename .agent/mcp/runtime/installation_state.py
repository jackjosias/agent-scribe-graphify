from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INSTALL_MANIFEST_RELATIVE = Path(".agent") / "state" / "install" / "agent-installation.json"
INSTALL_SCHEMA = "agent_installation_v1"

AGENT_INSTALLATION_MANIFEST_CREATED = "AGENT_INSTALLATION_MANIFEST_CREATED"
AGENT_INSTALLATION_CURRENT = "AGENT_INSTALLATION_CURRENT"
AGENT_BUNDLE_RELOCATION_DETECTED = "AGENT_BUNDLE_RELOCATION_DETECTED"
PROJECT_BOUND_STATE_PURGED = "PROJECT_BOUND_STATE_PURGED"
LEGACY_STATE_WITHOUT_INSTALL_MANIFEST_PURGED = "LEGACY_STATE_WITHOUT_INSTALL_MANIFEST_PURGED"
CORRUPT_INSTALLATION_MANIFEST_PURGED = "CORRUPT_INSTALLATION_MANIFEST_PURGED"
PROJECT_BOUND_STATE_PURGE_REFUSED = "PROJECT_BOUND_STATE_PURGE_REFUSED"
TENOR_INIT_FRESH_RUNTIME_READY = "TENOR_INIT_FRESH_RUNTIME_READY"

_LEGACY_STATE_NAMES = {
    "runtime",
    "proof",
    "locks",
    "sessions",
    "agents",
    "redteam",
    "backups",
}
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


def _atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _state_contains_project_bound_data(state_dir: Path) -> bool:
    if not state_dir.exists():
        return False
    for child in state_dir.iterdir():
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
    if root == Path("/") or state_dir == Path("/") or state_dir == root or state_dir == Path.home():
        return False, "refusing dangerous state path"
    if state_dir.exists() and state_dir.is_symlink():
        return False, "state directory must not be a symlink"
    if not (agent_dir / "mcp" / "server_entry.py").is_file():
        return False, "server_entry.py marker missing"
    try:
        resolved_agent = agent_dir.resolve(strict=True)
    except FileNotFoundError:
        return False, ".agent directory missing"
    resolved_state_parent = state_dir.parent.resolve(strict=True)
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
    if data.get("schema") != INSTALL_SCHEMA:
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


def write_installation_manifest(project_root: Path) -> dict[str, Any]:
    now = _utc_now()
    fingerprint = current_installation_fingerprint(project_root)
    existing: dict[str, Any] | None
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
        "created_at": (existing or {}).get("created_at") or now,
        "last_seen_at": now,
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
            "verdict": "AGENT_INSTALLATION_FINGERPRINT_REFRESH_REQUIRED",
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


def purge_project_bound_state(project_root: Path) -> dict[str, Any]:
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
    if state_dir.exists():
        shutil.rmtree(state_dir)
    (state_dir / "install").mkdir(parents=True, exist_ok=True)
    return {
        "ok": True,
        "verdict": PROJECT_BOUND_STATE_PURGED,
        "state_dir": str(state_dir),
        "install_dir": str(state_dir / "install"),
    }


def ensure_fresh_installation_state(project_root: Path, *, allow_purge: bool = True) -> dict[str, Any]:
    root = project_root.resolve()
    state_dir = _state_dir(root)
    state_dir.mkdir(parents=True, exist_ok=True)
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

    manifest = write_installation_manifest(root)
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
