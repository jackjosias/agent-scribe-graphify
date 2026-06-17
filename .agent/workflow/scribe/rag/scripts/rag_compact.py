from __future__ import annotations

from typing import Any

from rag_scoring import RagResult
from rag_text import compact


def reason_summary(result: RagResult) -> str:
    parts = []
    for key in ("bm25", "negative", "failure", "causal", "tier"):
        value = result.reasons.get(key, 0.0)
        if value >= 0.5:
            parts.append(f"{key}={value:.2f}")
    if result.negative_matches:
        parts.append("neg=" + compact(str(result.negative_matches[0].get("term") or ""), 34))
    return " · ".join(parts) if parts else "match faible"


def entity_label(entity: dict[str, Any]) -> str:
    return f"{entity.get('id')} [{entity.get('collection')}/{entity.get('tier') or '-'}]"


def format_results(title: str, results: list[RagResult], *, limit_lines: int = 15) -> str:
    lines = [title]
    for result in results:
        entity = result.entity
        lines.append(f"[{result.score:.2f}] {entity_label(entity)} — {compact(entity.get('title') or entity.get('abstract') or '', 66)}")
        lines.append(f"  {compact(entity.get('abstract') or '', 82)} | {reason_summary(result)}")
    lines.append(f"{len(results)} résultat(s) · scribe-rag explain <ID> · --verbose pour L2")
    if len(lines) > limit_lines:
        return "\n".join(lines[: limit_lines - 1] + [lines[-1]])
    return "\n".join(lines)


def format_explain(entity: dict[str, Any], *, verbose: bool = False) -> str:
    lines = [
        f"{entity_label(entity)} — {compact(entity.get('title') or '', 80)}",
        f"l0       : {compact(entity.get('abstract') or '', 110)}",
        f"scope    : {entity.get('scope')} · evidence: {entity.get('evidence_type') or '-'} · links: {entity.get('causal_links_count')}",
    ]
    value = entity.get("value") if isinstance(entity.get("value"), dict) else {}
    for field in ("virus", "antidote", "pourquoi"):
        if value.get(field):
            lines.append(f"{field[:8]:8}: {compact(str(value[field]), 110)}")
    if verbose and value.get("l2_details"):
        lines.append(f"l2       : {compact(str(value['l2_details']), 140)}")
    return "\n".join(lines[:10])
