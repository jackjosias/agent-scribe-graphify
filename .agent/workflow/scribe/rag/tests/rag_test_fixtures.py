from __future__ import annotations

from typing import Any

from rag_index import build_negative_memory_index, normalize_entity


RAW_AUTH_ENTITIES: list[dict[str, Any]] = [
    {
        "id": "GHOST-005",
        "collection": "ghosts",
        "tier": "hot",
        "status": "ACTIVE",
        "title": "Cookie-only browser auth",
        "abstract": "Browser JWT secrets must stay in HttpOnly cookies; never store tokens in localStorage or browser storage.",
        "incoming": ["JOURNAL-058"],
        "outgoing": ["PAT-027"],
        "value": {
            "date": "2026-05-26",
            "scope": "auth security",
            "l0_abstract": "Access and refresh tokens for browser clients belong in HttpOnly SameSite cookies, not localStorage, sessionStorage, Redux, or any JavaScript-readable browser store.",
            "l2_details": "The approved browser pattern is cookie-only with CSRF protection. Storing JWT client-side makes XSS credential theft permanent.",
            "ne_pas_reproposer": [
                "mettre JWT en localStorage",
                "stocker token côté client",
                "utiliser sessionStorage pour les tokens",
            ],
            "alternatives_rejetees": [
                {"nom": "localStorage JWT", "raison_rejet": "JavaScript-readable token theft under XSS."}
            ],
            "evidence": {"type": "OBSERVED", "source": "JOURNAL-058"},
        },
    },
    {
        "id": "SCAR-003",
        "collection": "scars",
        "tier": "hot",
        "status": "ACTIVE",
        "title": "Refresh rotation race",
        "abstract": "Concurrent refresh requests can race; one token family must be revoked and the second request must fail cleanly.",
        "incoming": ["JOURNAL-063"],
        "outgoing": ["VAC-009"],
        "value": {
            "date": "2026-05-26",
            "scope": "auth runtime",
            "l0_abstract": "A concurrent refresh race must be handled atomically so only one refresh succeeds and reuse detection revokes the family.",
            "l2_details": "The bug appears when two refresh requests use the same current token. The repository/use case must map the loser to RefreshTokenRotationConflictError.",
            "evidence": {"type": "OBSERVED", "source": "JOURNAL-063"},
        },
    },
    {
        "id": "PAT-027",
        "collection": "patterns",
        "tier": "hot",
        "status": "ACTIVE",
        "title": "Approved HttpOnly refresh rotation",
        "abstract": "Cookies HttpOnly plus refresh rotation plus CSRF protection is an approved browser security pattern.",
        "incoming": ["GHOST-005"],
        "outgoing": [],
        "value": {
            "date": "2026-05-26",
            "scope": "auth security",
            "approved": True,
            "l0_abstract": "Cookies HttpOnly with refresh rotation and CSRF validation are approved for browser auth.",
            "l2_details": "This approval applies to the storage and transport pattern, not to ignoring refresh-race handling.",
            "evidence": {"type": "OBSERVED", "source": "JOURNAL-074"},
        },
    },
]


