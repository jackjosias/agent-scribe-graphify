from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from scribe_doctor_model import Entity


SEARCH_FIELDS = (
    "id",
    "titre",
    "title",
    "l0_abstract",
    "pourquoi",
    "virus",
    "antidote",
    "contexte",
    "l2_details",
    "plan_remboursement",
    "scope",
    "status",
    "tier",
    "validations",
    "hot_entries_consulted",
    "scribe_delta",
    "ne_pas_reproposer",
    "alternatives_rejetees",
)
SYNONYMS = {
    "abstrait": {"abstract", "concept", "conceptual", "intention", "intent"},
    "archive": {"archival", "archiver", "retention", "cold"},
    "benchmark": {"bench", "perf", "performance", "latency", "charge", "scale", "load"},
    "dashboard": {"tableau", "visualisation", "ui", "html", "charts", "chart"},
    "degradation": {"demotion", "demote", "retrogradation", "retier", "aging", "vieillissement"},
    "fiabilite": {"reliability", "reliable", "robustesse", "robust", "surete", "resilience"},
    "friction": {"ritual", "rituel", "overhead", "lenteur", "lent", "surcout", "temps", "ceremony"},
    "graph": {"graphe", "graphify", "structure", "structural"},
    "hot": {"chaud", "prioritaire", "urgent", "active"},
    "inflation": {"growth", "grossit", "bloated", "bloat", "overflow", "pressure", "pression"},
    "memoire": {"memory", "scribe", "causal", "causale", "historique"},
    "perf": {"performance", "speed", "latency", "rapide", "temps", "throughput"},
    "performance": {"perf", "speed", "latency", "rapide", "throughput"},
    "query": {"search", "recherche", "retrieval", "rag"},
    "regression": {"breakage", "rollback", "risk", "risque", "casse", "perte"},
    "securite": {"security", "auth", "abuse", "attack"},
    "semantique": {"semantic", "conceptual", "abstrait", "meaning", "intent"},
    "tooling": {"bundle", "adapter", "install", "shim"},
}
CONCEPT_GROUPS = {
    "context_friction": {
        "agentique",
        "attention",
        "ceremony",
        "friction",
        "lenteur",
        "overhead",
        "ritual",
        "rituel",
        "surcout",
        "temps",
        "tokens",
    },
    "hot_pressure": {
        "aging",
        "bloat",
        "chaud",
        "degradation",
        "demote",
        "grossit",
        "hot",
        "inflation",
        "pressure",
        "prioritaire",
        "retrogradation",
        "vieillissement",
    },
    "local_retrieval": {
        "abstrait",
        "concept",
        "fuzzy",
        "lexical",
        "query",
        "rag",
        "recherche",
        "retrieval",
        "search",
        "semantic",
        "semantique",
        "synonymes",
    },
    "scale_perf": {
        "100k",
        "benchmark",
        "charge",
        "latency",
        "load",
        "perf",
        "performance",
        "scale",
        "throughput",
    },
}
MIN_RELEVANCE_SCORE = 6
FUZZY_MIN_TOKEN_LENGTH = 5
FUZZY_MATCH_RATIO = 0.86
FUZZY_MATCH_SCORE = 6
FUZZY_MAX_EDIT_DISTANCE = 2
TOKEN_SUFFIXES = (
    "ements",
    "ement",
    "ations",
    "ation",
    "iques",
    "ique",
    "ites",
    "ite",
    "ale",
    "aux",
    "es",
    "s",
)


@dataclass(frozen=True)
class SearchDoc:
    entity: Entity
    text: str
    title: str
    abstract: str
    tokens: set[str]
    token_counts: dict[str, int]
    title_tokens: set[str]
    abstract_tokens: set[str]
    primary_tokens: set[str]
    fuzzy_buckets: dict[str, set[tuple[str, str]]]
    primary_fuzzy_buckets: dict[str, set[tuple[str, str]]]


def build_search_doc(entity: Entity, title: str, abstract: str, related_text: str = "") -> SearchDoc:
    primary_text = entity_text(entity)
    text = " ".join(part for part in (primary_text, related_text) if part)
    tokens = expand_tokens(tokenize(text))
    primary_tokens = expand_tokens(tokenize(primary_text))
    return SearchDoc(
        entity=entity,
        text=text,
        title=title,
        abstract=abstract,
        tokens=tokens,
        token_counts=dict(Counter(tokens)),
        title_tokens=expand_tokens(tokenize(title)),
        abstract_tokens=expand_tokens(tokenize(abstract)),
        primary_tokens=primary_tokens,
        fuzzy_buckets=build_fuzzy_buckets(tokens),
        primary_fuzzy_buckets=build_fuzzy_buckets(primary_tokens),
    )


