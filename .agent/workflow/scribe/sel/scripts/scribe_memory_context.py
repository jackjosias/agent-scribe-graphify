from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scribe_doctor_checks import check_all
from scribe_doctor_model import Entity, Finding
from scribe_doctor_report import has_errors
from scribe_context_quick import context_payload as quick_index_payload
from scribe_context_quick import print_context_text as print_quick_context_text
from scribe_index import ensure_quick_index
from scribe_store import ScribeStore, compact_entity, entity_abstract, entity_title, load_scribe


@dataclass(frozen=True)
class ContextProfile:
    hot_limit: int
    topic_limit: int
    debt_limit: int
    include_doctor: bool


CONTEXT_PROFILES = {
    "quick": ContextProfile(hot_limit=5, topic_limit=3, debt_limit=1, include_doctor=False),
    "standard": ContextProfile(hot_limit=8, topic_limit=5, debt_limit=3, include_doctor=True),
}


def cmd_context(args: argparse.Namespace) -> int:
    if args.mode == "quick":
        return cmd_quick_context(args)

    store = load_scribe(Path(args.scribe))
    profile = CONTEXT_PROFILES[args.mode]
    hot_limit = args.limit if args.limit is not None else profile.hot_limit
    topic_limit = args.topic_limit if args.topic_limit is not None else profile.topic_limit
    findings: list[Finding] | None = full_findings(store) if profile.include_doctor else None
    if args.format == "json":
        payload = context_payload(store, args.mode, profile, hot_limit, args.topic, topic_limit, findings)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if findings is not None and has_errors(findings) else 0

    print(f"SCRIBE CONTEXT [{args.mode}]")
    print(f"  source: {store.path}")
    print(f"  policy: minimal context; use doctor/challenge only when risk escalates.")
    print_doctor_line(store, profile, findings)
    print_hot_block(store, hot_limit, args.topic)
    if args.topic:
        print_topic_block(store, args.topic, topic_limit)
    print_debt_block(store, profile.debt_limit)
    return 1 if findings is not None and has_errors(findings) else 0


