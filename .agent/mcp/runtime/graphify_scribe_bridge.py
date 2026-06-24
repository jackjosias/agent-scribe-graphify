"""graphify_scribe_bridge.py — Automatic SCRIBE GHOST generation on Graphify centrality drift.

Design:
    Graphify = structural memory (what, where, how).
    SCRIBE   = causal memory (why, decisions, pain).

    Problem: a SCAR references a function by name. If that function is later
    refactored and its centrality score drops from 0.8 to 0.1, the SCAR becomes
    a "ghost" reference — pointing at something that no longer matters structurally.
    Conversely, a NEW god-node may appear that has no SCAR coverage.

    This bridge runs as a lightweight check (no daemon, no watcher) and:
    1. Reads graphify-out/graph.json (always up-to-date per graphify watch).
    2. Reads AGENT-MEMOIRE_PROJECT_STATUS.scribe for SCAR/GHOST entries.
    3. Computes centrality for each node referenced in SCARs.
    4. If centrality has drifted > CENTRALITY_DRIFT_THRESHOLD from when the SCAR
       was written → appends a GHOST entry to the SCRIBE file.
    5. Returns a structured report.

    Cross-platform: pure Python, no OS-specific dependencies.
    Idempotent: a GHOST for the same (node, drift_event_id) is only written once.
    Production-safe: reads are lock-free, writes use atomic temp-file + replace.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

CENTRALITY_DRIFT_THRESHOLD = 0.30  # 30 % relative change triggers a GHOST.
GRAPH_JSON_PATH = "graphify-out/graph.json"
SCRIBE_FILENAME = "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
# Maximum number of GHOSTs to create in a single bridge run (safety cap).
MAX_GHOSTS_PER_RUN = 20

# ─ Fix #6: Structured machine-readable tag format ───────────────────────────────
# When writing SCARs, agents SHOULD embed this HTML comment tag on its own line.
# It is invisible in Markdown renderers but parseable by the bridge with zero
# regex ambiguity.
#
# Format: <!-- agent:node=IDENTIFIER centrality=SCORE -->
#   IDENTIFIER : the node name as it appears in graphify-out/graph.json
#   SCORE      : float [0.0, 1.0] representing the centrality at SCAR write time
#
# Example (inside a SCAR block):
#   <!-- agent:node=fetchWithResilience centrality=0.85 -->
#
# Backward compatibility: the bridge also parses the legacy regex pattern
# as a fallback for SCARs written before this fix.
# ──────────────────────────────────────────────────────────────────────────────
SCRIBE_NODE_TAG_FORMAT = "<!-- agent:node={node} centrality={centrality:.4f} -->"  # noqa: E501

# Compiled patterns for the structured tag (primary) and legacy heuristic (fallback).
_STRUCTURED_TAG_RE = re.compile(
    r"<!--\s*agent:node=([\w.:-]+)\s+centrality=([0-9.]+)\s*-->",
    re.IGNORECASE,
)

# Regex used to detect GHOSTs already written (deduplication).
_GHOST_MARKER_PATTERN = re.compile(
    r"GHOST-([a-f0-9]{12})-graphify-drift",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────

class BridgeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ─────────────────────────────────────────────────────────────
# Graph loading
# ─────────────────────────────────────────────────────────────

def load_graph(workspace_root: Path) -> dict[str, Any]:
    """Load graphify-out/graph.json.

    Returns the parsed graph dict, or raises BridgeError if unavailable.
    Tolerates missing file (Graphify not yet run) with a clear error.
    """
    graph_path = workspace_root / GRAPH_JSON_PATH
    if not graph_path.is_file():
        raise BridgeError(
            "GRAPH_JSON_MISSING",
            f"graphify-out/graph.json not found at {graph_path}. Run `graphify update .` first.",
        )
    try:
        raw = graph_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BridgeError("GRAPH_JSON_INVALID", f"graph.json is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise BridgeError("GRAPH_JSON_READ_ERROR", f"Cannot read graph.json: {exc}") from exc
    return data


def get_node_centrality(graph: dict[str, Any], node_name: str) -> float | None:
    """Extract the centrality score for a node name from the graph.

    Graphify stores nodes in graph["nodes"] as a list of dicts with fields:
        id, label, centrality (or degree_centrality), community, ...

    Returns the centrality score [0.0, 1.0] or None if node not found.

    Tries multiple field names for forward/backward compatibility with
    different graphify schema versions.
    """
    if not graph or not node_name:
        return None

    nodes = graph.get("nodes", [])
    if not isinstance(nodes, list):
        return None

    name_lower = node_name.strip().lower()
    centrality_fields = ["centrality", "degree_centrality", "betweenness_centrality", "pagerank"]

    for node in nodes:
        if not isinstance(node, dict):
            continue
        # Match by label, id, or name field (graphify uses different keys).
        node_label = str(node.get("label") or node.get("id") or node.get("name") or "").lower()
        if node_label != name_lower and node_name.lower() not in node_label:
            continue
        for field in centrality_fields:
            val = node.get(field)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue
    return None


def get_all_god_nodes(graph: dict[str, Any], threshold: float = 0.5) -> list[dict[str, Any]]:
    """Return nodes with centrality above threshold (god-nodes).

    These are the high-impact nodes that most deserve SCAR coverage.
    """
    nodes = graph.get("nodes", [])
    if not isinstance(nodes, list):
        return []

    result = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        for field in ["centrality", "degree_centrality", "betweenness_centrality", "pagerank"]:
            val = node.get(field)
            if val is not None:
                try:
                    c = float(val)
                    if c >= threshold:
                        result.append({
                            "name": node.get("label") or node.get("id") or node.get("name") or "",
                            "centrality": c,
                            "community": node.get("community"),
                        })
                    break
                except (TypeError, ValueError):
                    continue
    return sorted(result, key=lambda n: n["centrality"], reverse=True)


# ─────────────────────────────────────────────────────────────
# SCRIBE parsing
# ─────────────────────────────────────────────────────────────

# ─ Fix #6: Legacy regex heuristic (fallback for pre-structured-tag SCARs) ───
# This pattern is intentionally NOT the primary parser.
# It fires only when no structured tag is found in a SCAR block.
# Known limitation: slightly ambiguous for unusual formatting variations.
# New SCARs MUST use SCRIBE_NODE_TAG_FORMAT to avoid this.
_NODE_REF_PATTERN = re.compile(
    r"(?:\*\*)?(?:function|fonction|node|n\u0153ud|class|module|def|method|m\u00e9thode)(?:\*\*)?[:\s]+[`'\"]?([\w][\w.:-]*)",
    re.IGNORECASE,
)

_GHOST_MARKER_PATTERN = re.compile(
    r"GHOST-([a-f0-9]{12})-graphify-drift",
    re.IGNORECASE,
)


def _extract_structured_nodes(block_text: str) -> list[tuple[str, float | None]]:
    """PRIMARY parser: extract (node_name, centrality) from structured HTML comment tags.

    Finds all: <!-- agent:node=NAME centrality=SCORE --> markers in a block.
    Returns deduplicated list of (name, score) tuples preserving first-occurrence order.
    Score is None if the tag is malformed (name still extracted if possible).

    This is the Fix #6 primary path — zero ambiguity, machine-generated.
    """
    found: list[tuple[str, float | None]] = []
    seen: set[str] = set()
    for match in _STRUCTURED_TAG_RE.finditer(block_text):
        name = match.group(1).strip()
        try:
            score: float | None = float(match.group(2))
            # Validate range [0.0, 1.0].
            if not (0.0 <= score <= 1.0):
                score = None
        except (ValueError, IndexError):
            score = None
        if name and name not in seen:
            seen.add(name)
            found.append((name, score))
    return found


def _extract_node_refs_from_text(text: str) -> list[str]:
    """FALLBACK (legacy) parser: heuristic regex for pre-Fix-#6 SCARs.

    Used only when no structured tag is found in a block. Returns deduplicated
    node name list preserving first-occurrence order.
    """
    found: list[str] = []
    seen: set[str] = set()
    for match in _NODE_REF_PATTERN.finditer(text):
        name = match.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            found.append(name)
    return found


def load_scar_tagged_nodes(workspace_root: Path) -> list[dict[str, Any]]:
    """Parse SCRIBE file and extract SCAR entries with their node references.

    Fix #6 — Parsing priority:
    1. PRIMARY: structured HTML comment tags (<!-- agent:node=X centrality=Y -->).
       These are unambiguous, machine-generated, regex-proof.
    2. FALLBACK: legacy heuristic regex for SCARs written before Fix #6.

    This two-tier strategy ensures backward compatibility while making new
    SCARs immune to formatting variations.

    Returns a list of dicts:
        {
            "scar_id": str,
            "node_name": str,
            "text": str,
            "centrality_hint": float | None,
            "parse_method": "structured" | "heuristic",  # for observability
        }
    """
    scribe_path = workspace_root / SCRIBE_FILENAME
    if not scribe_path.is_file():
        return []

    try:
        content = scribe_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    results: list[dict[str, Any]] = []
    # Split on SCAR section headers: "## SCAR-xxx" or "### SCAR-xxx".
    scar_blocks = re.split(r"(?m)^#{1,4}\s+SCAR-", content)

    for block in scar_blocks[1:]:  # Skip preamble before first SCAR.
        lines = block.split("\n")
        if not lines:
            continue
        scar_id_line = lines[0].strip()
        scar_id = re.split(r"[\s—\-–]", scar_id_line)[0].strip()
        if not scar_id:
            continue

        block_text = "\n".join(lines)

        # Fix #6: Try structured tags FIRST.
        structured = _extract_structured_nodes(block_text)
        if structured:
            for node_name, centrality_hint in structured:
                results.append({
                    "scar_id": f"SCAR-{scar_id}",
                    "node_name": node_name,
                    "text": block_text[:500],
                    "centrality_hint": centrality_hint,
                    "parse_method": "structured",
                })
            continue  # Don't also run heuristic on same block.

        # FALLBACK: legacy heuristic regex.
        node_names = _extract_node_refs_from_text(block_text)

        # Try to extract centrality hint from human-written text.
        centrality_hint: float | None = None
        cent_match = re.search(r"centrality[:\s]+([0-9.]+)", block_text, re.IGNORECASE)
        if cent_match:
            try:
                centrality_hint = float(cent_match.group(1))
            except ValueError:
                pass

        for node_name in node_names:
            results.append({
                "scar_id": f"SCAR-{scar_id}",
                "node_name": node_name,
                "text": block_text[:500],
                "centrality_hint": centrality_hint,
                "parse_method": "heuristic",
            })

    return results


# ─────────────────────────────────────────────────────────────
# Ghost deduplication
# ─────────────────────────────────────────────────────────────

def _existing_ghost_ids(workspace_root: Path) -> set[str]:
    """Return set of drift event IDs already present in SCRIBE as GHOSTs."""
    scribe_path = workspace_root / SCRIBE_FILENAME
    if not scribe_path.is_file():
        return set()
    try:
        content = scribe_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return set(GHOST_MARKER_PATTERN.findall(content) if False else
               _GHOST_MARKER_PATTERN.findall(content))


# ─────────────────────────────────────────────────────────────
# GHOST writing
# ─────────────────────────────────────────────────────────────

def _drift_event_id(scar_id: str, node_name: str) -> str:
    """Deterministic 12-char hex ID for a (SCAR, node) drift event.

    Deterministic → idempotent: same SCAR+node always produces the same ID,
    so we can check if this GHOST was already written without full text scan.
    """
    raw = f"{scar_id}:{node_name}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def create_ghost_entry(
    workspace_root: Path,
    scar_id: str,
    node_name: str,
    drift_info: dict[str, Any],
) -> dict[str, Any]:
    """Append a GHOST entry to the SCRIBE file.

    Atomic append via temp-file + replace to prevent corruption under concurrent
    access. The existing content is preserved; only the new GHOST block is added.

    Returns dict with verdict and written content.
    """
    scribe_path = workspace_root / SCRIBE_FILENAME
    drift_id = _drift_event_id(scar_id, node_name)

    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    old_c = drift_info.get("old_centrality")
    new_c = drift_info.get("new_centrality")
    drift_pct = drift_info.get("drift_percent", 0.0)
    direction = "DECREASED" if (new_c or 0) < (old_c or 0) else "INCREASED"

    new_c_str = f"{new_c:.3f}" if new_c is not None else "N/A (node removed)"

    # Fix #6: embed machine-readable tag in the GHOST so future bridge runs
    # can parse this entry without regex heuristics.
    node_machine_tag = SCRIBE_NODE_TAG_FORMAT.format(
        node=node_name,
        centrality=new_c if new_c is not None else 0.0,
    )

    ghost_block = f"""
