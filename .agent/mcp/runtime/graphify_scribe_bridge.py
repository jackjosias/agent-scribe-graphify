"""Bridge structural Graphify drift into causal SCRIBE memory."""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:
    from . import db, graphify_readiness
    from .state_paths import prepare_state_dirs
except ImportError:
    import db  # type: ignore
    import graphify_readiness  # type: ignore
    from state_paths import prepare_state_dirs  # type: ignore

CENTRALITY_DRIFT_THRESHOLD = 0.30
GRAPH_JSON_PATH = "graphify-out/graph.json"
SCRIBE_FILENAME = "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
MAX_GHOSTS_PER_RUN = 20
SCRIBE_NODE_TAG_FORMAT = "<!-- agent:node={node} centrality={centrality:.4f} -->"
_STRUCTURED_TAG_RE = re.compile(r"<!--\s*agent:node=([\w.:-]+)\s+centrality=([0-9.]+)\s*-->", re.IGNORECASE)
_GHOST_MARKER_PATTERN = re.compile(r"GHOST-([a-f0-9]{12})-graphify-drift", re.IGNORECASE)
_NODE_REF_PATTERN = re.compile(r"(?:\*\*)?(?:function|fonction|node|nœud|class|module|def|method|méthode)(?:\*\*)?[:\s]+[`'\"]?([\w][\w.:-]*)", re.IGNORECASE)


class BridgeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _graph_path(workspace_root: Path) -> Path:
    canonical = prepare_state_dirs(workspace_root)["graphify_out"] / "graph.json"
    legacy = workspace_root / GRAPH_JSON_PATH
    return canonical if canonical.is_file() else legacy


def load_graph(workspace_root: Path) -> dict[str, Any]:
    root = workspace_root.resolve()
    if (root / ".agent").is_dir():
        ready = graphify_readiness.inspect_graphify_readiness(root)
        if not ready.ok:
            raise BridgeError(ready.verdict, ready.reason)
    graph_path = _graph_path(root)
    if not graph_path.is_file():
        raise BridgeError(
            "GRAPH_JSON_MISSING",
            f"graphify graph.json not found at {graph_path}. Run `{graphify_readiness.PROJECT_BUILD_ACTION}` first.",
        )
    try:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BridgeError("GRAPH_JSON_INVALID", f"graph.json is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise BridgeError("GRAPH_JSON_READ_ERROR", f"Cannot read graph.json: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list) or not isinstance(data.get("edges", []), list):
        raise BridgeError("GRAPH_JSON_INVALID", "graph.json must contain list fields nodes and edges")
    return data


def get_node_centrality(graph: dict[str, Any], node_name: str) -> float | None:
    if not graph or not node_name or not isinstance(graph.get("nodes"), list):
        return None
    wanted = node_name.strip().lower()
    fields = ("centrality", "degree_centrality", "betweenness_centrality", "pagerank")
    for node in graph["nodes"]:
        if not isinstance(node, dict):
            continue
        label = str(node.get("label") or node.get("id") or node.get("name") or "").lower()
        if label != wanted and wanted not in label:
            continue
        for field in fields:
            try:
                if node.get(field) is not None:
                    return float(node[field])
            except (TypeError, ValueError):
                continue
    return None


def get_all_god_nodes(graph: dict[str, Any], threshold: float = 0.5) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in graph.get("nodes", []) if isinstance(graph, dict) else []:
        if not isinstance(node, dict):
            continue
        name = node.get("label") or node.get("id") or node.get("name") or ""
        value = get_node_centrality({"nodes": [node]}, str(name))
        if value is not None and value >= threshold:
            result.append({"name": name, "centrality": value, "community": node.get("community")})
    return sorted(result, key=lambda item: item["centrality"], reverse=True)


def _extract_structured_nodes(block_text: str) -> list[tuple[str, float | None]]:
    found: list[tuple[str, float | None]] = []
    seen: set[str] = set()
    for match in _STRUCTURED_TAG_RE.finditer(block_text):
        name = match.group(1).strip()
        try:
            score: float | None = float(match.group(2))
            if not 0.0 <= score <= 1.0:
                score = None
        except (ValueError, IndexError):
            score = None
        if name and name not in seen:
            seen.add(name)
            found.append((name, score))
    return found


