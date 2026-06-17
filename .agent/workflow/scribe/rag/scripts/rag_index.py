from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag_interface import DEFAULT_SCRIBE, RAG_INDEX_PATH, export_scribe_index, source_snapshot
from rag_embeddings import MODEL_NAME, available as embeddings_available, encode_texts
from rag_text import expand_tokens, tokenize

INDEX_VERSION = 2
ACTIVE_STATUSES = {"ACTIVE", ""}


def build_search_text(entity: dict[str, Any]) -> str:
    value = entity.get("value") if isinstance(entity.get("value"), dict) else {}
    parts = [
        entity.get("id"), entity.get("collection"), entity.get("title"), entity.get("abstract"), entity.get("tier"), entity.get("status"),
        value.get("l0_abstract"), value.get("l2_details"), value.get("pourquoi"), value.get("virus"), value.get("antidote"),
        value.get("contexte"), value.get("scope"), value.get("plan_remboursement"), value.get("titre"),
    ]
    for field in ("ne_pas_reproposer", "alternatives_rejetees", "liens_causaux", "evidence"):
        parts.append(json.dumps(value.get(field), ensure_ascii=False, sort_keys=True) if value.get(field) is not None else "")
    parts.extend(entity.get("incoming") or [])
    parts.extend(entity.get("outgoing") or [])
    return " ".join(str(part) for part in parts if part)


def entity_scope(entity: dict[str, Any]) -> str:
    value = entity.get("value") if isinstance(entity.get("value"), dict) else {}
    raw = value.get("scope") or value.get("contexte") or "universal"
    scope = str(raw).lower()
    if any(word in scope for word in ("auth", "runtime", "backend", "frontend", "websocket", "dev", "bundle", "security")):
        for candidate in ("auth", "runtime", "backend", "frontend", "websocket", "dev", "bundle", "security"):
            if candidate in scope:
                return candidate
    return "universal"


def evidence_type(entity: dict[str, Any]) -> str:
    value = entity.get("value") if isinstance(entity.get("value"), dict) else {}
    evidence = value.get("evidence")
    if isinstance(evidence, dict) and evidence.get("type"):
        return str(evidence["type"]).upper()
    return ""


def causal_count(entity: dict[str, Any]) -> int:
    return len(entity.get("incoming") or []) + len(entity.get("outgoing") or [])


def normalize_entity(entity: dict[str, Any], position: int) -> dict[str, Any]:
    text = build_search_text(entity)
    tokens = sorted(expand_tokens(tokenize(text)))
    value = entity.get("value") if isinstance(entity.get("value"), dict) else {}
    return {
        "id": entity.get("id"),
        "collection": entity.get("collection"),
        "tier": entity.get("tier") or "",
        "status": entity.get("status") or "",
        "title": entity.get("title") or value.get("titre") or "",
        "abstract": entity.get("abstract") or value.get("l0_abstract") or "",
        "date": value.get("date") or value.get("schema_patch_date") or value.get("date_creation") or "",
        "scope": entity_scope(entity),
        "evidence_type": evidence_type(entity),
        "causal_links_count": causal_count(entity),
        "incoming": entity.get("incoming") or [],
        "outgoing": entity.get("outgoing") or [],
        "search_text": text,
        "tokens": tokens,
        "position": position,
        "is_invariant": str(entity.get("collection")) == "invariants" or str(entity.get("id") or "").startswith("INV-"),
        "approved": bool(value.get("approved")),
        "value": value,
    }


def negative_terms_from_ghost(entity: dict[str, Any]) -> list[str]:
    value = entity.get("value") if isinstance(entity.get("value"), dict) else {}
    terms: list[str] = []
    raw_terms = value.get("ne_pas_reproposer")
    if isinstance(raw_terms, list):
        terms.extend(str(item) for item in raw_terms if str(item).strip())
    alternatives = value.get("alternatives_rejetees")
    if isinstance(alternatives, list):
        for item in alternatives:
            if isinstance(item, dict) and item.get("nom"):
                terms.append(str(item["nom"]))
    return terms


def build_negative_memory_index(entities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for entity in entities:
        if entity.get("collection") != "ghosts":
            continue
        ghost_id = str(entity.get("id") or "")
        scope = entity_scope(entity)
        for term in negative_terms_from_ghost(entity):
            tokens = sorted(expand_tokens(tokenize(term)))
            index[term] = {
                "ghost_id": ghost_id,
                "ne_pas_reproposer": True,
                "scope": scope,
                "tokens": tokens,
            }
    return index


def build_index(index_path: Path = RAG_INDEX_PATH, scribe_path: Path = DEFAULT_SCRIBE, *, force: bool = False, with_embeddings: bool = False) -> dict[str, Any]:
    snapshot = source_snapshot(scribe_path)
    requested_mode = "hybrid" if with_embeddings else "bm25"
    if not force:
        current = read_index(index_path)
        if is_fresh(current, snapshot, requested_mode):
            return current
    export = export_scribe_index(include_values=True)
    raw_entities = [item for item in export.get("entities", []) if isinstance(item, dict) and item.get("id")]
    entities = [normalize_entity(entity, position) for position, entity in enumerate(raw_entities)]
    embedding_error = ""
    embedding_dimension = 0
    mode = requested_mode
    if with_embeddings:
        if not embeddings_available():
            mode = "bm25"
            embedding_error = "sentence-transformers unavailable; rebuilt BM25 fallback"
        else:
            vectors = encode_texts(entity_embedding_text(entity) for entity in entities)
            embedding_dimension = len(vectors[0]) if vectors else 0
            for entity, vector in zip(entities, vectors):
                entity["embedding"] = vector
    payload = {
        "version": INDEX_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": snapshot["path"],
        "source_sha256": snapshot["sha256"],
        "source_mtime_ns": snapshot["mtime_ns"],
        "source_line_count": snapshot["line_count"],
        "mode": mode,
        "embedding_model": MODEL_NAME if mode == "hybrid" else "",
        "embedding_dimension": embedding_dimension,
        "embedding_error": embedding_error,
        "entities": entities,
        "negative_memory_index": build_negative_memory_index(raw_entities),
        "stats": {
            "entities": len(entities),
            "negative_terms": len(build_negative_memory_index(raw_entities)),
            "embedding_dimension": embedding_dimension,
            "doctor_errors": export.get("summary", {}).get("doctor_errors", 0),
            "doctor_warnings": export.get("summary", {}).get("doctor_warnings", 0),
        },
    }
    write_index(index_path, payload)
    return payload


def read_index(index_path: Path = RAG_INDEX_PATH) -> dict[str, Any] | None:
    if not index_path.exists():
        return None
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def entity_embedding_text(entity: dict[str, Any]) -> str:
    return " ".join(
        str(part)
        for part in (entity.get("id"), entity.get("collection"), entity.get("title"), entity.get("abstract"), entity.get("scope"), entity.get("search_text"))
        if part
    )


def is_fresh(index: dict[str, Any] | None, snapshot: dict[str, Any], requested_mode: str) -> bool:
    return bool(
        index
        and index.get("version") == INDEX_VERSION
        and index.get("source_sha256") == snapshot["sha256"]
        and index.get("mode") == requested_mode
    )


def requested_index_mode(with_embeddings: bool | None) -> str:
    return "hybrid" if with_embeddings else "bm25"


def write_index(index_path: Path, payload: dict[str, Any]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = index_path.with_name(f"{index_path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(index_path)
    finally:
        if tmp.exists():
            tmp.unlink()