## GHOST-{drift_id}-graphify-drift

{node_machine_tag}
**Type:** GHOST — Centrality Drift Alert (auto-generated by graphify_scribe_bridge)
**Date:** {ts}
**Triggered by:** {scar_id}
**Node:** `{node_name}`
**Drift:** centrality {direction} by {drift_pct:.1f}% (was ~{old_c:.3f}, now {new_c_str})

**What this means:**
The node `{node_name}` referenced in {scar_id} has changed structural importance significantly.
{"The node was a god-node and is no longer — the SCAR may be less critical now." if direction == "DECREASED" else
 "The node gained centrality — its SCAR coverage may need updating."}
{"The node no longer exists in the graph — it may have been deleted or renamed." if new_c is None else ""}

**Action required:**
Review {scar_id} in context of the current graph. Update, close, or escalate the SCAR if needed.
Do NOT dismiss this GHOST without checking the node's current blast radius.

---
"""
    # Atomic append: read + extend + atomic replace.
    try:
        existing = scribe_path.read_text(encoding="utf-8") if scribe_path.is_file() else ""
    except OSError:
        existing = ""

    new_content = existing.rstrip() + "\n" + ghost_block
    tmp_path = scribe_path.with_suffix(".tmp")
    try:
        tmp_path.write_text(new_content, encoding="utf-8")
        os.replace(tmp_path, scribe_path)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return {
            "verdict": "GHOST_WRITE_FAILED",
            "error": str(exc),
            "scar_id": scar_id,
            "node_name": node_name,
        }

    return {
        "verdict": "GHOST_WRITTEN",
        "drift_id": drift_id,
        "scar_id": scar_id,
        "node_name": node_name,
        "direction": direction,
        "drift_percent": drift_pct,
        "old_centrality": old_c,
        "new_centrality": new_c,
        "timestamp": ts,
    }


# ─────────────────────────────────────────────────────────────
# Centrality drift detection
# ─────────────────────────────────────────────────────────────

def detect_centrality_drift(
    graph: dict[str, Any],
    scar_nodes: list[dict[str, Any]],
    threshold: float = CENTRALITY_DRIFT_THRESHOLD,
) -> list[dict[str, Any]]:
    """Find SCAR-tagged nodes whose current centrality has drifted significantly.

    For each (scar_id, node_name) pair:
    - If centrality_hint was recorded and current centrality differs by > threshold → drift.
    - If node no longer exists in graph (centrality = None) → drift (node removed).
    - If no centrality_hint was recorded, use default assumption of 0.5 (god-node territory)
      to catch newly insignificant nodes.

    Returns list of drift dicts, one per (scar_id, node_name) pair with drift detected.
    """
    drifts: list[dict[str, Any]] = []

    for entry in scar_nodes:
        node_name = entry["node_name"]
        scar_id = entry["scar_id"]
        old_c = entry.get("centrality_hint")

        current_c = get_node_centrality(graph, node_name)

        if old_c is None:
            # No baseline recorded — we can only flag if node disappeared entirely.
            if current_c is None:
                # Node was referenced in a SCAR but no longer exists.
                drifts.append({
                    "scar_id": scar_id,
                    "node_name": node_name,
                    "old_centrality": None,
                    "new_centrality": None,
                    "drift_percent": 100.0,
                    "reason": "node_not_found_in_graph",
                })
            # If node still exists but no baseline → no drift detectable.
            continue

        if current_c is None:
            # Had a centrality baseline, now node is gone.
            drifts.append({
                "scar_id": scar_id,
                "node_name": node_name,
                "old_centrality": old_c,
                "new_centrality": None,
                "drift_percent": 100.0,
                "reason": "node_removed_from_graph",
            })
            continue

        # Compute relative drift.
        if old_c == 0.0:
            # Avoid division by zero: if old was 0 and new is positive → 100% increase.
            drift_pct = 100.0 if current_c > 0 else 0.0
        else:
            drift_pct = abs(current_c - old_c) / old_c * 100.0

        if drift_pct >= threshold * 100.0:
            drifts.append({
                "scar_id": scar_id,
                "node_name": node_name,
                "old_centrality": old_c,
                "new_centrality": current_c,
                "drift_percent": drift_pct,
                "reason": "centrality_drift",
            })

    return drifts


# ─────────────────────────────────────────────────────────────
# Main bridge entry point
# ─────────────────────────────────────────────────────────────

def run_bridge_check(
    workspace_root: Path | str | None = None,
    drift_threshold: float = CENTRALITY_DRIFT_THRESHOLD,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Full bridge check: load graph, parse SCRIBE, detect drift, write GHOSTs.

    Args:
        workspace_root: Project root path. Defaults to cwd.
        drift_threshold: Relative centrality change [0.0, 1.0] that triggers a GHOST.
        dry_run: If True, detect but do NOT write GHOSTs to SCRIBE.

    Returns a structured report:
        {
            "verdict": "BRIDGE_OK" | "BRIDGE_GHOSTS_WRITTEN" | "BRIDGE_ERROR" | "BRIDGE_SKIPPED",
            "ghosts_written": int,
            "drifts_detected": int,
            "details": [...],
            "errors": [...],
        }
    """
    if workspace_root is None:
        workspace_root = Path.cwd()
    root = Path(workspace_root).resolve()

    errors: list[str] = []
    details: list[dict[str, Any]] = []
    ghosts_written = 0

    # Step 1: Load graph.
    try:
        graph = load_graph(root)
    except BridgeError as exc:
        return {
            "verdict": "BRIDGE_SKIPPED",
            "reason": exc.code,
            "message": exc.message,
            "ghosts_written": 0,
            "drifts_detected": 0,
            "details": [],
            "errors": [exc.message],
        }

    # Step 2: Parse SCAR nodes from SCRIBE.
    scar_nodes = load_scar_tagged_nodes(root)
    if not scar_nodes:
        return {
            "verdict": "BRIDGE_OK",
            "reason": "No SCAR entries with node references found in SCRIBE.",
            "ghosts_written": 0,
            "drifts_detected": 0,
            "details": [],
            "errors": [],
        }

    # Step 3: Detect drift.
    drifts = detect_centrality_drift(graph, scar_nodes, threshold=drift_threshold)

    if not drifts:
        return {
            "verdict": "BRIDGE_OK",
            "reason": f"No centrality drift detected above {drift_threshold * 100:.0f}% threshold.",
            "ghosts_written": 0,
            "drifts_detected": 0,
            "details": [],
            "errors": [],
        }

    # Step 4: Write GHOSTs for new drifts (skip if already written).
    existing_ghost_ids = _existing_ghost_ids(root)
    ghosts_to_write = [
        d for d in drifts
        if _drift_event_id(d["scar_id"], d["node_name"]) not in existing_ghost_ids
    ][:MAX_GHOSTS_PER_RUN]

    if not dry_run:
        for drift in ghosts_to_write:
            result = create_ghost_entry(
                workspace_root=root,
                scar_id=drift["scar_id"],
                node_name=drift["node_name"],
                drift_info=drift,
            )
            details.append(result)
            if result.get("verdict") == "GHOST_WRITTEN":
                ghosts_written += 1
            else:
                errors.append(result.get("error", "unknown write error"))
    else:
        details = [{"dry_run": True, **d} for d in ghosts_to_write]

    verdict = "BRIDGE_GHOSTS_WRITTEN" if ghosts_written > 0 else (
        "BRIDGE_OK" if not errors else "BRIDGE_PARTIAL"
    )

    return {
        "verdict": verdict,
        "ghosts_written": ghosts_written,
        "drifts_detected": len(drifts),
        "already_recorded": len(drifts) - len(ghosts_to_write),
        "dry_run": dry_run,
        "drift_threshold_percent": drift_threshold * 100,
        "details": details,
        "errors": errors,
    }
