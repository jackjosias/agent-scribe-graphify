from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .state_paths import prepare_state_dirs
except ImportError:
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

_STUB_MARKERS = (
    "smoke stub",
    "bootstrap placeholder",
    "no application graph has been built",
    "fixture-only graph",
)
_IGNORED_DIRS = {
    ".agent", ".git", ".hg", ".svn", ".next", ".venv", "node_modules",
    "vendor", "dist", "build", "coverage", "target", "__pycache__",
    "graphify-out", "scribe-out",
}
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_output_dir(project_root: Path | str) -> Path:
    return prepare_state_dirs(Path(project_root).resolve())["graphify_out"]


def manifest_path(project_root: Path | str) -> Path:
    return canonical_output_dir(project_root) / MANIFEST_FILENAME


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


def _iter_source_files(root: Path, max_files: int) -> tuple[list[tuple[str, int, int]], bool]:
    rows: list[tuple[str, int, int]] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if entry.name in _IGNORED_DIRS:
                continue
            path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                relative = path.relative_to(root).as_posix()
                if path.name not in _MARKER_FILES and path.suffix.lower() not in _SOURCE_SUFFIXES:
                    continue
                stat = entry.stat(follow_symlinks=False)
                rows.append((relative, int(stat.st_size), int(stat.st_mtime_ns)))
                if len(rows) > max_files:
                    return rows, True
            except (OSError, ValueError):
                continue
    rows.sort(key=lambda item: item[0].casefold())
    return rows, False


def workspace_fingerprint(project_root: Path | str, *, max_files: int = 200_000) -> dict[str, Any]:
    root = Path(project_root).resolve()
    rows, truncated = _iter_source_files(root, max_files)
    digest = hashlib.sha256()
    digest.update(str(root).encode("utf-8", errors="surrogatepass"))
    digest.update(b"\0")
    for relative, size, mtime_ns in rows:
        digest.update(relative.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(mtime_ns).encode("ascii"))
        digest.update(b"\n")
    return {
        "fingerprint": f"sha256:{digest.hexdigest()}",
        "source_file_count": len(rows),
        "truncated": truncated,
    }


