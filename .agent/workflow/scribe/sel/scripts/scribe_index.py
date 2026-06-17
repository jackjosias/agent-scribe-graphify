from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from scribe_doctor_model import Entity


INDEX_VERSION = 3
RECOMMENDATION_POLICY_VERSION = 2
DEFAULT_INDEX_NAME = "scribe-index.json"
SYNONYMS = {
    "app": {"application"},
    "application": {"app"},
    "graph": {"graphe", "graphify", "structure", "structural"},
    "graphify": {"graph", "graphe"},
    "bundle": {"tooling", "adapter", "portable"},
    "clean": {"cleanup", "nettoyage", "noise", "bruit"},
    "query": {"search", "recherche", "retrieval", "rag"},
    "sync": {"state", "repair", "stale", "writer"},
}


@dataclass(frozen=True)
class QuickIndex:
    path: Path
    payload: dict[str, Any]
    rebuilt: bool


def index_path_for_scribe(scribe_path: Path) -> Path:
    return scribe_path.resolve().parent / "scribe-out" / DEFAULT_INDEX_NAME


def ensure_quick_index(scribe_path: Path, index_path: Path | None = None) -> QuickIndex:
    target = index_path or index_path_for_scribe(scribe_path)
    source = source_snapshot(scribe_path)
    current = read_index(target)
    if index_is_fresh(current, source["sha256"]):
        return QuickIndex(target, current, rebuilt=False)

    from scribe_store import load_scribe

    store = load_scribe(scribe_path)
    payload = build_quick_index_payload(store, source)
    write_index(target, payload)
    return QuickIndex(target, payload, rebuilt=True)


def source_snapshot(scribe_path: Path) -> dict[str, Any]:
    raw = scribe_path.read_bytes()
    stat = scribe_path.stat()
    return {
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "mtime_ns": stat.st_mtime_ns,
        "line_count": len(raw.decode("utf-8").splitlines()),
        "raw_text": raw.decode("utf-8"),
    }


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def tokenize(text: str) -> set[str]:
    normalized = normalize_text(text)
    tokens: set[str] = set()
    for raw in re.findall(r"[a-z0-9_./-]{3,}", normalized):
        token = raw.strip("./,;:!?()[]{}")
        if len(token) >= 3:
            tokens.add(token)
    return tokens


