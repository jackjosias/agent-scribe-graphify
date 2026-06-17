from __future__ import annotations

from typing import Any

from rag_scoring import retrieve
from rag_text import compact


def context(index: dict[str, Any], *, module: str | None = None) -> str:
    entities = [item for item in index.get("entities", []) if isinstance(item, dict)]
    focus = module or "production auth runtime"
    ranked = retrieve(focus, index, top_k=8, mode="query", context=module or "production")
    active_debts = [item for item in entities if item.get("collection") in {"debts", "dettes"} and str(item.get("status") or "").upper() == "ACTIVE"]
    lines = [
        "SCRIBE-RAG CONTEXT",
        f"mode     : {index.get('mode')} · entities: {index.get('stats', {}).get('entities')} · negative_terms: {index.get('stats', {}).get('negative_terms')}",
        f"source   : {index.get('source')}",
        "focus    : " + (module or "global"),
        "hot/relevant:",
    ]
    for result in ranked[:8]:
        entity = result.entity
        lines.append(f"- {entity.get('id')} [{entity.get('tier') or '-'}] {compact(entity.get('abstract') or entity.get('title') or '', 92)}")
    lines.append("active_debts:")
    if active_debts:
        for debt in active_debts[:5]:
            lines.append(f"- {debt.get('id')} {compact(debt.get('abstract') or '', 92)}")
    else:
        lines.append("- none")
    return "\n".join(lines[:35])
