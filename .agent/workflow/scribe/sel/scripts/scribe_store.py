from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scribe_doctor_model import (
    Entity,
    Finding,
    as_refs,
    canonical_status,
    collect_entities,
    collect_registry,
    parse_yaml,
    read_text,
)
from scribe_search import SearchDoc, build_search_doc, expand_tokens, score_doc, tokenize


DEFAULT_SCRIBE_PATH = Path("AGENT-MEMOIRE_PROJECT_STATUS.scribe")
COLLECTION_ORDER = {
    "scars": 0,
    "vaccins": 1,
    "patterns": 2,
    "ghosts": 3,
    "hypotheses": 4,
    "debts": 5,
    "dettes": 5,
    "journal": 6,
}
EDGE_TYPES = ("causal", "evidence", "consultation", "journal")


@dataclass(frozen=True)
class ScribeIndex:
    id_index: dict[str, Entity]
    tier_index: dict[str, list[Entity]]
    text_index: list[SearchDoc]
    token_index: dict[str, list[int]]
    edge_types: dict[str, dict[str, set[str]]]
    causal_edges: dict[str, set[str]]
    reverse_edges: dict[str, set[str]]


@dataclass(frozen=True)
class ScribeStore:
    path: Path
    raw: str
    data: dict[str, Any]
    findings: list[Finding]
    entities: list[Entity]
    registry: dict[str, list[str]]
    index: ScribeIndex

    def by_id(self, entity_id: str) -> Entity | None:
        return self.index.id_index.get(entity_id)

    def hot_entities(self) -> list[Entity]:
        hot_ids = as_refs(self.data.get("tiers", {}).get("hot") if isinstance(self.data.get("tiers"), dict) else [])
        seen: set[str] = set()
        entities: list[Entity] = []
        for entity_id in hot_ids:
            entity = self.by_id(entity_id)
            if entity is not None:
                entities.append(entity)
                seen.add(entity_id)
        for entity in self.index.tier_index.get("hot", []):
            if entity.id and entity.id not in seen:
                entities.append(entity)
                seen.add(entity.id)
        return entities

    def related(self, entity_id: str) -> tuple[list[Entity], list[Entity]]:
        outgoing = sorted_entities(
            self.index.id_index[target]
            for target in self.index.causal_edges.get(entity_id, set())
            if target in self.index.id_index
        )
        incoming = sorted_entities(
            self.index.id_index[source]
            for source in self.index.reverse_edges.get(entity_id, set())
            if source in self.index.id_index
        )
        return outgoing, incoming

    def search(self, query: str, limit: int = 8, collections: set[str] | None = None) -> list[tuple[int, SearchDoc]]:
        raw_tokens = tokenize(query)
        query_tokens = expand_tokens(raw_tokens)
        if not query_tokens:
            return []
        candidate_indexes = candidate_doc_indexes(self.index.token_index, query_tokens)
        candidate_docs = [self.index.text_index[index] for index in sorted(candidate_indexes)] if candidate_indexes else self.index.text_index
        scored: list[tuple[int, SearchDoc]] = []
        for doc in candidate_docs:
            if collections is not None and doc.entity.collection not in collections:
                continue
            score = score_doc(doc, query, query_tokens, raw_tokens)
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda item: (-item[0], sort_key(item[1].entity)))
        return scored[:limit]


def load_scribe(path: Path = DEFAULT_SCRIBE_PATH) -> ScribeStore:
    raw = read_text(path)
    data, findings = parse_yaml(raw, path)
    if data is None:
        data = {}
    entities = collect_entities(data)
    registry = collect_registry(data)
    index = build_index(entities, registry)
    return ScribeStore(path, raw, data, findings, entities, registry, index)


def build_index(entities: list[Entity], registry: dict[str, list[str]]) -> ScribeIndex:
    with ThreadPoolExecutor(max_workers=2) as executor:
        tier_future = executor.submit(build_tier_index, entities)
        edges_future = executor.submit(build_edge_types, entities, set(registry))
        tier_index = tier_future.result()
        edge_types = edges_future.result()
    causal_edges = merge_edge_types(edge_types)
    reverse_edges = reverse_index(causal_edges)
    id_index = {entity.id: entity for entity in entities if entity.id}
    text_index = build_text_index(entities, id_index, causal_edges, reverse_edges)
    token_index = build_token_index(text_index)
    return ScribeIndex(id_index, tier_index, text_index, token_index, edge_types, causal_edges, reverse_edges)


def build_text_index(
    entities: list[Entity],
    id_index: dict[str, Entity] | None = None,
    causal_edges: dict[str, set[str]] | None = None,
    reverse_edges: dict[str, set[str]] | None = None,
) -> list[SearchDoc]:
    id_index = id_index or {entity.id: entity for entity in entities if entity.id}
    causal_edges = causal_edges or {}
    reverse_edges = reverse_edges or {}
    return [
        build_search_doc(entity, entity_title(entity), entity_abstract(entity), related_search_text(entity, id_index, causal_edges, reverse_edges))
        for entity in entities
    ]