def build_fuzzy_buckets(tokens: set[str]) -> dict[str, set[tuple[str, str]]]:
    buckets: dict[str, set[tuple[str, str]]] = {}
    for token in tokens:
        if not is_fuzzy_candidate(token):
            continue
        root = token_root(token)
        if root:
            buckets.setdefault(root[0], set()).add((root, token))
    return buckets


def entity_text(entity: Entity) -> str:
    parts = [entity.id or "", entity.collection]
    for field in SEARCH_FIELDS:
        parts.extend(flatten_strings(entity.value.get(field)))
    evidence = entity.value.get("evidence")
    if isinstance(evidence, dict):
        parts.extend(flatten_strings(evidence.get("observable")))
    liens = entity.value.get("liens_causaux")
    if isinstance(liens, dict):
        parts.extend(flatten_strings(liens))
    return " ".join(part for part in parts if part)


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
    for concept, members in CONCEPT_GROUPS.items():
        if expanded & members:
            expanded.add(f"concept:{concept}")
            expanded.update(members)
    return expanded


def token_root(token: str) -> str:
    if token.endswith("aux") and len(token) > 5:
        return token[:-3] + "al"
    if token.endswith("ale") and len(token) > 5:
        return token[:-1]
    for suffix in TOKEN_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def fuzzy_token_matches(query_tokens: set[str], doc_buckets: dict[str, set[tuple[str, str]]]) -> set[str]:
    matches: set[str] = set()
    for query_token in query_tokens:
        if not is_fuzzy_candidate(query_token):
            continue
        query_root = token_root(query_token)
        for doc_root, doc_token in doc_buckets.get(query_root[:1], set()):
            if abs(len(query_token) - len(doc_token)) > max(2, len(query_token) // 3):
                continue
            if tokens_are_fuzzy_match(query_root, doc_root, query_token, doc_token):
                matches.add(query_token)
                break
    return matches


def tokens_are_fuzzy_match(query_root: str, doc_root: str, query_token: str, doc_token: str) -> bool:
    if query_root == doc_root:
        return True
    if SequenceMatcher(None, query_token, doc_token).ratio() >= FUZZY_MATCH_RATIO:
        return True
    return edit_distance_at_most(query_token, doc_token, FUZZY_MAX_EDIT_DISTANCE)


def edit_distance_at_most(left: str, right: str, maximum: int) -> bool:
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        row_minimum = current[0]
        for right_index, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            current.append(min(previous[right_index] + 1, current[-1] + 1, previous[right_index - 1] + cost))
            row_minimum = min(row_minimum, current[-1])
        if row_minimum > maximum:
            return False
        previous = current
    return previous[-1] <= maximum


def is_fuzzy_candidate(token: str) -> bool:
    return len(token) >= FUZZY_MIN_TOKEN_LENGTH and not any(char in token for char in ":./-_0123456789")


def score_doc(doc: SearchDoc, query: str, query_tokens: set[str], fuzzy_tokens: set[str] | None = None) -> int:
    normalized_query = normalize_text(query)
    entity_id = normalize_text(doc.entity.id or "")
    haystack = normalize_text(doc.text)
    title = normalize_text(doc.title)
    abstract = normalize_text(doc.abstract)
    score = 0
    if normalized_query == entity_id:
        score += 120
    if normalized_query and normalized_query in title:
        score += 35
    if normalized_query and normalized_query in abstract:
        score += 18
    if normalized_query and normalized_query in haystack:
        score += 10

    overlap = query_tokens & doc.tokens
    fuzzy_source = fuzzy_tokens if fuzzy_tokens is not None else query_tokens
    fuzzy_overlap = fuzzy_token_matches(fuzzy_source - doc.tokens, doc.fuzzy_buckets) if fuzzy_source else set()
    primary_fuzzy_overlap = fuzzy_token_matches(fuzzy_source - doc.primary_tokens, doc.primary_fuzzy_buckets) if fuzzy_source else set()
    title_overlap = query_tokens & doc.title_tokens
    abstract_overlap = query_tokens & doc.abstract_tokens
    score += sum(min(doc.token_counts.get(token, 0), 3) for token in overlap) * 3
    score += len(title_overlap) * 8
    score += len(abstract_overlap) * 5
    score += min(len(fuzzy_overlap), 3) * FUZZY_MATCH_SCORE
    score += min(len(primary_fuzzy_overlap), 3) * 4
    if query_tokens:
        coverage = len(overlap | fuzzy_overlap) / len(query_tokens)
        if coverage >= 0.5:
            score += 4
    if (overlap or fuzzy_overlap) and str(doc.entity.value.get("tier", "")).lower() == "hot":
        score += 2
    return score if score >= MIN_RELEVANCE_SCORE else 0
