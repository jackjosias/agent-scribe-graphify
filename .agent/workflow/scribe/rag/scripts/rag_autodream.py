from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from rag_autodream_io import (
    AutoDreamBudget,
    AutoDreamCancelled,
    AutoDreamConfig,
    BudgetExceeded,
    ReadOnlyGuard,
    ReadOnlyViolation,
    check_cancel,
    install_cancel_handlers,
    parse_status_paths,
    read_text_limited,
    run_git,
)
from rag_context import context as format_context
from rag_index import read_index
from rag_interface import PROJECT_ROOT, source_snapshot
from scribe_output_paths import scribe_out_dir
from rag_scoring import retrieve
from rag_text import compact


AUTODREAM_DOC_PATHS = (
    "AGENTS.md",
    ".agent/rules/scribe.md",
    ".agent/workflow/scribe/README.md",
    ".agent/workflow/scribe/rag/README.md",
    ".agent/workflow/scribe/sel/docs/AGENTS.md",
    ".agent/workflow/scribe/sel/docs/friction-policy.md",
    ".agent/workflow/scribe/sel/docs/scribe.md",
)


def collect_diff_digest(config: AutoDreamConfig, budget: AutoDreamBudget) -> dict[str, Any]:
    code, status_text, status_err = run_git(config.project_root, ["status", "--short", "--untracked-files=normal"], budget)
    if code != 0:
        return {"available": False, "error": compact(status_err or status_text, 180), "files": [], "surfaces": {}}
    rows = parse_status_paths(status_text)
    for row in rows:
        budget.charge_file(row["path"])
    patch_text = collect_patch_text(config, budget)
    surfaces: dict[str, int] = {}
    for row in rows:
        surfaces[row["surface"]] = surfaces.get(row["surface"], 0) + 1
    return {
        "available": True,
        "files": rows[: config.max_files],
        "file_count": len(rows),
        "surfaces": dict(sorted(surfaces.items())),
        "patch_bytes": len(patch_text.encode("utf-8")),
        "patch_truncated": budget.truncated,
        "signals": diff_signals(rows, patch_text),
    }


def collect_patch_text(config: AutoDreamConfig, budget: AutoDreamBudget) -> str:
    chunks: list[str] = []
    commands = (
        ["diff", "--stat", "--summary", "--find-renames"],
        ["diff", "--cached", "--stat", "--summary", "--find-renames"],
    )
    for args in commands:
        code, stdout, stderr = run_git(config.project_root, args, budget)
        chunks.append(stderr if code else stdout)
        current = "\n".join(chunks).encode("utf-8")
        if len(current) > config.max_diff_bytes:
            budget.truncated = True
            return current[: config.max_diff_bytes].decode("utf-8", errors="replace")
    return "\n".join(chunks)


def diff_signals(rows: list[dict[str, str]], patch_text: str) -> list[str]:
    signals: list[str] = []
    surfaces = {row["surface"] for row in rows}
    if "runtime_state" in surfaces or "graph_state" in surfaces:
        signals.append("generated-runtime-state-present")
    if "scribe_memory" in surfaces:
        signals.append("scribe-memory-changed")
    if "agent_rag" in surfaces:
        signals.append("scribe-rag-surface-changed")
    lowered = patch_text.lower()
    if "autodream" in lowered and "read-only" not in lowered and "lecture seule" not in lowered:
        signals.append("autodream-without-readonly-language")
    if "todo" in lowered or "placeholder" in lowered:
        signals.append("incomplete-marker-present")
    return signals


def index_status(config: AutoDreamConfig) -> dict[str, Any]:
    index = read_index(config.index_path)
    if not index:
        return {"available": False, "fresh": False, "mode": "", "entities": 0, "warning": "rag index absent"}
    snapshot = source_snapshot(config.scribe_path)
    return {
        "available": True,
        "fresh": index.get("source_sha256") == snapshot["sha256"],
        "mode": index.get("mode") or "",
        "entities": index.get("stats", {}).get("entities") or 0,
        "source_sha256": index.get("source_sha256") or "",
        "scribe_sha256": snapshot["sha256"],
    }