def related_search_text(
    entity: Entity,
    id_index: dict[str, Entity],
    causal_edges: dict[str, set[str]],
    reverse_edges: dict[str, set[str]],
) -> str:
    if not entity.id:
        return ""
    related_ids = sorted((causal_edges.get(entity.id, set()) | reverse_edges.get(entity.id, set())) & set(id_index))[:12]
    parts: list[str] = []
    for related_id in related_ids:
        related = id_index[related_id]
        parts.append(entity_title(related))
        parts.append(entity_abstract(related))
    return " ".join(part for part in parts if part)


def build_token_index(docs: list[SearchDoc]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for doc_index, doc in enumerate(docs):
        for token in doc.tokens:
            index.setdefault(token, []).append(doc_index)
    return index


def candidate_doc_indexes(token_index: dict[str, list[int]], query_tokens: set[str]) -> set[int]:
    candidates: set[int] = set()
    for token in query_tokens:
        candidates.update(token_index.get(token, []))
    return candidates


def build_tier_index(entities: list[Entity]) -> dict[str, list[Entity]]:
    tiers: dict[str, list[Entity]] = {}
    for entity in entities:
        tier = str(entity.value.get("tier", "")).lower()
        if tier:
            tiers.setdefault(tier, []).append(entity)
    for tier, items in tiers.items():
        tiers[tier] = sorted_entities(items)
    return tiers


def build_causal_edges(entities: list[Entity], known_ids: set[str]) -> dict[str, set[str]]:
    return merge_edge_types(build_edge_types(entities, known_ids))


def build_edge_types(entities: list[Entity], known_ids: set[str]) -> dict[str, dict[str, set[str]]]:
    edge_types: dict[str, dict[str, set[str]]] = {edge_type: {} for edge_type in EDGE_TYPES}
    for entity in entities:
        if not entity.id:
            continue
        for edge_type, targets in typed_edge_targets(entity.value, known_ids).items():
            if targets:
                edge_types[edge_type][entity.id] = targets
    return edge_types


def merge_edge_types(edge_types: dict[str, dict[str, set[str]]]) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for edges in edge_types.values():
        for source, targets in edges.items():
            merged.setdefault(source, set()).update(targets)
    return merged


def edge_type_counts(edge_types: dict[str, dict[str, set[str]]]) -> dict[str, int]:
    return {edge_type: sum(len(targets) for targets in edge_types.get(edge_type, {}).values()) for edge_type in EDGE_TYPES}


def reverse_index(edges: dict[str, set[str]]) -> dict[str, set[str]]:
    reverse: dict[str, set[str]] = {}
    for source, targets in edges.items():
        for target in targets:
            reverse.setdefault(target, set()).add(source)
    return reverse


def edge_targets(value: dict[str, Any], known_ids: set[str]) -> set[str]:
    return set().union(*typed_edge_targets(value, known_ids).values())


def typed_edge_targets(value: dict[str, Any], known_ids: set[str]) -> dict[str, set[str]]:
    typed = {edge_type: set() for edge_type in EDGE_TYPES}
    typed["causal"].update(split_refs(value.get("superseded_by")))
    typed["consultation"].update(split_refs(value.get("hot_entries_consulted")))
    typed["journal"].update(split_refs(value.get("scribe_delta")))
    collect_causal_fields(value, typed)
    collect_evidence_fields(value, typed)
    return {edge_type: {target for target in targets if target in known_ids} for edge_type, targets in typed.items()}


def collect_causal_fields(value: dict[str, Any], typed: dict[str, set[str]]) -> None:
    causal = value.get("liens_causaux")
    if not isinstance(causal, dict):
        return
    for field in ("source", "prevenu_par", "contribue_a", "complements"):
        typed["causal"].update(split_refs(causal.get(field)))
    typed["journal"].update(split_refs(causal.get("renforce")))


def collect_evidence_fields(value: dict[str, Any], typed: dict[str, set[str]]) -> None:
    evidence = value.get("evidence")
    if isinstance(evidence, dict):
        typed["evidence"].update(split_refs(evidence.get("source")))
    typed["evidence"].update(split_refs(value.get("validated_by_session")))
    typed["evidence"].update(split_refs(value.get("confirmed_sessions")))


def split_refs(value: Any) -> set[str]:
    if isinstance(value, str):
        return {part.strip() for part in re.split(r"[|,\s]+", value) if part.strip()}
    if isinstance(value, list):
        refs: set[str] = set()
        for item in value:
            refs.update(split_refs(item))
        return refs
    if isinstance(value, dict):
        return {str(value["session"])} if isinstance(value.get("session"), str) else set()
    return set()


def entity_title(entity: Entity) -> str:
    title = entity.value.get("titre", entity.value.get("title", ""))
    if isinstance(title, str) and title:
        return title
    return entity.id or entity.path


def entity_abstract(entity: Entity) -> str:
    abstract = entity.value.get("l0_abstract", "")
    return abstract if isinstance(abstract, str) else ""


def sorted_entities(entities: Any) -> list[Entity]:
    return sorted(list(entities), key=sort_key)


def sort_key(entity: Entity) -> tuple[int, str]:
    return (COLLECTION_ORDER.get(entity.collection, 99), entity.id or entity.path)


def compact_entity(entity: Entity) -> str:
    tier = entity.value.get("tier", "-")
    status = canonical_status(entity.value) or "-"
    return f"{entity.id or entity.path} [{entity.collection}] tier={tier} status={status}"
