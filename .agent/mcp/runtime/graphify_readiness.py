from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    from .owned_file_lock import owned_file_lock
    from .state_paths import prepare_state_dirs
except ImportError:
    from owned_file_lock import owned_file_lock  # type: ignore
    from state_paths import prepare_state_dirs  # type: ignore

MANIFEST_SCHEMA = "graphify_readiness_v1"
MANIFEST_FILENAME = "GRAPHIFY_READY.json"
REQUIRED_FILES = ("graph.json", "GRAPH_REPORT.md", "graph.html")
FIXTURE_ENV = "AGENT_ALLOW_GRAPHIFY_TEST_FIXTURE"

GRAPHIFY_READY = "GRAPHIFY_READY"
GRAPHIFY_EMPTY_PROJECT_READY = "GRAPHIFY_EMPTY_PROJECT_READY"
GRAPHIFY_TEST_FIXTURE_READY = "GRAPHIFY_TEST_FIXTURE_READY"
GRAPHIFY_MISSING = "GRAPHIFY_MISSING"
GRAPHIFY_OUTPUTS_INCOMPLETE = "GRAPHIFY_OUTPUTS_INCOMPLETE"
GRAPHIFY_STUB_INVALID = "GRAPHIFY_STUB_INVALID"
GRAPHIFY_CORRUPT = "GRAPHIFY_CORRUPT"
GRAPHIFY_LEGACY_UNBOUND = "GRAPHIFY_LEGACY_UNBOUND"
GRAPHIFY_STALE_ROOT = "GRAPHIFY_STALE_ROOT"
GRAPHIFY_STALE_WORKSPACE = "GRAPHIFY_STALE_WORKSPACE"
GRAPHIFY_FIXTURE_FORBIDDEN = "GRAPHIFY_FIXTURE_FORBIDDEN"
GRAPHIFY_MANIFEST_INVALID = "GRAPHIFY_MANIFEST_INVALID"
GRAPHIFY_WORKSPACE_TOO_LARGE = "GRAPHIFY_WORKSPACE_TOO_LARGE"
GRAPHIFY_WORKSPACE_MUTATING = "GRAPHIFY_WORKSPACE_MUTATING"
PROJECT_BUILD_ACTION = ".agent/workflow/scribe/scribe graph --project-build --timeout 180"
FINGERPRINT_ALGORITHM = "relative-path-size-content-sha256-v2"
DEFAULT_FINGERPRINT_WORKERS = 8
DEFAULT_MAX_FINGERPRINT_BYTES = 32 * 1024 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024

_STUB_MARKERS = (
    "smoke stub",
    "bootstrap placeholder",
    "no application graph has been built",
    "fixture-only graph",
)
_IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".next", ".venv", "node_modules",
    "vendor", "dist", "build", "coverage", "target", "__pycache__",
    "graphify-out", "scribe-out",
}
_INTERNAL_AGENT_DIRS = {".agent/state", ".agent/state/outputs", ".agent/state/runtime"}

_SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js",
    ".jsx", ".kt", ".kts", ".php", ".py", ".rb", ".rs", ".scala",
    ".swift", ".ts", ".tsx", ".vue", ".svelte", ".sql",
}
_MARKER_FILES = {
    "package.json", "pyproject.toml", "requirements.txt", "Cargo.toml", "go.mod",
    "composer.json", "pom.xml", "build.gradle", "build.gradle.kts",
}


@dataclass(frozen=True)
class Readiness:
    ok: bool
    verdict: str
    project_root: str
    output_dir: str
    reason: str
    next_action: str
    node_count: int = 0
    edge_count: int = 0
    manifest_kind: str = ""
    workspace_fingerprint: str = ""
    manifest_fingerprint: str = ""
    source_file_count: int = 0
    source_total_bytes: int = 0
    graph_epoch: int = 0
    fingerprint_algorithm: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GraphArtifacts:
    raw: dict[str, Any]
    nodes: list[Any]
    edges: list[Any]
    edge_field: str


@dataclass(frozen=True)
class SourceSnapshot:
    relative: str
    size: int
    mtime_ns: int
    device: int
    inode: int


