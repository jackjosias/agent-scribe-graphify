from __future__ import annotations

import json
from typing import Any

from rag_text import compact


def format_autodream_report(report: dict[str, Any]) -> str:
    lines = ["SCRIBE-RAG AUTODREAM", f"status   : {report.get('status')}", f"read_only: {report.get('read_only')}"]
    if report.get("error"):
        lines.append(f"error    : {report['error']}")
    lines.extend(format_budget(report.get("budget", {})))
    lines.extend(format_diff(report.get("diff_digest", {})))
    lines.extend(format_index(report.get("index", {})))
    lines.extend(format_findings("contradictions", report.get("contradictions", [])))
    lines.extend(format_list("context_cleanup", report.get("context_cleanup", [])))
    lines.extend(format_candidates(report.get("candidate_memories", [])))
    proof = report.get("read_only_proof")
    if proof:
        unchanged = proof.get("unchanged")
        protected = proof.get("protected_paths")
        lines.append(f"read_only_proof: unchanged={unchanged} protected_paths={protected}")
    lines.extend(format_list("next_actions", report.get("next_actions", [])))
    max_lines = int(report.get("budget", {}).get("max_output_lines") or 180)
    return "\n".join(lines[:max_lines])


def format_budget(budget: dict[str, Any]) -> list[str]:
    elapsed = budget.get("elapsed_ms", 0)
    timeout = budget.get("timeout_ms", 0)
    files_seen = budget.get("files_seen", 0)
    max_files = budget.get("max_files", 0)
    bytes_read = budget.get("bytes_read", 0)
    max_read_bytes = budget.get("max_read_bytes", 0)
    return [
        f"budget   : {elapsed}ms/{timeout}ms · files {files_seen}/{max_files} · bytes {bytes_read}/{max_read_bytes}",
        f"truncated: {budget.get('truncated', False)}",
    ]


def format_diff(diff: dict[str, Any]) -> list[str]:
    if not diff:
        return ["diff     : unavailable"]
    lines = [f"diff     : files={diff.get('file_count', 0)} patch_bytes={diff.get('patch_bytes', 0)}"]
    surfaces = diff.get("surfaces") or {}
    surface_text = ", ".join(f"{name}:{count}" for name, count in surfaces.items()) or "none"
    lines.append(f"surfaces : {surface_text}")
    signals = ", ".join(diff.get("signals") or []) or "none"
    lines.append(f"signals  : {signals}")
    return lines


def format_index(index: dict[str, Any]) -> list[str]:
    if not index:
        return ["index    : unavailable"]
    available = index.get("available")
    fresh = index.get("fresh")
    mode = index.get("mode")
    entities = index.get("entities")
    return [f"index    : available={available} fresh={fresh} mode={mode} entities={entities}"]


def format_findings(title: str, findings: list[dict[str, str]]) -> list[str]:
    lines = [f"{title}:"]
    if not findings:
        return lines + ["- none"]
    for finding in findings[:8]:
        severity = finding.get("severity")
        path = finding.get("path")
        message = finding.get("message")
        lines.append(f"- {severity} {path}: {message}")
    return lines


def format_list(title: str, items: list[str]) -> list[str]:
    lines = [f"{title}:"]
    if not items:
        return lines + ["- none"]
    return lines + [f"- {item}" for item in items[:10]]


def format_candidates(candidates: list[dict[str, str]]) -> list[str]:
    lines = ["candidate_memories:"]
    if not candidates:
        return lines + ["- none"]
    for candidate in candidates[:8]:
        kind = candidate.get("kind")
        title = candidate.get("title")
        why = compact(candidate.get("why") or "", 96)
        lines.append(f"- {kind} {title} — {why}")
    return lines


def format_autodream_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