RAW_AGENT_PROTOCOL_ENTITIES: list[dict[str, Any]] = [
    {
        "id": "PAT-GRAPH-001",
        "collection": "patterns",
        "tier": "hot",
        "status": "ACTIVE",
        "title": "Graphify and SCRIBE separation",
        "abstract": "Use Graphify for structural code facts and SCRIBE for causal project memory.",
        "incoming": ["JOURNAL-000"],
        "outgoing": [],
        "value": {
            "date": "2026-05-28",
            "scope": "dev",
            "l0_abstract": "Graphify owns code structure, dependencies, god-nodes, and blast radius; SCRIBE owns causal decisions and pain.",
            "evidence": {"type": "OBSERVED", "source": "JOURNAL-000"},
        },
    },
    {
        "id": "PAT-GIT-001",
        "collection": "patterns",
        "tier": "hot",
        "status": "ACTIVE",
        "title": "Agent artifact versioning boundary",
        "abstract": "Commit product source by default; keep graphify-out and scribe-out generated state out of product commits.",
        "incoming": ["JOURNAL-000"],
        "outgoing": [],
        "value": {
            "date": "2026-05-28",
            "scope": "dev",
            "l0_abstract": "Generated Graphify and SCRIBE runtime artifacts are reconstructible local state unless explicitly shared.",
            "evidence": {"type": "REASONED", "source": "JOURNAL-000"},
        },
    },
    {
        "id": "INV-F004",
        "collection": "invariants",
        "tier": "hot",
        "status": "ACTIVE",
        "title": "Generated agent state stays out of product commits",
        "abstract": "Version host product source by default; keep graphify-out and scribe-out out of commits unless explicitly requested.",
        "incoming": [],
        "outgoing": [],
        "value": {"scope": "dev"},
    },
    {
        "id": "PAT-SCRIBE-RAG-001",
        "collection": "patterns",
        "tier": "hot",
        "status": "ACTIVE",
        "title": "scribe-rag preflight proof",
        "abstract": "Agents must use scribe-rag preflight/context/query/challenge as the proof surface before significant work.",
        "incoming": ["JOURNAL-000"],
        "outgoing": ["GHOST-SCRIBE-RAG-SEL-DIRECT-001"],
        "value": {
            "date": "2026-05-28",
            "scope": "dev bundle",
            "l0_abstract": "The agent memory proof is scribe-rag preflight, then focused query/explain, then challenge before implementation; SEL stays internal.",
            "l2_details": "This prevents host models from reading policy text once and skipping the actual memory retrieval path.",
            "approved": True,
            "evidence": {"type": "OBSERVED", "source": "JOURNAL-000"},
        },
    },
    {
        "id": "GHOST-SCRIBE-RAG-SEL-DIRECT-001",
        "collection": "ghosts",
        "tier": "hot",
        "status": "ACTIVE",
        "title": "No SEL direct retrieval for agents",
        "abstract": "SEL direct context/query/challenge is rejected for host-agent retrieval; use scribe-rag so retrieval is compact and auditable.",
        "incoming": ["PAT-SCRIBE-RAG-001"],
        "outgoing": [],
        "value": {
            "date": "2026-05-28",
            "scope": "dev bundle",
            "l0_abstract": "Agents read memory through scribe-rag only; SEL direct commands are reserved for maintenance, guard, sync, lock, export, and writes.",
            "ne_pas_reproposer": [
                "appeler SEL direct context query pour retrieval agent",
                "utiliser scribe context au lieu de scribe-rag",
                "lire AGENT-MEMOIRE_PROJECT_STATUS.scribe directement pour retrieval agent",
            ],
            "alternatives_rejetees": [
                {"nom": "SEL direct retrieval", "raison_rejet": "It bypasses the compact agent proof surface and makes policy compliance invisible."}
            ],
            "evidence": {"type": "OBSERVED", "source": "JOURNAL-000"},
        },
    },
]


def _build_test_index(raw_entities: list[dict[str, Any]]) -> dict[str, Any]:
    entities = [normalize_entity(entity, position) for position, entity in enumerate(raw_entities)]
    negative_index = build_negative_memory_index(raw_entities)
    return {
        "version": 2,
        "mode": "bm25",
        "source": "rag-test-fixture",
        "entities": entities,
        "negative_memory_index": negative_index,
        "stats": {"entities": len(entities), "negative_terms": len(negative_index)},
    }


def build_auth_test_index() -> dict[str, Any]:
    return _build_test_index(RAW_AUTH_ENTITIES)


def build_agent_protocol_test_index() -> dict[str, Any]:
    return _build_test_index(RAW_AGENT_PROTOCOL_ENTITIES)