class WorkspaceFingerprintError(RuntimeError):
    """Raised when a content snapshot cannot be proven stable and bounded."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_output_dir(project_root: Path | str) -> Path:
    return prepare_state_dirs(Path(project_root).resolve())["graphify_out"]


def manifest_path(project_root: Path | str) -> Path:
    return canonical_output_dir(project_root) / MANIFEST_FILENAME


def _atomic_replace(src: str, dst: Path) -> None:
    """Publish a sibling temp file atomically with a bounded Windows retry.

    On Windows, os.replace can raise PermissionError while the destination is
    read concurrently; we retry a bounded number of times with backoff and then
    re-raise. We never silently fall back to a non-atomic direct write.
    """
    last_exc: OSError | None = None
    for attempt in range(10):
        try:
            os.replace(src, str(dst))
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt < 9:
                time.sleep(min(0.25, 0.01 * (2 ** attempt)))
                continue
            raise
    if last_exc is not None:
        raise last_exc


def _write_json_atomic_unlocked(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_replace(temporary, path)
    finally:
        with suppress(OSError):
            os.unlink(temporary)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    lock_path = path.with_name(f".{path.name}.publish.lock")
    with owned_file_lock(
        lock_path,
        purpose=f"graphify-json-publish:{path.name}",
        timeout_seconds=30.0,
        stale_after_seconds=120.0,
    ):
        _write_json_atomic_unlocked(path, payload)


def _publish_manifest(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    lock_path = path.with_name(f".{path.name}.publish.lock")
    with owned_file_lock(
        lock_path,
        purpose=f"graphify-json-publish:{path.name}",
        timeout_seconds=30.0,
        stale_after_seconds=120.0,
    ):
        previous_epoch = 0
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(previous, dict):
                previous_epoch = max(0, int(previous.get("graph_epoch") or 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            previous_epoch = 0
        published = {
            **payload,
            "graph_epoch": max(time.time_ns(), previous_epoch + 1),
        }
        _write_json_atomic_unlocked(path, published)
        return published


def _iter_source_files(
    root: Path,
    max_files: int,
    max_bytes: int,
) -> tuple[list[SourceSnapshot], bool, int]:
    rows: list[SourceSnapshot] = []
    total_bytes = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            raise WorkspaceFingerprintError(
                f"cannot scan source directory {current}: {exc}"
            ) from exc
        for entry in entries:
            if entry.name in _IGNORED_DIRS:
                continue
            path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    relative_dir = path.relative_to(root).as_posix()
                    if any(
                        relative_dir == item or relative_dir.startswith(item + "/")
                        for item in _INTERNAL_AGENT_DIRS
                    ):
                        continue
                    stack.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                relative = path.relative_to(root).as_posix()
                if path.name not in _MARKER_FILES and path.suffix.lower() not in _SOURCE_SUFFIXES:
                    continue
                # DirEntry.stat() can expose zero-valued st_ino/st_dev fields
                # on Windows while os.stat() and fstat() expose the real file
                # index. Snapshot through the path so every later identity
                # comparison uses the same stat family on every platform.
                source_stat = os.stat(path, follow_symlinks=False)
                size = int(source_stat.st_size)
                total_bytes += size
                rows.append(
                    SourceSnapshot(
                        relative=relative,
                        size=size,
                        mtime_ns=int(source_stat.st_mtime_ns),
                        device=int(source_stat.st_dev),
                        inode=int(source_stat.st_ino),
                    )
                )
                if len(rows) > max_files or total_bytes > max_bytes:
                    return rows, True, total_bytes
            except ValueError as exc:
                raise WorkspaceFingerprintError(
                    f"source path escaped workspace: {path}"
                ) from exc
            except OSError as exc:
                raise WorkspaceFingerprintError(
                    f"cannot inspect source file {path}: {exc}"
                ) from exc
    rows.sort(key=lambda item: item.relative)
    return rows, False, total_bytes


def discover_sources(
    project_root: Path | str,
    *,
    required_resources: list[str] | None = None,
    max_files: int = 200_000,
    max_bytes: int = DEFAULT_MAX_FINGERPRINT_BYTES,
) -> dict[str, Any]:
    """Discover only supported sources under explicitly requested bounded roots."""
    root = Path(project_root).resolve()
    requested = sorted({str(item).strip().replace("\\", "/").strip("/") for item in required_resources or [] if str(item).strip()})
    indexed: list[str] = []
    excluded: list[dict[str, str]] = []
    candidates = 0
    total_bytes = 0
    for resource in requested:
        base = (root / resource).resolve()
        try:
            base.relative_to(root)
        except ValueError:
            excluded.append({"path": resource, "reason": "resource escapes project root"})
            continue
        if not base.exists():
            excluded.append({"path": resource, "reason": "resource missing"})
            continue
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                excluded.append({"path": relative, "reason": "symlink refused"})
                continue
            if not path.is_file():
                continue
            if any(relative == item or relative.startswith(item + "/") for item in _INTERNAL_AGENT_DIRS):
                excluded.append({"path": relative, "reason": "internal state excluded"})
                continue
            if path.name not in _MARKER_FILES and path.suffix.lower() not in _SOURCE_SUFFIXES:
                continue
            candidates += 1
            try:
                size = path.stat().st_size
            except OSError:
                excluded.append({"path": relative, "reason": "stat failed"})
                continue
            if len(indexed) >= max_files or total_bytes + size > max_bytes:
                excluded.append({"path": relative, "reason": "bounded discovery limit"})
                continue
            indexed.append(relative)
            total_bytes += size
    return {
        "project_root": str(root),
        "requested_resources": requested,
        "discovered_candidate_count": candidates,
        "indexed_source_count": len(indexed),
        "indexed_paths": indexed,
        "excluded_count": len(excluded),
        "excluded_paths": excluded,
        "source_total_bytes": total_bytes,
    }


def _stable_content_digest(
    root: Path,
    snapshot: SourceSnapshot,
) -> tuple[str, int, str]:
    path = root / snapshot.relative
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise WorkspaceFingerprintError(
            f"source changed before hashing: {snapshot.relative}: {exc}"
        ) from exc
    digest = hashlib.sha256()
    bytes_read = 0
    try:
        before = os.fstat(descriptor)
        identity = (
            int(before.st_dev),
            int(before.st_ino),
            int(before.st_size),
            int(before.st_mtime_ns),
        )
        expected = (
            snapshot.device,
            snapshot.inode,
            snapshot.size,
            snapshot.mtime_ns,
        )
        if not stat.S_ISREG(before.st_mode) or identity != expected:
            raise WorkspaceFingerprintError(
                f"source changed before hashing: {snapshot.relative}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while True:
                chunk = handle.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                bytes_read += len(chunk)
        after = os.fstat(descriptor)
        try:
            path_after = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise WorkspaceFingerprintError(
                f"source changed during hashing: {snapshot.relative}: {exc}"
            ) from exc
        after_identity = (
            int(after.st_dev),
            int(after.st_ino),
            int(after.st_size),
            int(after.st_mtime_ns),
        )
        path_identity = (
            int(path_after.st_dev),
            int(path_after.st_ino),
            int(path_after.st_size),
            int(path_after.st_mtime_ns),
        )
        if (
            after_identity != identity
            or path_identity != identity
            or bytes_read != snapshot.size
        ):
            raise WorkspaceFingerprintError(
                f"source changed during hashing: {snapshot.relative}"
            )
    finally:
        os.close(descriptor)
    return snapshot.relative, snapshot.size, digest.hexdigest()


def workspace_fingerprint(
    project_root: Path | str,
    *,
    max_files: int = 200_000,
    max_bytes: int = DEFAULT_MAX_FINGERPRINT_BYTES,
    max_workers: int = DEFAULT_FINGERPRINT_WORKERS,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    rows, truncated, total_bytes = _iter_source_files(
        root,
        max(1, int(max_files)),
        max(1, int(max_bytes)),
    )
    worker_count = max(1, min(int(max_workers), 32))
    digest = hashlib.sha256()
    digest.update(FINGERPRINT_ALGORITHM.encode("ascii"))
    digest.update(b"\n")
    if not truncated:
        batch_size = worker_count * 4
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="graphify-fingerprint",
        ) as executor:
            for offset in range(0, len(rows), batch_size):
                batch = rows[offset:offset + batch_size]
                for relative, size, content_digest in executor.map(
                    lambda item: _stable_content_digest(root, item),
                    batch,
                ):
                    digest.update(relative.encode("utf-8", errors="surrogatepass"))
                    digest.update(b"\0")
                    digest.update(str(size).encode("ascii"))
                    digest.update(b"\0")
                    digest.update(content_digest.encode("ascii"))
                    digest.update(b"\n")
    return {
        "fingerprint": f"sha256:{digest.hexdigest()}",
        "source_file_count": len(rows),
        "source_total_bytes": total_bytes,
        "algorithm": FINGERPRINT_ALGORITHM,
        "truncated": truncated,
    }


def _resolve_edge_collection(graph: dict[str, Any]) -> tuple[list[Any] | None, str, str]:
    present = {name: graph[name] for name in ("edges", "links") if name in graph}
    if not present:
        return None, "", "graph.json must contain a list field named edges or links"

    invalid = [name for name, value in present.items() if not isinstance(value, list)]
    if invalid:
        return None, "", f"graph.json field(s) {', '.join(invalid)} must be lists"

    if len(present) == 2:
        edges = present["edges"]
        links = present["links"]
        if edges != links:
            return None, "", "graph.json contains contradictory list fields edges and links"
        return edges, "edges+links", ""

    edge_field, edge_values = next(iter(present.items()))
    return edge_values, edge_field, ""


def _read_artifacts(root: Path) -> tuple[GraphArtifacts | None, str, list[str], str]:
    out = canonical_output_dir(root)
    missing: list[str] = []
    for name in REQUIRED_FILES:
        path = out / name
        if not path.is_file() or path.stat().st_size <= 0:
            missing.append(name)
    if missing:
        return None, "", missing, ""
    try:
        graph = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, "", [], str(exc)
    try:
        report = (out / "GRAPH_REPORT.md").read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, "", [], str(exc)
    if not isinstance(graph, dict):
        return None, report, [], "graph.json root must be an object"

    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return None, report, [], "graph.json field nodes must be a list"

    edges, edge_field, edge_error = _resolve_edge_collection(graph)
    if edges is None:
        return None, report, [], edge_error

    return GraphArtifacts(graph, nodes, edges, edge_field), report, [], ""


def inspect_graphify_readiness(
    project_root: Path | str,
    *,
    allow_fixture: bool | None = None,
    require_workspace_match: bool = True,
) -> Readiness:
    root = Path(project_root).resolve()
    out = canonical_output_dir(root)
    if allow_fixture is None:
        allow_fixture = os.environ.get(FIXTURE_ENV, "").strip() == "1"
    graph, report, missing, artifact_error = _read_artifacts(root)
    if missing:
        verdict = GRAPHIFY_MISSING if len(missing) == len(REQUIRED_FILES) else GRAPHIFY_OUTPUTS_INCOMPLETE
        return Readiness(False, verdict, str(root), str(out), f"missing or empty: {', '.join(missing)}", PROJECT_BUILD_ACTION)
    if graph is None:
        return Readiness(False, GRAPHIFY_CORRUPT, str(root), str(out), artifact_error or "invalid Graphify artifacts", PROJECT_BUILD_ACTION)

    node_count = len(graph.nodes)
    edge_count = len(graph.edges)
    marker = next((item for item in _STUB_MARKERS if item in report.lower()), "")
    manifest_file = out / MANIFEST_FILENAME
    if not manifest_file.is_file():
        verdict = GRAPHIFY_STUB_INVALID if marker else GRAPHIFY_LEGACY_UNBOUND
        reason = f"stub marker detected: {marker}" if marker else "legacy graph has no project-bound readiness manifest"
        return Readiness(False, verdict, str(root), str(out), reason, PROJECT_BUILD_ACTION, node_count, edge_count)
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Readiness(False, GRAPHIFY_MANIFEST_INVALID, str(root), str(out), str(exc), PROJECT_BUILD_ACTION, node_count, edge_count)
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        return Readiness(False, GRAPHIFY_MANIFEST_INVALID, str(root), str(out), "unsupported readiness manifest", PROJECT_BUILD_ACTION, node_count, edge_count)

    kind = str(manifest.get("kind") or "")
    bound_root = str(manifest.get("project_root") or "")
    if bound_root != str(root):
        return Readiness(False, GRAPHIFY_STALE_ROOT, str(root), str(out), f"graph is bound to {bound_root or '<empty>'}", PROJECT_BUILD_ACTION, node_count, edge_count, kind)
    if kind == "smoke_fixture" and not allow_fixture:
        return Readiness(False, GRAPHIFY_FIXTURE_FORBIDDEN, str(root), str(out), "test fixture graph is forbidden outside smoke mode", PROJECT_BUILD_ACTION, node_count, edge_count, kind)
    if marker and kind not in {"smoke_fixture", "empty_project"}:
        return Readiness(False, GRAPHIFY_STUB_INVALID, str(root), str(out), f"stub marker detected: {marker}", PROJECT_BUILD_ACTION, node_count, edge_count, kind)

    try:
        current = workspace_fingerprint(root)
    except WorkspaceFingerprintError as exc:
        return Readiness(
            False,
            GRAPHIFY_WORKSPACE_MUTATING,
            str(root),
            str(out),
            str(exc),
            PROJECT_BUILD_ACTION,
            node_count,
            edge_count,
            kind,
        )
    if current["truncated"]:
        return Readiness(
            False,
            GRAPHIFY_WORKSPACE_TOO_LARGE,
            str(root),
            str(out),
            "workspace fingerprint exceeded safety limit",
            "configure Graphify ignore rules and rebuild",
            node_count,
            edge_count,
            kind,
            current["fingerprint"],
            str(manifest.get("workspace_fingerprint") or ""),
            current["source_file_count"],
            current["source_total_bytes"],
            int(manifest.get("graph_epoch") or 0),
            str(manifest.get("fingerprint_algorithm") or ""),
        )
    recorded = str(manifest.get("workspace_fingerprint") or "")
    recorded_algorithm = str(manifest.get("fingerprint_algorithm") or "")
    try:
        graph_epoch = int(manifest.get("graph_epoch") or 0)
    except (TypeError, ValueError):
        graph_epoch = 0
    if recorded_algorithm != FINGERPRINT_ALGORITHM or graph_epoch <= 0:
        return Readiness(
            False,
            GRAPHIFY_MANIFEST_INVALID,
            str(root),
            str(out),
            "readiness manifest lacks the causal content fingerprint or graph epoch",
            PROJECT_BUILD_ACTION,
            node_count,
            edge_count,
            kind,
            current["fingerprint"],
            recorded,
            current["source_file_count"],
            current["source_total_bytes"],
            graph_epoch,
            recorded_algorithm,
        )
    if require_workspace_match and recorded != current["fingerprint"]:
        return Readiness(
            False,
            GRAPHIFY_STALE_WORKSPACE,
            str(root),
            str(out),
            "project sources changed since the graph was bound",
            PROJECT_BUILD_ACTION,
            node_count,
            edge_count,
            kind,
            current["fingerprint"],
            recorded,
            current["source_file_count"],
            current["source_total_bytes"],
            graph_epoch,
            recorded_algorithm,
        )

    if kind == "empty_project":
        if current["source_file_count"] != 0 or graph.nodes or graph.edges:
            return Readiness(False, GRAPHIFY_STALE_WORKSPACE, str(root), str(out), "empty-project placeholder no longer matches workspace", PROJECT_BUILD_ACTION, node_count, edge_count, kind, current["fingerprint"], recorded, current["source_file_count"])
        return Readiness(
            True,
            GRAPHIFY_EMPTY_PROJECT_READY,
            str(root),
            str(out),
            "empty project placeholder is bound and current",
            "",
            0,
            0,
            kind,
            current["fingerprint"],
            recorded,
            0,
            0,
            graph_epoch,
            recorded_algorithm,
        )
    if kind == "smoke_fixture":
        return Readiness(
            True,
            GRAPHIFY_TEST_FIXTURE_READY,
            str(root),
            str(out),
            "authorized test fixture",
            "",
            node_count,
            edge_count,
            kind,
            current["fingerprint"],
            recorded,
            current["source_file_count"],
            current["source_total_bytes"],
            graph_epoch,
            recorded_algorithm,
        )
    if kind != "real":
        return Readiness(False, GRAPHIFY_MANIFEST_INVALID, str(root), str(out), f"unsupported manifest kind: {kind}", PROJECT_BUILD_ACTION, node_count, edge_count, kind)
    if not graph.nodes:
        return Readiness(False, GRAPHIFY_STUB_INVALID, str(root), str(out), "real graph contains zero nodes", PROJECT_BUILD_ACTION, 0, edge_count, kind)
    return Readiness(
        True,
        GRAPHIFY_READY,
        str(root),
        str(out),
        "project-bound graph is valid and current",
        "",
        node_count,
        edge_count,
        kind,
        current["fingerprint"],
        recorded,
        current["source_file_count"],
        current["source_total_bytes"],
        graph_epoch,
        recorded_algorithm,
    )


def write_graphify_manifest(project_root: Path | str, *, kind: str = "real", purpose: str = "terrain") -> dict[str, Any]:
    if kind not in {"real", "empty_project", "smoke_fixture"}:
        raise ValueError(f"unsupported Graphify manifest kind: {kind}")
    root = Path(project_root).resolve()
    out = canonical_output_dir(root)
    graph, _report, missing, artifact_error = _read_artifacts(root)
    if missing or graph is None:
        return {
            "ok": False,
            "verdict": GRAPHIFY_OUTPUTS_INCOMPLETE if missing else GRAPHIFY_CORRUPT,
            "reason": f"missing or empty: {', '.join(missing)}" if missing else artifact_error,
        }
    try:
        current = workspace_fingerprint(root)
    except WorkspaceFingerprintError as exc:
        return {
            "ok": False,
            "verdict": GRAPHIFY_WORKSPACE_MUTATING,
            "reason": str(exc),
        }
    if current["truncated"]:
        return {"ok": False, "verdict": GRAPHIFY_WORKSPACE_TOO_LARGE, "reason": "workspace fingerprint exceeded safety limit"}
    payload = {
        "schema": MANIFEST_SCHEMA,
        "kind": kind,
        "purpose": purpose,
        "project_root": str(root),
        "workspace_fingerprint": current["fingerprint"],
        "fingerprint_algorithm": current["algorithm"],
        "source_file_count": current["source_file_count"],
        "source_total_bytes": current["source_total_bytes"],
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "edge_field": graph.edge_field,
        "created_at": _utc_now(),
    }
    published = _publish_manifest(out / MANIFEST_FILENAME, payload)
    return {
        "ok": True,
        "verdict": "GRAPHIFY_MANIFEST_WRITTEN",
        "manifest": published,
        "path": str(out / MANIFEST_FILENAME),
    }


def write_smoke_fixture(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    out = canonical_output_dir(root)
    out.mkdir(parents=True, exist_ok=True)
    (out / "graph.json").write_text('{"nodes":[],"edges":[]}\n', encoding="utf-8")
    (out / "GRAPH_REPORT.md").write_text("# Fixture-only Graph Report\n\nSmoke stub; never valid on terrain.\n", encoding="utf-8")
    (out / "graph.html").write_text("<html><body>fixture-only graph</body></html>\n", encoding="utf-8")
    (out / "cache" / "ast").mkdir(parents=True, exist_ok=True)
    return write_graphify_manifest(root, kind="smoke_fixture", purpose="mcp_smoke")


@contextmanager
def smoke_fixture_scope(project_root: Path | str) -> Iterator[dict[str, Any]]:
    """Install an authorized smoke fixture and restore the exact prior graph state.

    The scope owns both the fixture files and the environment authorization. It
    never leaves a smoke manifest behind and never destroys a pre-existing real
    graph, even when the smoke raises an exception.
    """

    root = Path(project_root).resolve()
    out = root / ".agent" / "state" / "outputs" / "graphify-out"
    if out.is_symlink():
        raise RuntimeError(f"refusing Graphify smoke fixture on symlinked output: {out}")

    previous_env = os.environ.get(FIXTURE_ENV)
    existed_before = out.exists()
    with tempfile.TemporaryDirectory(prefix="graphify-smoke-backup-") as temp_dir:
        backup = Path(temp_dir) / "graphify-out"
        if existed_before:
            shutil.copytree(out, backup, symlinks=True)
        try:
            if out.exists():
                shutil.rmtree(out)
            os.environ[FIXTURE_ENV] = "1"
            result = write_smoke_fixture(root)
            if not result.get("ok"):
                raise RuntimeError(f"cannot create Graphify smoke fixture: {result}")
            verified = inspect_graphify_readiness(root, allow_fixture=True)
            if not verified.ok or verified.verdict != GRAPHIFY_TEST_FIXTURE_READY:
                raise RuntimeError(f"Graphify smoke fixture failed readiness verification: {verified.to_dict()}")
            yield result
        finally:
            if previous_env is None:
                os.environ.pop(FIXTURE_ENV, None)
            else:
                os.environ[FIXTURE_ENV] = previous_env
            if out.exists():
                shutil.rmtree(out)
            if existed_before:
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(backup, out, symlinks=True)