def _read_artifacts(root: Path) -> tuple[dict[str, Any] | None, str, list[str], str]:
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
    if not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        return None, report, [], "graph.json must contain list fields nodes and edges"
    return graph, report, [], ""


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
        return Readiness(False, verdict, str(root), str(out), f"missing or empty: {', '.join(missing)}", "graphify update .")
    if graph is None:
        return Readiness(False, GRAPHIFY_CORRUPT, str(root), str(out), artifact_error or "invalid Graphify artifacts", "graphify update .")

    marker = next((item for item in _STUB_MARKERS if item in report.lower()), "")
    manifest_file = out / MANIFEST_FILENAME
    if not manifest_file.is_file():
        verdict = GRAPHIFY_STUB_INVALID if marker else GRAPHIFY_LEGACY_UNBOUND
        reason = f"stub marker detected: {marker}" if marker else "legacy graph has no project-bound readiness manifest"
        return Readiness(False, verdict, str(root), str(out), reason, "graphify update .", len(graph["nodes"]), len(graph["edges"]))
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Readiness(False, GRAPHIFY_MANIFEST_INVALID, str(root), str(out), str(exc), "graphify update .", len(graph["nodes"]), len(graph["edges"]))
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        return Readiness(False, GRAPHIFY_MANIFEST_INVALID, str(root), str(out), "unsupported readiness manifest", "graphify update .", len(graph["nodes"]), len(graph["edges"]))

    kind = str(manifest.get("kind") or "")
    bound_root = str(manifest.get("project_root") or "")
    if bound_root != str(root):
        return Readiness(False, GRAPHIFY_STALE_ROOT, str(root), str(out), f"graph is bound to {bound_root or '<empty>'}", "graphify update .", len(graph["nodes"]), len(graph["edges"]), kind)
    if kind == "smoke_fixture" and not allow_fixture:
        return Readiness(False, GRAPHIFY_FIXTURE_FORBIDDEN, str(root), str(out), "test fixture graph is forbidden outside smoke mode", "graphify update .", len(graph["nodes"]), len(graph["edges"]), kind)
    if marker and kind not in {"smoke_fixture", "empty_project"}:
        return Readiness(False, GRAPHIFY_STUB_INVALID, str(root), str(out), f"stub marker detected: {marker}", "graphify update .", len(graph["nodes"]), len(graph["edges"]), kind)

    current = workspace_fingerprint(root)
    if current["truncated"]:
        return Readiness(False, GRAPHIFY_WORKSPACE_TOO_LARGE, str(root), str(out), "workspace fingerprint exceeded safety limit", "configure Graphify ignore rules and rebuild", len(graph["nodes"]), len(graph["edges"]), kind, current["fingerprint"], str(manifest.get("workspace_fingerprint") or ""), current["source_file_count"])
    recorded = str(manifest.get("workspace_fingerprint") or "")
    if require_workspace_match and recorded != current["fingerprint"]:
        return Readiness(False, GRAPHIFY_STALE_WORKSPACE, str(root), str(out), "project sources changed since the graph was bound", "graphify update .", len(graph["nodes"]), len(graph["edges"]), kind, current["fingerprint"], recorded, current["source_file_count"])

    if kind == "empty_project":
        if current["source_file_count"] != 0 or graph["nodes"] or graph["edges"]:
            return Readiness(False, GRAPHIFY_STALE_WORKSPACE, str(root), str(out), "empty-project placeholder no longer matches workspace", "graphify update .", len(graph["nodes"]), len(graph["edges"]), kind, current["fingerprint"], recorded, current["source_file_count"])
        return Readiness(True, GRAPHIFY_EMPTY_PROJECT_READY, str(root), str(out), "empty project placeholder is bound and current", "", 0, 0, kind, current["fingerprint"], recorded, 0)
    if kind == "smoke_fixture":
        return Readiness(True, GRAPHIFY_TEST_FIXTURE_READY, str(root), str(out), "authorized test fixture", "", len(graph["nodes"]), len(graph["edges"]), kind, current["fingerprint"], recorded, current["source_file_count"])
    if kind != "real":
        return Readiness(False, GRAPHIFY_MANIFEST_INVALID, str(root), str(out), f"unsupported manifest kind: {kind}", "graphify update .", len(graph["nodes"]), len(graph["edges"]), kind)
    if not graph["nodes"]:
        return Readiness(False, GRAPHIFY_STUB_INVALID, str(root), str(out), "real graph contains zero nodes", "graphify update .", 0, len(graph["edges"]), kind)
    return Readiness(True, GRAPHIFY_READY, str(root), str(out), "project-bound graph is valid and current", "", len(graph["nodes"]), len(graph["edges"]), kind, current["fingerprint"], recorded, current["source_file_count"])


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
    current = workspace_fingerprint(root)
    if current["truncated"]:
        return {"ok": False, "verdict": GRAPHIFY_WORKSPACE_TOO_LARGE, "reason": "workspace fingerprint exceeded safety limit"}
    payload = {
        "schema": MANIFEST_SCHEMA,
        "kind": kind,
        "purpose": purpose,
        "project_root": str(root),
        "workspace_fingerprint": current["fingerprint"],
        "source_file_count": current["source_file_count"],
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "created_at": _utc_now(),
    }
    _atomic_json(out / MANIFEST_FILENAME, payload)
    return {"ok": True, "verdict": "GRAPHIFY_MANIFEST_WRITTEN", "manifest": payload, "path": str(out / MANIFEST_FILENAME)}


def write_smoke_fixture(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    out = canonical_output_dir(root)
    out.mkdir(parents=True, exist_ok=True)
    (out / "graph.json").write_text('{"nodes":[],"edges":[]}\n', encoding="utf-8")
    (out / "GRAPH_REPORT.md").write_text("# Fixture-only Graph Report\n\nSmoke stub; never valid on terrain.\n", encoding="utf-8")
    (out / "graph.html").write_text("<html><body>fixture-only graph</body></html>\n", encoding="utf-8")
    (out / "cache" / "ast").mkdir(parents=True, exist_ok=True)
    return write_graphify_manifest(root, kind="smoke_fixture", purpose="mcp_smoke")
