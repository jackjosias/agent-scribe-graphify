from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from rag_embeddings import available as embeddings_available, encode_texts
from rag_text import bm25_scores, expand_tokens, tokenize

FAILURE_WORDS = {"bug", "erreur", "error", "crash", "broken", "fail", "failure", "casse", "probleme", "problem", "fix", "race"}

@dataclass(frozen=True)
class RagResult:
    entity: dict[str, Any]
    score: float
    reasons: dict[str, float]
    negative_matches: list[dict[str, Any]]


def tier_weight(entity: dict[str, Any]) -> float:
    tier = str(entity.get("tier") or "").lower()
    return {"hot": 1.0, "warm": 0.55, "cold": 0.15}.get(tier, 0.25)


def evidence_quality(entity: dict[str, Any]) -> float:
    evidence = str(entity.get("evidence_type") or "").upper()
    return {"OBSERVED": 1.0, "REASONED": 0.6, "ASSUMED": 0.2}.get(evidence, 0.1)


def causal_centrality(entity: dict[str, Any]) -> float:
    return min(1.0, float(entity.get("causal_links_count") or 0) / 10.0)


def scope_match(context: str, entity: dict[str, Any]) -> float:
    scope = str(entity.get("scope") or "universal").lower()
    context = str(context or "production").lower()
    if scope == "universal":
        return 0.9
    if scope == context or scope in context or context in scope:
        return 1.0
    if scope == "test" and context == "production":
        return 0.05
    if scope in {"auth", "security", "runtime", "backend", "frontend"} and context in {"production", "runtime", "auth"}:
        return 0.75
    return 0.4


def recency_score(entity: dict[str, Any]) -> float:
    if entity.get("is_invariant"):
        return 1.0
    raw = str(entity.get("date") or "")[:10]
    if not raw:
        return 0.25
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return 0.25
    days = max(0, (date.today() - parsed).days)
    return max(0.1, 1.0 - days / 365.0)


def failure_boost(query: str, entity: dict[str, Any]) -> float:
    query_terms = expand_tokens(tokenize(query))
    if not (query_terms & FAILURE_WORDS):
        return 0.0
    collection = str(entity.get("collection") or "")
    if collection == "scars":
        return 1.0
    if collection == "vaccins":
        return 0.75
    return 0.0


NEGATIVE_BLOCKER_TERMS = {"localstorage", "sessionstorage", "redux", "bearer", "browser-stor", "browser-stored"}
NEGATIVE_GENERIC_TERMS = {
    "auth",
    "bootstrap",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "empty",
    "httponly",
    "jwt",
    "portable",
    "project",
    "projet",
    "secret",
    "secrets",
    "session",
    "token",
    "vide",
}
NEGATIVE_REQUIRED_GROUPS = (
    {"direct", "directe", "directement"},
    {"archive", "archiver", "archivage"},
)


def negative_matches(query: str, index: dict[str, Any]) -> list[dict[str, Any]]:
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return []
    matches: list[dict[str, Any]] = []
    for term, payload in (index.get("negative_memory_index") or {}).items():
        if not isinstance(payload, dict):
            continue
        term_tokens = set(tokenize(str(term)))
        if not term_tokens:
            continue
        overlap = query_tokens & term_tokens
        if any(group & term_tokens and not group & query_tokens for group in NEGATIVE_REQUIRED_GROUPS):
            continue
        blocker_overlap = overlap & NEGATIVE_BLOCKER_TERMS
        meaningful_overlap = overlap - NEGATIVE_GENERIC_TERMS
        meaningful_term_tokens = term_tokens - NEGATIVE_GENERIC_TERMS
        if blocker_overlap:
            matches.append({"term": term, **payload, "overlap": sorted(overlap)})
            continue
        if len(meaningful_overlap) < 2 or not meaningful_term_tokens:
            continue
        overlap_ratio = len(meaningful_overlap) / len(meaningful_term_tokens)
        required_ratio = 0.5 if len(meaningful_term_tokens) <= 4 else 0.6
        if overlap_ratio >= required_ratio:
            matches.append({"term": term, **payload, "overlap": sorted(overlap)})
    return matches


def entity_negative_score(entity: dict[str, Any], matches: list[dict[str, Any]], mode: str) -> float:
    if not matches:
        return 0.0
    entity_id = str(entity.get("id") or "")
    for match in matches:
        if str(match.get("ghost_id") or "") == entity_id:
            return 3.0 if mode == "challenge" else 1.0
    return 0.0


def cosine_score(query: str, entities: list[dict[str, Any]], index: dict[str, Any]) -> list[float]:
    if index.get("mode") != "hybrid" or not embeddings_available():
        return [0.0 for _ in entities]
    try:
        query_vector = encode_texts([query])[0]
    except Exception:
        return [0.0 for _ in entities]
    scores: list[float] = []
    for entity in entities:
        vector = entity.get("embedding")
        if not isinstance(vector, list) or not vector:
            scores.append(0.0)
            continue
        dot = sum(float(a) * float(b) for a, b in zip(query_vector, vector))
        scores.append(max(0.0, min(1.0, (dot + 1.0) / 2.0)))
    return scores


def retrieve(query: str, index: dict[str, Any], *, top_k: int = 5, mode: str = "query", context: str = "production") -> list[RagResult]:
    entities = [item for item in index.get("entities", []) if isinstance(item, dict)]
    documents = [list(item.get("tokens") or []) for item in entities]
    bm25 = bm25_scores(query, documents)
    semantic = cosine_score(query, entities, index)
    neg_matches = negative_matches(query, index)
    if index.get("mode") == "hybrid":
        weights = {
            "bm25": 0.22,
            "semantic": 0.24,
            "causal": 0.15,
            "tier": 0.10,
            "evidence": 0.06,
            "scope": 0.06,
            "failure": 0.07,
            "negative": 0.10,
        }
        if mode == "challenge":
            weights = {"bm25": 0.16, "semantic": 0.16, "causal": 0.11, "tier": 0.08, "evidence": 0.05, "scope": 0.05, "failure": 0.07, "negative": 0.32}
    else:
        weights = {
            "bm25": 0.35,
            "semantic": 0.0,
            "causal": 0.18,
            "tier": 0.13,
            "evidence": 0.07,
            "scope": 0.07,
            "failure": 0.08,
            "negative": 0.12,
        }
        if mode == "challenge":
            weights = {"bm25": 0.22, "semantic": 0.0, "causal": 0.13, "tier": 0.10, "evidence": 0.06, "scope": 0.06, "failure": 0.08, "negative": 0.35}
    results: list[RagResult] = []
    for entity, bm25_score, semantic_score in zip(entities, bm25, semantic):
        reasons = {
            "bm25": bm25_score,
            "semantic": semantic_score,
            "causal": causal_centrality(entity),
            "tier": tier_weight(entity),
            "evidence": evidence_quality(entity),
            "scope": scope_match(context, entity),
            "failure": failure_boost(query, entity),
            "negative": min(1.0, entity_negative_score(entity, neg_matches, mode)),
            "recency": recency_score(entity),
        }
        score = sum(weights[key] * reasons[key] for key in weights)
        if bm25_score <= 0 and semantic_score <= 0 and reasons["negative"] <= 0 and reasons["failure"] <= 0:
            continue
        entity_matches = [match for match in neg_matches if match.get("ghost_id") == entity.get("id")]
        results.append(RagResult(entity=entity, score=score, reasons=reasons, negative_matches=entity_matches if reasons["negative"] else []))
    results.sort(key=lambda item: (-item.score, -tier_weight(item.entity), str(item.entity.get("id") or "")))
    return results[:top_k]
