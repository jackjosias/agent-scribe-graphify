from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .owned_file_lock import OwnedFileLockTimeout, owned_file_lock


POLICY_FILENAME = "graphify_runtime_policy.json"
POLICY_SCHEMA = "tenor_graphify_runtime_policy_v1"
RUNTIME_SCHEMA = "tenor_graphify_local_runtime_v1"
RUNTIME_RELATIVE = Path(".agent") / "state" / "runtime" / "toolchains" / "graphify"
LOCK_RELATIVE = Path(".agent") / "state" / "runtime" / "locks" / "graphify-runtime-install.lock"
MANIFEST_FILENAME = "TENOR_GRAPHIFY_RUNTIME.json"
LAUNCHER_FILENAME = "tenor_graphify.py"
SITE_DIRNAME = "site"
ARTIFACT_DIRNAME = "artifacts"
CONSTRAINTS_FILENAME = "constraints.txt"
DEFAULT_TIMEOUT_SECONDS = 180
OUTPUT_LIMIT = 20_000
MIN_PYTHON = (3, 10)
_HEX_SHA256 = re.compile(r"\A[a-f0-9]{64}\Z")

Runner = Callable[
    ...,
    subprocess.CompletedProcess[str],
]

_CACHE_GUARD = threading.Lock()
_RESOLUTION_CACHE: dict[str, dict[str, Any]] = {}


def clear_runtime_resolution_cache() -> None:
    with _CACHE_GUARD:
        _RESOLUTION_CACHE.clear()


def runtime_policy_path(project_root: Path | str) -> Path:
    root = Path(project_root).resolve()
    return root / ".agent" / "mcp" / "runtime" / POLICY_FILENAME


def _policy_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_runtime_policy(project_root: Path | str) -> dict[str, Any]:
    path = runtime_policy_path(project_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Graphify runtime policy is missing: {path}") from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Graphify runtime policy is unreadable: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Graphify runtime policy must be a JSON object")
    if value.get("schema") != POLICY_SCHEMA:
        raise RuntimeError(f"unsupported Graphify runtime policy schema: {value.get('schema')!r}")
    for key in ("package", "version", "module", "index_url", "requires_python"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise RuntimeError(f"Graphify runtime policy field {key!r} must be non-empty")
    wheel = value.get("wheel")
    if not isinstance(wheel, dict):
        raise RuntimeError("Graphify runtime policy wheel must be an object")
    filename = wheel.get("filename")
    digest = wheel.get("sha256")
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or not filename.endswith(".whl")
    ):
        raise RuntimeError("Graphify runtime wheel filename is invalid")
    if not isinstance(digest, str) or _HEX_SHA256.fullmatch(digest) is None:
        raise RuntimeError("Graphify runtime wheel SHA-256 is invalid")
    constraints = value.get("constraints")
    if not isinstance(constraints, list) or not constraints:
        raise RuntimeError("Graphify runtime constraints must be a non-empty list")
    for constraint in constraints:
        if (
            not isinstance(constraint, str)
            or not constraint.strip()
            or "\n" in constraint
            or "\r" in constraint
            or constraint.lstrip().startswith(("-", "http:", "https:"))
        ):
            raise RuntimeError(f"unsafe Graphify runtime constraint: {constraint!r}")
    return value


def runtime_platform_key() -> str:
    implementation = getattr(sys.implementation, "cache_tag", None) or "python"
    system = platform.system().lower() or "unknown"
    machine = platform.machine().lower() or "unknown"
    abi_platform = sysconfig.get_platform().lower()
    raw = f"{implementation}-{system}-{machine}-{abi_platform}"
    return re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-")


def runtime_install_dir(project_root: Path | str) -> Path:
    root = Path(project_root).resolve()
    try:
        policy = load_runtime_policy(root)
        version = str(policy["version"])
    except RuntimeError:
        version = "unknown"
    return root / RUNTIME_RELATIVE / version / runtime_platform_key()