def retrieve_context(config: AutoDreamConfig, budget: AutoDreamBudget) -> dict[str, Any]:
    check_cancel(config, budget)
    index = read_index(config.index_path)
    if not index:
        return {"available": False, "summary": "RAG index absent; rebuild outside AutoDream if needed.", "memories": []}
    query = "AutoDream read-only post implementation constraints forget context contradictions"
    memories = []
    for result in retrieve(query, index, top_k=6, mode="query", context="dev bundle"):
        entity = result.entity
        memories.append({"id": entity.get("id"), "tier": entity.get("tier") or "", "abstract": compact(entity.get("abstract") or "", 120)})
    return {"available": True, "summary": format_context(index, module="dev bundle"), "memories": memories}


def collect_coordination_state(config: AutoDreamConfig, budget: AutoDreamBudget) -> dict[str, Any]:
    root = config.project_root
    scribe_out = scribe_out_dir(root)
    state_text, _ = read_text_limited(scribe_out / "state.json", budget, limit=80_000)
    lock_path = scribe_out / "locks" / "scribe.lock"
    claims_dir = scribe_out / "coordination" / "claims"
    active_claims = collect_active_claim_names(claims_dir, budget)
    return {"state_available": bool(state_text), "lock_present": lock_path.exists(), "active_claims": active_claims[:20], "active_claim_count": len(active_claims)}


def collect_active_claim_names(claims_dir: Path, budget: AutoDreamBudget) -> list[str]:
    if not claims_dir.exists():
        return []
    active: list[str] = []
    for claim_path in sorted(claims_dir.glob("*.json")):
        text, _ = read_text_limited(claim_path, budget, limit=20_000)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            active.append(claim_path.name)
            continue
        status = str(payload.get("status") or "active").lower()
        if status not in {"done", "finished", "cancelled", "expired"}:
            active.append(claim_path.name)
    return active