def cmd_quick_context(args: argparse.Namespace) -> int:
    profile = CONTEXT_PROFILES["quick"]
    hot_limit = args.limit if args.limit is not None else profile.hot_limit
    topic_limit = args.topic_limit if args.topic_limit is not None else profile.topic_limit
    quick_index = ensure_quick_index(Path(args.scribe))
    if args.format == "json":
        print(json.dumps(quick_index_payload(quick_index, hot_limit, args.topic, topic_limit), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print_quick_context_text(quick_index, hot_limit, args.topic, topic_limit)
    return 0


def context_payload(
    store: ScribeStore,
    mode: str,
    profile: ContextProfile,
    hot_limit: int,
    topic: str | None,
    topic_limit: int,
    findings: list[Finding] | None,
) -> dict:
    hot_entities = ranked_hot_entities(store, topic)
    topic_results = store.search(topic, limit=topic_limit) if topic else []
    full = findings or []
    return {
        "mode": mode,
        "source": str(store.path),
        "policy": "minimal context; use doctor/challenge only when risk escalates.",
        "doctor": {
            "included": profile.include_doctor,
            "errors": sum(1 for item in full if item.severity == "ERROR"),
            "warnings": sum(1 for item in full if item.severity == "WARNING"),
        },
        "hot_label": "hot" if not topic else "hot_by_topic",
        "hot": [entity_payload(entity) for entity in hot_entities[:hot_limit]],
        "topic": topic,
        "topic_results": [entity_payload(doc.entity, score) for score, doc in topic_results],
        "active_debts": [entity_payload(entity) for entity in active_debts(store)[: profile.debt_limit]],
    }


def entity_payload(entity: Entity, score: int | None = None) -> dict:
    payload = {
        "id": entity.id,
        "collection": entity.collection,
        "tier": entity.value.get("tier"),
        "status": entity.value.get("status", entity.value.get("statut")),
        "title": entity_title(entity),
        "abstract": entity_abstract(entity),
    }
    if score is not None:
        payload["score"] = score
    return payload



def print_doctor_line(store: ScribeStore, profile: ContextProfile, findings: list[Finding] | None) -> None:
    if not profile.include_doctor:
        print("  doctor: skipped for quick mode")
        if store.findings:
            print(f"  parse_findings: {len(store.findings)}")
        return

    full = findings or []
    errors = sum(1 for item in full if item.severity == "ERROR")
    warnings = sum(1 for item in full if item.severity == "WARNING")
    print(f"  doctor: {errors} error(s), {warnings} warning(s)")


def ranked_hot_entities(store: ScribeStore, topic: str | None = None) -> list[Entity]:
    hot_entities = store.hot_entities()
    topic_scores = topic_score_by_id(store, topic)
    indexed = list(enumerate(hot_entities))
    indexed.sort(key=lambda item: hot_sort_key(item[0], item[1], topic_scores), reverse=True)
    return [entity for _, entity in indexed]


def print_hot_block(store: ScribeStore, limit: int, topic: str | None = None) -> None:
    hot_entities = ranked_hot_entities(store, topic)
    label = "hot" if not topic else "hot_by_topic"
    print(f"  {label}: {min(limit, len(hot_entities))}/{len(hot_entities)}")
    print_compact_entities(hot_entities[:limit])


def print_topic_block(store: ScribeStore, topic: str, limit: int) -> None:
    print(f"  topic: {topic}")
    results = store.search(topic, limit=limit)
    if not results:
        print("    - no local causal match")
        return
    for score, doc in results:
        print(f"    - score={score} {compact_entity(doc.entity)} :: {short_title(doc.entity)}")
        abstract = short_text(doc.abstract)
        if abstract:
            print(f"      {abstract}")


def print_debt_block(store: ScribeStore, limit: int) -> None:
    debts = active_debts(store)
    print(f"  active_debts: {min(limit, len(debts))}/{len(debts)}")
    print_compact_entities(debts[:limit], indent="    ")


def print_compact_entities(entities: Iterable, indent: str = "    ") -> None:
    printed = False
    for entity in entities:
        printed = True
        print(f"{indent}- {compact_entity(entity)} :: {short_title(entity)}")
        abstract = short_text(entity_abstract(entity))
        if abstract:
            print(f"{indent}  {abstract}")
    if not printed:
        print(f"{indent}- none")


def active_debts(store: ScribeStore) -> list:
    return [
        entity
        for entity in store.entities
        if entity.collection in {"debts", "dettes"} and str(entity.value.get("status", "")).upper() == "ACTIVE"
    ]


def full_findings(store: ScribeStore) -> list[Finding]:
    findings = store.findings[:]
    if store.data:
        findings.extend(check_all(store.data, store.raw, store.entities, store.registry, store.path))
    return findings


def topic_score_by_id(store: ScribeStore, topic: str | None) -> dict[str, int]:
    if not topic:
        return {}
    return {doc.entity.id or doc.entity.path: score for score, doc in store.search(topic, limit=len(store.entities))}


def hot_sort_key(index: int, entity: Entity, topic_scores: dict[str, int]) -> tuple[int, str, int, int]:
    entity_id = entity.id or entity.path
    date, source_number = recency_key(entity)
    return (topic_scores.get(entity_id, 0), date, source_number, -index)


def recency_key(entity: Entity) -> tuple[str, int]:
    value = entity.value
    dates = [str(value.get(field) or "") for field in ("date", "schema_patch_date", "date_creation")]
    validite = value.get("validite")
    if isinstance(validite, dict):
        dates.append(str(validite.get("derniere_verif") or ""))
    refs = collect_reference_strings(value)
    return max(dates, default=""), max((journal_number(ref) for ref in refs), default=0)


def collect_reference_strings(value: dict) -> list[str]:
    refs: list[str] = []
    for field in ("validated_by_session", "scribe_delta"):
        refs.extend(flatten_reference(value.get(field)))
    for field in ("liens_causaux", "evidence", "validite"):
        child = value.get(field)
        if isinstance(child, dict):
            refs.extend(flatten_reference(child))
    return refs


def flatten_reference(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        refs: list[str] = []
        for item in value:
            refs.extend(flatten_reference(item))
        return refs
    if isinstance(value, dict):
        refs: list[str] = []
        for item in value.values():
            refs.extend(flatten_reference(item))
        return refs
    return []


def journal_number(ref: str) -> int:
    numbers = [int(match) for match in re.findall(r"JOURNAL-(\d+)", ref)]
    return max(numbers, default=0)


def short_title(entity) -> str:
    return short_text(entity_title(entity), width=90)


def short_text(text: str, width: int = 140) -> str:
    clean = " ".join(text.split())
    if len(clean) <= width:
        return clean
    return clean[: max(0, width - 3)].rstrip() + "..."