def _default_runner(
    command: list[str],
    *,
    cwd: Path,
    timeout: float,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=max(1.0, timeout),
            check=False,
            shell=False,
        )
        output = completed.stdout or ""
        return subprocess.CompletedProcess(
            command,
            completed.returncode,
            stdout=output[-OUTPUT_LIMIT:],
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(command, 127, stdout=str(exc))
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return subprocess.CompletedProcess(
            command,
            124,
            stdout=(output + f"\ntimeout after {timeout:.1f}s")[-OUTPUT_LIMIT:],
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 126, stdout=str(exc))


def _sanitized_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for key in list(environment):
        normalized = key.upper()
        if normalized.startswith("PIP_") or normalized in {"PYTHONHOME", "PYTHONPATH"}:
            environment.pop(key, None)
    environment.update(
        {
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
        }
    )
    return environment


def _runtime_launcher(site_dir: Path, policy: dict[str, Any]) -> str:
    del site_dir
    package = json.dumps(str(policy["package"]))
    version = json.dumps(str(policy["version"]))
    module = json.dumps(str(policy["module"]))
    return f"""from __future__ import annotations

import importlib.metadata
import json
import runpy
import sys
from pathlib import Path

SITE = str(Path(__file__).resolve().parent / {SITE_DIRNAME!r})
PACKAGE = {package}
VERSION = {version}
MODULE = {module}

sys.dont_write_bytecode = True
if SITE not in sys.path:
    sys.path.insert(0, SITE)

if len(sys.argv) == 2 and sys.argv[1] == "--tenor-runtime-probe":
    installed = importlib.metadata.version(PACKAGE)
    print(json.dumps({{"package": PACKAGE, "version": installed, "module": MODULE}}, sort_keys=True))
    raise SystemExit(0 if installed == VERSION else 70)

runpy.run_module(MODULE, run_name="__main__", alter_sys=True)
"""


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _integrity_digest(runtime_dir: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    file_count = 0
    byte_count = 0
    for path in sorted(runtime_dir.rglob("*"), key=lambda candidate: candidate.as_posix()):
        if not path.is_file() or path.name == MANIFEST_FILENAME:
            continue
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(runtime_dir).as_posix()
        size = path.stat().st_size
        content_digest = _file_sha256(path)
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(content_digest.encode("ascii"))
        digest.update(b"\n")
        file_count += 1
        byte_count += size
    return f"sha256:{digest.hexdigest()}", file_count, byte_count


def _command_for(runtime_dir: Path) -> list[str]:
    return [
        sys.executable,
        "-I",
        str(runtime_dir / LAUNCHER_FILENAME),
    ]


def _parse_probe(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def _cached(root: Path) -> dict[str, Any] | None:
    key = os.path.normcase(str(root))
    with _CACHE_GUARD:
        value = _RESOLUTION_CACHE.get(key)
        return dict(value) if value is not None else None


def _cache(root: Path, value: dict[str, Any]) -> None:
    key = os.path.normcase(str(root))
    with _CACHE_GUARD:
        _RESOLUTION_CACHE[key] = dict(value)


def inspect_graphify_runtime(
    project_root: Path | str,
    *,
    runner: Runner = _default_runner,
    use_cache: bool = True,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    if use_cache:
        cached = _cached(root)
        if cached is not None:
            return cached
    try:
        policy = load_runtime_policy(root)
    except RuntimeError as exc:
        return {
            "ok": False,
            "verdict": "GRAPHIFY_RUNTIME_POLICY_INVALID",
            "reason": str(exc),
            "source": "project_local",
        }
    runtime_dir = runtime_install_dir(root)
    manifest_path = runtime_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return {
            "ok": False,
            "verdict": "GRAPHIFY_LOCAL_RUNTIME_MISSING",
            "reason": "The pinned project-local Graphify runtime has not been installed.",
            "runtime_dir": runtime_dir,
            "source": "project_local",
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "verdict": "GRAPHIFY_LOCAL_RUNTIME_MANIFEST_INVALID",
            "reason": str(exc),
            "runtime_dir": runtime_dir,
            "source": "project_local",
        }
    expected = {
        "schema": RUNTIME_SCHEMA,
        "package": policy["package"],
        "version": policy["version"],
        "module": policy["module"],
        "platform_key": runtime_platform_key(),
        "policy_sha256": _policy_digest(runtime_policy_path(root)),
        "wheel_sha256": policy["wheel"]["sha256"],
    }
    mismatches = {
        key: {"expected": value, "actual": manifest.get(key)}
        for key, value in expected.items()
        if manifest.get(key) != value
    }
    launcher = runtime_dir / LAUNCHER_FILENAME
    site_dir = runtime_dir / SITE_DIRNAME
    if mismatches or not launcher.is_file() or not site_dir.is_dir():
        return {
            "ok": False,
            "verdict": "GRAPHIFY_LOCAL_RUNTIME_MANIFEST_MISMATCH",
            "reason": "The local runtime manifest does not match the pinned policy or layout.",
            "mismatches": mismatches,
            "runtime_dir": runtime_dir,
            "source": "project_local",
        }
    integrity, file_count, byte_count = _integrity_digest(runtime_dir)
    if integrity != manifest.get("integrity_sha256"):
        return {
            "ok": False,
            "verdict": "GRAPHIFY_LOCAL_RUNTIME_INTEGRITY_FAILED",
            "reason": "The project-local Graphify runtime bytes changed after publication.",
            "expected_integrity": manifest.get("integrity_sha256"),
            "actual_integrity": integrity,
            "runtime_dir": runtime_dir,
            "source": "project_local",
        }
    command = _command_for(runtime_dir)
    probe = runner(
        [*command, "--tenor-runtime-probe"],
        cwd=root,
        timeout=15,
        env=_sanitized_environment(),
    )
    probe_payload = _parse_probe(probe.stdout or "")
    if (
        probe.returncode != 0
        or probe_payload.get("package") != policy["package"]
        or probe_payload.get("version") != policy["version"]
        or probe_payload.get("module") != policy["module"]
    ):
        return {
            "ok": False,
            "verdict": "GRAPHIFY_LOCAL_RUNTIME_PROBE_FAILED",
            "reason": "The isolated Graphify runtime did not report the pinned distribution.",
            "returncode": probe.returncode,
            "output": (probe.stdout or "")[-OUTPUT_LIMIT:],
            "probe": probe_payload,
            "runtime_dir": runtime_dir,
            "source": "project_local",
        }
    result = {
        "ok": True,
        "verdict": "GRAPHIFY_LOCAL_RUNTIME_READY",
        "source": "project_local",
        "package": policy["package"],
        "version": policy["version"],
        "wheel_sha256": policy["wheel"]["sha256"],
        "policy_sha256": expected["policy_sha256"],
        "integrity_sha256": integrity,
        "integrity_files": file_count,
        "integrity_bytes": byte_count,
        "runtime_dir": runtime_dir,
        "command": command,
        "manifest": manifest,
    }
    _cache(root, result)
    return result


def _external_graphify(
    project_root: Path,
    *,
    runner: Runner,
) -> dict[str, Any] | None:
    resolved = shutil.which("graphify")
    if resolved is None:
        return None
    try:
        binary = Path(resolved).resolve()
    except OSError:
        return None
    environment = _sanitized_environment()
    probe = runner(
        [str(binary), "--version"],
        cwd=project_root,
        timeout=10,
        env=environment,
    )
    if probe.returncode != 0:
        probe = runner(
            [str(binary), "--help"],
            cwd=project_root,
            timeout=10,
            env=environment,
        )
    if probe.returncode != 0:
        return {
            "ok": False,
            "verdict": "GRAPHIFY_EXTERNAL_RUNTIME_PROBE_FAILED",
            "source": "external_legacy",
            "command": [str(binary)],
            "runtime_dir": binary.parent,
            "returncode": probe.returncode,
            "output": (probe.stdout or "")[-OUTPUT_LIMIT:],
        }
    return {
        "ok": True,
        "verdict": "GRAPHIFY_EXTERNAL_RUNTIME_READY",
        "source": "external_legacy",
        "command": [str(binary)],
        "runtime_dir": binary.parent,
        "version": (probe.stdout or "").strip() or None,
        "supply_chain_verified": False,
    }


def resolve_graphify_runtime(
    project_root: Path | str,
    *,
    runner: Runner = _default_runner,
    allow_external: bool = True,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    local = inspect_graphify_runtime(root, runner=runner)
    if local.get("ok"):
        return local
    if allow_external:
        external = _external_graphify(root, runner=runner)
        if external is not None:
            return external
    return local


def _remaining(deadline: float) -> float:
    return max(1.0, deadline - time.monotonic())


def _quarantine(path: Path, *, prefix: str) -> Path | None:
    if not path.exists():
        return None
    destination = path.parent / f".{prefix}-{uuid.uuid4().hex}"
    os.replace(path, destination)
    return destination


def _failure(
    verdict: str,
    reason: str,
    *,
    staging: Path | None = None,
    returncode: int | None = None,
    output: str = "",
) -> dict[str, Any]:
    failed = None
    if staging is not None and staging.exists():
        try:
            failed = _quarantine(staging, prefix="failed")
        except OSError:
            failed = staging
    result: dict[str, Any] = {
        "ok": False,
        "verdict": verdict,
        "reason": reason,
        "source": "project_local",
        "output": output[-OUTPUT_LIMIT:],
    }
    if returncode is not None:
        result["returncode"] = returncode
    if failed is not None:
        result["failed_runtime"] = failed
    return result


def ensure_graphify_runtime(
    project_root: Path | str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    runner: Runner = _default_runner,
    allow_external: bool = True,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    if sys.version_info < MIN_PYTHON:
        return {
            "ok": False,
            "verdict": "GRAPHIFY_RUNTIME_PYTHON_UNSUPPORTED",
            "reason": f"Graphify requires Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer.",
            "python": platform.python_version(),
            "source": "project_local",
        }
    resolved = resolve_graphify_runtime(
        root,
        runner=runner,
        allow_external=allow_external,
    )
    if resolved.get("ok"):
        return resolved
    try:
        timeout = int(timeout_seconds)
    except (TypeError, ValueError):
        timeout = 0
    if timeout < 1 or timeout > 3600:
        return {
            "ok": False,
            "verdict": "GRAPHIFY_RUNTIME_TIMEOUT_INVALID",
            "reason": "timeout_seconds must be between 1 and 3600.",
            "source": "project_local",
        }
    try:
        policy = load_runtime_policy(root)
    except RuntimeError as exc:
        return {
            "ok": False,
            "verdict": "GRAPHIFY_RUNTIME_POLICY_INVALID",
            "reason": str(exc),
            "source": "project_local",
        }
    lock_path = root / LOCK_RELATIVE
    try:
        with owned_file_lock(
            lock_path,
            purpose=f"graphify-runtime-install:{policy['version']}:{runtime_platform_key()}",
            timeout_seconds=float(timeout + 30),
            stale_after_seconds=float(max(900, timeout + 300)),
        ):
            clear_runtime_resolution_cache()
            current = inspect_graphify_runtime(root, runner=runner, use_cache=False)
            if current.get("ok"):
                return current

            deadline = time.monotonic() + timeout
            final = runtime_install_dir(root)
            final.parent.mkdir(parents=True, exist_ok=True)
            staging = final.parent / f".{final.name}.installing-{uuid.uuid4().hex}"
            staging.mkdir(parents=False, exist_ok=False)
            artifacts = staging / ARTIFACT_DIRNAME
            site_dir = staging / SITE_DIRNAME
            artifacts.mkdir()
            site_dir.mkdir()
            constraints_path = staging / CONSTRAINTS_FILENAME
            _atomic_text(
                constraints_path,
                "\n".join(str(value) for value in policy["constraints"]) + "\n",
            )
            environment = _sanitized_environment()
            pip_cache = root / ".agent" / "state" / "runtime" / "pip-cache"
            download_command = [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--disable-pip-version-check",
                "--no-input",
                "--no-deps",
                "--only-binary=:all:",
                "--index-url",
                str(policy["index_url"]),
                "--cache-dir",
                str(pip_cache),
                "--dest",
                str(artifacts),
                f"{policy['package']}=={policy['version']}",
            ]
            downloaded = runner(
                download_command,
                cwd=root,
                timeout=_remaining(deadline),
                env=environment,
            )
            if downloaded.returncode != 0:
                return _failure(
                    "GRAPHIFY_RUNTIME_WHEEL_DOWNLOAD_FAILED",
                    "The pinned Graphify wheel could not be downloaded from the configured index.",
                    staging=staging,
                    returncode=downloaded.returncode,
                    output=downloaded.stdout or "",
                )
            wheel_path = artifacts / str(policy["wheel"]["filename"])
            if not wheel_path.is_file():
                return _failure(
                    "GRAPHIFY_RUNTIME_WHEEL_MISSING",
                    "pip did not produce the exact wheel filename declared by the policy.",
                    staging=staging,
                    output=downloaded.stdout or "",
                )
            actual_wheel_sha = _file_sha256(wheel_path)
            if actual_wheel_sha != policy["wheel"]["sha256"]:
                return _failure(
                    "GRAPHIFY_RUNTIME_WHEEL_HASH_MISMATCH",
                    "The downloaded Graphify wheel does not match the pinned SHA-256.",
                    staging=staging,
                    output=(
                        f"expected={policy['wheel']['sha256']} "
                        f"actual={actual_wheel_sha}"
                    ),
                )

            install_command = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--only-binary=:all:",
                "--index-url",
                str(policy["index_url"]),
                "--cache-dir",
                str(pip_cache),
                "--constraint",
                str(constraints_path),
                "--target",
                str(site_dir),
                str(wheel_path),
            ]
            installed = runner(
                install_command,
                cwd=root,
                timeout=_remaining(deadline),
                env=environment,
            )
            if installed.returncode != 0:
                return _failure(
                    "GRAPHIFY_RUNTIME_DEPENDENCY_INSTALL_FAILED",
                    "The private Graphify dependency set could not be installed from binary wheels.",
                    staging=staging,
                    returncode=installed.returncode,
                    output=installed.stdout or "",
                )

            launcher = staging / LAUNCHER_FILENAME
            _atomic_text(launcher, _runtime_launcher(site_dir, policy))
            command = _command_for(staging)
            probe = runner(
                [*command, "--tenor-runtime-probe"],
                cwd=root,
                timeout=_remaining(deadline),
                env=environment,
            )
            probe_payload = _parse_probe(probe.stdout or "")
            if (
                probe.returncode != 0
                or probe_payload.get("package") != policy["package"]
                or probe_payload.get("version") != policy["version"]
                or probe_payload.get("module") != policy["module"]
            ):
                return _failure(
                    "GRAPHIFY_RUNTIME_INSTALL_PROBE_FAILED",
                    "The staged Graphify runtime did not report the pinned package identity.",
                    staging=staging,
                    returncode=probe.returncode,
                    output=probe.stdout or "",
                )

            integrity, file_count, byte_count = _integrity_digest(staging)
            manifest = {
                "schema": RUNTIME_SCHEMA,
                "package": policy["package"],
                "version": policy["version"],
                "module": policy["module"],
                "platform_key": runtime_platform_key(),
                "python_executable": sys.executable,
                "python_version": platform.python_version(),
                "policy_sha256": _policy_digest(runtime_policy_path(root)),
                "wheel_filename": policy["wheel"]["filename"],
                "wheel_sha256": policy["wheel"]["sha256"],
                "integrity_sha256": integrity,
                "integrity_files": file_count,
                "integrity_bytes": byte_count,
                "installed_at_epoch": time.time(),
                "source": "pypi_verified_wheel_project_local",
            }
            _atomic_json(staging / MANIFEST_FILENAME, manifest)

            quarantined = None
            if final.exists():
                quarantined = _quarantine(final, prefix="quarantine")
            try:
                os.replace(staging, final)
            except Exception:
                if quarantined is not None and quarantined.exists() and not final.exists():
                    os.replace(quarantined, final)
                raise

            clear_runtime_resolution_cache()
            verified = inspect_graphify_runtime(root, runner=runner, use_cache=False)
            if not verified.get("ok"):
                _quarantine(final, prefix="failed-published")
                if quarantined is not None and quarantined.exists():
                    os.replace(quarantined, final)
                return {
                    **verified,
                    "verdict": "GRAPHIFY_RUNTIME_POST_PUBLISH_VERIFY_FAILED",
                }
            ready_for_cache = dict(verified)
            ready_for_cache["verdict"] = "GRAPHIFY_LOCAL_RUNTIME_READY"
            ready_for_cache["installed"] = False
            verified["verdict"] = "GRAPHIFY_LOCAL_RUNTIME_INSTALLED"
            verified["installed"] = True
            if quarantined is not None:
                verified["quarantined_previous_runtime"] = quarantined
            _cache(root, ready_for_cache)
            return verified
    except OwnedFileLockTimeout as exc:
        return {
            "ok": False,
            "verdict": "GRAPHIFY_RUNTIME_INSTALL_BUSY",
            "reason": "The project-local Graphify runtime install lock did not become available.",
            "owner": exc.owner,
            "source": "project_local",
        }
    except Exception as exc:
        return {
            "ok": False,
            "verdict": "GRAPHIFY_RUNTIME_INSTALL_FAILED",
            "reason": f"{type(exc).__name__}: {exc}",
            "source": "project_local",
        }


def graphify_command(
    project_root: Path | str,
    *,
    install_if_missing: bool,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    runner: Runner = _default_runner,
    allow_external: bool = True,
) -> dict[str, Any]:
    if install_if_missing:
        return ensure_graphify_runtime(
            project_root,
            timeout_seconds=timeout_seconds,
            runner=runner,
            allow_external=allow_external,
        )
    return resolve_graphify_runtime(
        project_root,
        runner=runner,
        allow_external=allow_external,
    )