def detect_contradictions(config: AutoDreamConfig, budget: AutoDreamBudget, diff_digest: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for rel_path in AUTODREAM_DOC_PATHS:
        text, truncated = read_text_limited(config.project_root / rel_path, budget, limit=160_000)
        lowered = text.lower()
        if not text or "autodream" not in lowered:
            continue
        if "read-only" not in lowered and "lecture seule" not in lowered:
            findings.append({"severity": "HIGH", "path": rel_path, "message": "AutoDream is mentioned without read-only boundary."})
        daemon_forbidden = "must not" in lowered or "run daemons" in lowered or "background daemons" in lowered
        explicit_not_daemon = "not an automatic idle daemon" in lowered or "aucun daemon" in lowered
        if "daemon" in lowered and not daemon_forbidden and not explicit_not_daemon:
            findings.append({"severity": "MEDIUM", "path": rel_path, "message": "AutoDream daemon language needs explicit non-automatic boundary."})
        if truncated:
            findings.append({"severity": "LOW", "path": rel_path, "message": "Document scan truncated by read budget."})
    if "autodream-without-readonly-language" in diff_digest.get("signals", []):
        findings.append({"severity": "HIGH", "path": "git diff", "message": "Diff mentions AutoDream without read-only wording."})
    return findings


def candidate_memories(diff_digest: dict[str, Any], contradictions: list[dict[str, str]], max_candidates: int) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    surfaces = diff_digest.get("surfaces", {})
    if surfaces.get("agent_rag"):
        candidates.append(memory_candidate("PAT", "AutoDream runner stays read-only by construction", "scribe-rag AutoDream code changed; preserve anti-write guard as a reusable pattern."))
    if surfaces.get("scribe_memory"):
        candidates.append(memory_candidate("JOURNAL", "AutoDream reviewed a memory mutation", "SCRIBE memory changed in the current diff; capture why only after human validation."))
    if contradictions:
        candidates.append(memory_candidate("SCAR", "AutoDream detected workflow contradiction", "Contradiction findings exist; bind a test if confirmed."))
    if surfaces.get("source") or surfaces.get("components"):
        why = "Product code changed; record only causal decisions future agents cannot infer from Graphify."
        candidates.append(memory_candidate("JOURNAL", "Product implementation follow-up context", why))
    if not candidates:
        candidates.append(memory_candidate("JOURNAL", "AutoDream clean read-only review", "No memory write is required unless the user validates a causal lesson."))
    return candidates[:max_candidates]


def memory_candidate(kind: str, title: str, why: str) -> dict[str, str]:
    return {"kind": kind, "title": title, "why": why, "write_requires": "separate user approval + workflow ack/check + doctor + lock + sync + validation"}


def context_cleanup(diff_digest: dict[str, Any], index: dict[str, Any], coordination: dict[str, Any]) -> list[str]:
    items: list[str] = []
    if not index.get("fresh"):
        items.append("RAG index is stale or absent; rebuild outside AutoDream before relying on retrieval scores.")
    active_claim_count = coordination.get("active_claim_count")
    if active_claim_count:
        items.append(f"{active_claim_count} active coordination claim(s) exist; avoid overlapping writes.")
    if diff_digest.get("patch_truncated"):
        items.append("Diff digest was truncated by budget; rerun with a larger --max-diff-bytes for exhaustive review.")
    if "generated-runtime-state-present" in diff_digest.get("signals", []):
        items.append("Generated runtime state appears in the worktree; keep it out of commits unless explicitly shared.")
    return items or ["No stale context cleanup required from the inspected signals."]


def run_autodream(config: AutoDreamConfig) -> dict[str, Any]:
    install_cancel_handlers()
    budget = AutoDreamBudget(config.max_files, config.max_diff_bytes, config.max_read_bytes, config.timeout_ms, config.max_output_lines, config.max_candidates)
    guard = ReadOnlyGuard(config.project_root, config.protected_paths, budget)
    report = base_report(config)
    try:
        if not config.read_only:
            raise ReadOnlyViolation("AutoDream requires explicit --read-only")
        guard.snapshot_before()
        check_cancel(config, budget)
        diff_digest = collect_diff_digest(config, budget)
        index = index_status(config)
        coordination = collect_coordination_state(config, budget)
        context = retrieve_context(config, budget)
        contradictions = detect_contradictions(config, budget, diff_digest)
        report.update(build_report(diff_digest, index, coordination, context, contradictions, config))
        report["read_only_proof"] = guard.verify_after()
        report["status"] = "OK"
        report["exit_code"] = 0
    except AutoDreamCancelled as exc:
        report.update({"status": "CANCELLED", "exit_code": 130, "error": str(exc)})
    except BudgetExceeded as exc:
        report.update({"status": "BUDGET_EXCEEDED", "exit_code": 3, "error": str(exc)})
    except ReadOnlyViolation as exc:
        report.update({"status": "READ_ONLY_VIOLATION", "exit_code": 4, "error": str(exc)})
    finally:
        report["budget"] = budget_summary(budget)
    return report


def base_report(config: AutoDreamConfig) -> dict[str, Any]:
    return {"command": "scribe-rag autodream", "schema_version": 1, "status": "STARTED", "exit_code": 1, "read_only": config.read_only, "project_root": str(config.project_root)}


def build_report(diff_digest: dict[str, Any], index: dict[str, Any], coordination: dict[str, Any], context: dict[str, Any], contradictions: list[dict[str, str]], config: AutoDreamConfig) -> dict[str, Any]:
    return {
        "diff_digest": diff_digest,
        "index": index,
        "coordination": coordination,
        "retrieval_context": context,
        "contradictions": contradictions,
        "context_cleanup": context_cleanup(diff_digest, index, coordination),
        "candidate_memories": candidate_memories(diff_digest, contradictions, config.max_candidates),
        "next_actions": next_actions(contradictions),
    }


def next_actions(contradictions: list[dict[str, str]]) -> list[str]:
    if contradictions:
        return ["Resolve HIGH/MEDIUM contradictions before writing new SCRIBE memory.", "Ask the user before applying any candidate memory."]
    return ["Use this report as review input only.", "Write memory only if the user validates a candidate in a separate guarded task."]


def budget_summary(budget: AutoDreamBudget) -> dict[str, Any]:
    elapsed_ms = int((time.monotonic() - budget.started_at) * 1000)
    return {
        "elapsed_ms": elapsed_ms,
        "timeout_ms": budget.timeout_ms,
        "files_seen": budget.files_seen,
        "max_files": budget.max_files,
        "bytes_read": budget.bytes_read,
        "max_read_bytes": budget.max_read_bytes,
        "truncated": budget.truncated,
        "max_output_lines": budget.max_output_lines,
    }