def _extract_node_refs_from_text(text: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for match in _NODE_REF_PATTERN.finditer(text):
        name = match.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def load_scar_tagged_nodes(workspace_root: Path) -> list[dict[str, Any]]:
    scribe_path = workspace_root / SCRIBE_FILENAME
    if not scribe_path.is_file():
        return []
    try:
        content = scribe_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    results: list[dict[str, Any]] = []
    for block in re.split(r"(?m)^#{1,4}\s+SCAR-", content)[1:]:
        lines = block.splitlines()
        if not lines:
            continue
        suffix = re.split(r"[\s—\-–]", lines[0].strip())[0].strip()
        if not suffix:
            continue
        scar_id = f"SCAR-{suffix}"
        block_text = "\n".join(lines)
        structured = _extract_structured_nodes(block_text)
        if structured:
            for node_name, centrality in structured:
                results.append({"scar_id": scar_id, "node_name": node_name, "text": block_text[:500], "centrality_hint": centrality, "parse_method": "structured"})
            continue
        centrality_hint: float | None = None
        match = re.search(r"centrality[:*\s]+([0-9.]+)", block_text, re.IGNORECASE)
        if match:
            try:
                centrality_hint = float(match.group(1))
            except ValueError:
                centrality_hint = None
        for node_name in _extract_node_refs_from_text(block_text):
            results.append({"scar_id": scar_id, "node_name": node_name, "text": block_text[:500], "centrality_hint": centrality_hint, "parse_method": "heuristic"})
    return results


def _existing_ghost_ids(workspace_root: Path) -> set[str]:
    path = workspace_root / SCRIBE_FILENAME
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return set(_GHOST_MARKER_PATTERN.findall(content))


def _drift_event_id(scar_id: str, node_name: str) -> str:
    return hashlib.sha256(f"{scar_id}:{node_name}".encode("utf-8")).hexdigest()[:12]


def _pid_alive(pid: object) -> bool:
    return db.process_is_alive(pid)


@contextmanager
def _scribe_bridge_lock(scribe_path: Path, timeout: float = 5.0) -> Iterator[None]:
    lock = scribe_path.with_name(f".{scribe_path.name}.graphify-bridge.lock")
    deadline = time.monotonic() + timeout
    token = f"{socket.gethostname()}:{os.getpid()}:{time.time_ns()}"
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"token": token, "pid": os.getpid(), "hostname": socket.gethostname(), "created": time.time()}, handle)
            break
        except FileExistsError:
            try:
                payload = json.loads(lock.read_text(encoding="utf-8"))
                age = time.time() - float(payload.get("created", 0))
            except (OSError, ValueError, json.JSONDecodeError):
                payload, age = {}, float("inf")
            same_host = not payload.get("hostname") or payload.get("hostname") == socket.gethostname()
            if age > 60 and same_host and not _pid_alive(payload.get("pid")):
                try:
                    lock.unlink()
                    continue
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                raise BridgeError("SCRIBE_BRIDGE_BUSY", f"SCRIBE bridge lock busy: {lock}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            current = json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            current = {}
        if current.get("token") == token:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass


def create_ghost_entry(workspace_root: Path, scar_id: str, node_name: str, drift_info: dict[str, Any]) -> dict[str, Any]:
    scribe_path = workspace_root / SCRIBE_FILENAME
    drift_id = _drift_event_id(scar_id, node_name)
    old_c = drift_info.get("old_centrality")
    new_c = drift_info.get("new_centrality")
    drift_pct = float(drift_info.get("drift_percent", 0.0))
    direction = "DECREASED" if (new_c or 0) < (old_c or 0) else "INCREASED"
    old_text = f"{float(old_c):.3f}" if old_c is not None else "unknown"
    new_text = f"{float(new_c):.3f}" if new_c is not None else "removed"
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tag = SCRIBE_NODE_TAG_FORMAT.format(node=node_name, centrality=float(new_c or 0.0))
    block = f"""
## GHOST-{drift_id}-graphify-drift

{tag}
**Type:** GHOST — Centrality Drift Alert
**Date:** {timestamp}
**Triggered by:** {scar_id}
**Node:** `{node_name}`
**Drift:** {direction} by {drift_pct:.1f}% (was {old_text}, now {new_text})
**Action required:** Re-evaluate {scar_id} against the current blast radius before changing this node.

---
"""
    try:
        with _scribe_bridge_lock(scribe_path):
            existing = scribe_path.read_text(encoding="utf-8") if scribe_path.is_file() else ""
            if drift_id in _GHOST_MARKER_PATTERN.findall(existing):
                return {"verdict": "GHOST_ALREADY_RECORDED", "drift_id": drift_id, "scar_id": scar_id, "node_name": node_name}
            temporary = scribe_path.with_name(f".{scribe_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
            try:
                temporary.write_text(existing.rstrip() + "\n" + block, encoding="utf-8")
                os.replace(temporary, scribe_path)
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
    except (OSError, BridgeError) as exc:
        return {"verdict": "GHOST_WRITE_FAILED", "error": str(exc), "scar_id": scar_id, "node_name": node_name}
    return {"verdict": "GHOST_WRITTEN", "drift_id": drift_id, "scar_id": scar_id, "node_name": node_name, "direction": direction, "drift_percent": drift_pct, "old_centrality": old_c, "new_centrality": new_c, "timestamp": timestamp}


def detect_centrality_drift(graph: dict[str, Any], scar_nodes: list[dict[str, Any]], threshold: float = CENTRALITY_DRIFT_THRESHOLD) -> list[dict[str, Any]]:
    drifts: list[dict[str, Any]] = []
    for entry in scar_nodes:
        node_name = str(entry.get("node_name") or "")
        scar_id = str(entry.get("scar_id") or "")
        old_c = entry.get("centrality_hint")
        current = get_node_centrality(graph, node_name)
        if old_c is None:
            if current is None:
                drifts.append({"scar_id": scar_id, "node_name": node_name, "old_centrality": None, "new_centrality": None, "drift_percent": 100.0, "reason": "node_not_found_in_graph"})
            continue
        old_value = float(old_c)
        if current is None:
            drifts.append({"scar_id": scar_id, "node_name": node_name, "old_centrality": old_value, "new_centrality": None, "drift_percent": 100.0, "reason": "node_removed_from_graph"})
            continue
        denominator = max(abs(old_value), 1e-9)
        ratio = abs(current - old_value) / denominator
        if ratio > threshold:
            drifts.append({"scar_id": scar_id, "node_name": node_name, "old_centrality": old_value, "new_centrality": current, "drift_percent": ratio * 100.0, "reason": "centrality_drift"})
    return drifts


def run_bridge_check(workspace_root: str | Path | None = None, threshold: float = CENTRALITY_DRIFT_THRESHOLD) -> dict[str, Any]:
    root = Path(workspace_root or Path.cwd()).resolve()
    try:
        graph = load_graph(root)
    except BridgeError as exc:
        return {"ok": False, "verdict": exc.code, "reason": exc.message, "ghosts_written": 0, "drifts_detected": 0}
    scars = load_scar_tagged_nodes(root)
    drifts = detect_centrality_drift(graph, scars, threshold=threshold)
    existing = _existing_ghost_ids(root)
    writes: list[dict[str, Any]] = []
    for drift in drifts:
        if len(writes) >= MAX_GHOSTS_PER_RUN:
            break
        event_id = _drift_event_id(drift["scar_id"], drift["node_name"])
        if event_id in existing:
            continue
        result = create_ghost_entry(root, drift["scar_id"], drift["node_name"], drift)
        if result.get("verdict") == "GHOST_WRITTEN":
            writes.append(result)
            existing.add(event_id)
    return {
        "ok": True,
        "verdict": "GRAPHIFY_SCRIBE_BRIDGE_OK",
        "drifts_detected": len(drifts),
        "ghosts_written": len(writes),
        "ghosts": writes,
        "god_nodes": get_all_god_nodes(graph),
    }