def expand_tokens(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for token in list(tokens):
        expanded.add(token_root(token))
        expanded.update(SYNONYMS.get(token, set()))
        for key, values in SYNONYMS.items():
            if token in values:
                expanded.add(key)
    return expanded


def token_root(token: str) -> str:
    for suffix in ("ements", "ement", "ations", "ation", "iques", "ique", "ites", "ite", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def read_index(index_path: Path) -> dict[str, Any] | None:
    if not index_path.exists():
        return None
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def index_is_fresh(payload: dict[str, Any] | None, source_sha256: str) -> bool:
    if not payload:
        return False
    return (
        payload.get("version") == INDEX_VERSION
        and payload.get("recommendation_policy_version") == RECOMMENDATION_POLICY_VERSION
        and payload.get("source_sha256") == source_sha256
    )


def build_quick_index_payload(store, source: dict[str, Any]) -> dict[str, Any]:
    from scribe_doctor_checks import check_all
    from scribe_memory_eval import SURFACES, default_eval_cases, evaluate_store
    from scribe_store import edge_type_counts

    offsets = entity_line_offsets(source["raw_text"])
    findings = store.findings[:]
    if store.data:
        findings.extend(check_all(store.data, store.raw, store.entities, store.registry, store.path))
    edge_counts = edge_type_counts(store.index.edge_types)
    hot_entities = store.hot_entities()
    debts = [
        entity
        for entity in store.entities
        if entity.collection in {"debts", "dettes"} and str(entity.value.get("status", "")).upper() == "ACTIVE"
    ]
    indexed_entities = [index_entity(store, entity, offsets, position) for position, entity in enumerate(store.entities)]
    tiers = normalized_tiers(store)
    stale_warm_patterns = stale_warm_patterns_without_causal_source(store.entities, session_total(store))
    payload = {
        "version": INDEX_VERSION,
        "recommendation_policy_version": RECOMMENDATION_POLICY_VERSION,
        "complete": True,
        "source": str(store.path),
        "schema_version": store.data.get("schema_version"),
        "source_sha256": source["sha256"],
        "source_mtime_ns": source["mtime_ns"],
        "source_line_count": source["line_count"],
        "summary": {
            "entities": len(store.entities),
            "ids": len(store.index.id_index),
            "doctor_errors": sum(1 for item in findings if item.severity == "ERROR"),
            "doctor_warnings": sum(1 for item in findings if item.severity == "WARNING"),
            "causal_edges": edge_counts["causal"],
            "edges": {"total": sum(edge_counts.values()), **edge_counts},
            "index_version": INDEX_VERSION,
            "index_complete": True,
            "source_line_count": source["line_count"],
            "warm_patterns_without_causal_source": len(warm_patterns_without_causal_source(store.entities)),
            "stale_warm_patterns_without_causal_source": len(stale_warm_patterns),
        },
        "tiers": tiers,
        "collections": dict(sorted(Counter(entity.collection for entity in store.entities).items())),
        "statuses": dict(sorted(Counter(str(entity.value.get("status", entity.value.get("statut", "-")) or "-") for entity in store.entities).items())),
        "hot_entities": [index_entity(store, entity, offsets, position) for position, entity in enumerate(hot_entities)],
        "active_debts": [index_entity(store, entity, offsets, position) for position, entity in enumerate(debts)],
        "entities": indexed_entities,
        "id_to_offset": offsets,
        "doctor_findings": [finding_payload(item) for item in findings],
        "retrieval_quality": evaluate_store(store, default_eval_cases(store), SURFACES, top_k=5),
    }
    from scribe_recommendations import build_recommendations

    payload["recommendations"] = build_recommendations(payload)
    return payload


def entity_line_offsets(raw: str) -> dict[str, dict[str, int]]:
    offsets: dict[str, dict[str, int]] = {}
    char_offset = 0
    pattern = re.compile(r"^\s*-\s+id:\s*[\"']?([^\"'\s]+)")
    for line_number, line in enumerate(raw.splitlines(keepends=True), start=1):
        match = pattern.match(line)
        if match:
            offsets[match.group(1)] = {"line": line_number, "char": char_offset}
        char_offset += len(line)
    return offsets


def index_entity(store, entity: "Entity", offsets: dict[str, dict[str, int]], position: int) -> dict[str, Any]:
    from scribe_store import entity_abstract, entity_title, related_search_text

    entity_id = entity.id or entity.path
    links = sorted(set().union(*[edge_map.get(entity_id, set()) for edge_map in store.index.edge_types.values()]))
    outgoing = sorted(store.index.causal_edges.get(entity_id, set()))
    incoming = sorted(store.index.reverse_edges.get(entity_id, set()))
    related_text = related_search_text(entity, store.index.id_index, store.index.causal_edges, store.index.reverse_edges)
    return {
        "id": entity_id,
        "collection": entity.collection,
        "path": entity.path,
        "position": position,
        "tier": entity.value.get("tier"),
        "status": entity.value.get("status", entity.value.get("statut")),
        "title": entity_title(entity),
        "abstract": entity_abstract(entity),
        "date": first_string(entity.value, ("date", "schema_patch_date", "date_creation")),
        "source_number": source_number(entity.value),
        "links": links,
        "outgoing": outgoing,
        "incoming": incoming,
        "offset": offsets.get(entity_id),
        "search_text": indexed_search_text(entity, related_text),
    }


def indexed_search_text(entity: "Entity", related_text: str) -> str:
    from scribe_store import entity_abstract, entity_title

    parts = [
        entity.id or "",
        entity.collection,
        entity_title(entity),
        entity_abstract(entity),
        related_text,
    ]
    for field in (
        "pourquoi",
        "virus",
        "antidote",
        "contexte",
        "l2_details",
        "plan_remboursement",
        "scope",
    ):
        value = entity.value.get(field)
        if isinstance(value, str):
            parts.append(value)
    return " ".join(part for part in parts if part)


def normalized_tiers(store) -> dict[str, list[str]]:
    tiers = store.data.get("tiers", {}) if isinstance(store.data.get("tiers"), dict) else {}
    return {tier: flatten_strings(tiers.get(tier)) for tier in ("hot", "warm", "cold")}


def session_total(store) -> int:
    metrics = store.data.get("metrics")
    if isinstance(metrics, dict) and isinstance(metrics.get("sessions_total"), int):
        return int(metrics["sessions_total"])
    return sum(1 for entity in store.entities if entity.collection == "journal")


def warm_patterns_without_causal_source(entities: list["Entity"]) -> list["Entity"]:
    return [
        entity
        for entity in entities
        if entity.collection == "patterns"
        and str(entity.value.get("tier", "")).lower() == "warm"
        and not has_scar_or_ghost_causal_source(entity.value)
    ]


def stale_warm_patterns_without_causal_source(entities: list["Entity"], sessions_total: int, threshold: int = 20) -> list["Entity"]:
    return [
        entity
        for entity in warm_patterns_without_causal_source(entities)
        if sessions_total - source_number(entity.value) >= threshold
    ]


def has_scar_or_ghost_causal_source(value: dict[str, Any]) -> bool:
    refs: list[str] = []
    refs.extend(flatten_strings(value.get("superseded_by")))
    causal = value.get("liens_causaux")
    if isinstance(causal, dict):
        for field in ("source", "prevenu_par", "contribue_a", "complements"):
            refs.extend(flatten_strings(causal.get(field)))
    return any(ref.startswith(("SCAR-", "GHOST-")) for ref in refs)


def finding_payload(item: Any) -> dict[str, str]:
    return {
        "severity": str(getattr(item, "severity", "")),
        "code": str(getattr(item, "code", "")),
        "location": str(getattr(item, "location", "")),
        "message": str(getattr(item, "message", "")),
        "suggestion": str(getattr(item, "suggestion", "")),
    }


def first_string(value: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        raw = value.get(field)
        if isinstance(raw, str) and raw:
            return raw
    return ""


def source_number(value: dict[str, Any]) -> int:
    refs: list[str] = []
    for field in ("validated_by_session", "scribe_delta"):
        refs.extend(flatten_strings(value.get(field)))
    for field in ("liens_causaux", "evidence", "validite"):
        refs.extend(flatten_strings(value.get(field)))
    numbers = [int(match) for ref in refs for match in re.findall(r"JOURNAL-(\d+)", ref)]
    return max(numbers, default=0)


def flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        items: list[str] = []
        for child in value:
            items.extend(flatten_strings(child))
        return items
    if isinstance(value, dict):
        items: list[str] = []
        for child in value.values():
            items.extend(flatten_strings(child))
        return items
    return []


def write_index(index_path: Path, payload: dict[str, Any]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = index_path.with_suffix(index_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(index_path)


def rank_index_entities(items: list[dict[str, Any]], topic: str | None = None) -> list[tuple[int, dict[str, Any]]]:
    scored = [(score_index_entity(item, topic), item) for item in items]
    scored.sort(key=lambda pair: (pair[0], str(pair[1].get("date") or ""), int(pair[1].get("source_number") or 0), -int(pair[1].get("position") or 0)), reverse=True)
    return scored


def score_index_entity(item: dict[str, Any], topic: str | None) -> int:
    if not topic:
        return 0
    raw_tokens = tokenize(topic)
    query_tokens = expand_tokens(raw_tokens)
    if not query_tokens:
        return 0
    text = " ".join(
        str(part or "")
        for part in (
            item.get("id"),
            item.get("collection"),
            item.get("title"),
            item.get("abstract"),
            item.get("search_text"),
        )
    )
    doc_tokens = expand_tokens(tokenize(text))
    if not doc_tokens:
        return 0
    normalized_query = normalize_text(topic)
    title = normalize_text(str(item.get("title") or ""))
    abstract = normalize_text(str(item.get("abstract") or ""))
    haystack = normalize_text(text)
    overlap = query_tokens & doc_tokens
    score = sum(3 for _ in overlap)
    score += len(query_tokens & expand_tokens(tokenize(str(item.get("title") or "")))) * 8
    score += len(query_tokens & expand_tokens(tokenize(str(item.get("abstract") or "")))) * 5
    if normalized_query and normalized_query in title:
        score += 35
    if normalized_query and normalized_query in abstract:
        score += 18
    if normalized_query and normalized_query in haystack:
        score += 10
    return score if score >= 6 else 0


def topic_matches(scored: list[tuple[int, dict[str, Any]]]) -> list[tuple[int, dict[str, Any]]]:
    return [(score, item) for score, item in scored if score > 0]


def search_index_entities(
    payload: dict[str, Any],
    query: str,
    limit: int = 8,
    collections: set[str] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    items = payload.get("entities")
    if not isinstance(items, list):
        items = payload.get("hot_entities", [])
    scored = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if collections is not None and str(item.get("collection") or "") not in collections:
            continue
        score = score_index_entity(item, query)
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("collection") or ""), str(pair[1].get("id") or "")))
    return scored[:limit]
