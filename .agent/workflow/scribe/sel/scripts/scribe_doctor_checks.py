from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from scribe_doctor_model import (
    Entity,
    Finding,
    as_refs,
    broken_reference,
    canonical_status,
    confirmed_session_ids,
    detect_v32_document,
    finding,
    has_causal_link,
    has_evidence_type,
    is_post_v32,
    looks_inverse,
    required_finding,
    session_count,
)
from scribe_state import WRITE_KINDS, check_sync


def check_all(
    data: dict[str, Any],
    raw: str,
    entities: list[Entity],
    registry: dict[str, list[str]],
    scribe_path: Path | None = None,
) -> list[Finding]:
    file_is_v32 = detect_v32_document(data, raw)
    findings: list[Finding] = []
    findings.extend(check_duplicate_ids(registry))
    findings.extend(check_schema_dates(entities, file_is_v32))
    findings.extend(check_required_v32_fields(entities, file_is_v32))
    findings.extend(check_broken_references(entities, registry))
    findings.extend(check_edge_type_ratio(entities, registry))
    findings.extend(check_warm_pattern_causal_audit(entities, data))
    findings.extend(check_status_and_evidence_rules(entities))
    findings.extend(check_hot_consultation(entities, data))
    findings.extend(check_ghost_index(entities, data))
    findings.extend(check_debts(entities))
    findings.extend(check_scope_contradictions(entities))
    if scribe_path is not None:
        findings.extend(check_state_file(scribe_path))
    return findings


def check_duplicate_ids(registry: dict[str, list[str]]) -> list[Finding]:
    findings: list[Finding] = []
    for entity_id, paths in sorted(registry.items()):
        if len(paths) > 1:
            findings.append(
                finding(
                    "ERROR",
                    "E004",
                    ", ".join(paths),
                    f"ID dupliqué détecté: {entity_id}",
                    "Renommer la nouvelle entrée; ne jamais réutiliser un ID historique.",
                )
            )
    return findings


def check_schema_dates(entities: list[Entity], file_is_v32: bool) -> list[Finding]:
    findings: list[Finding] = []
    legacy_missing = 0
    for entity in entities:
        if not entity.id or entity.collection == "journal":
            continue
        if entity.value.get("schema_patch_date"):
            continue
        if is_post_v32(entity, file_is_v32):
            findings.append(
                finding(
                    "ERROR",
                    "E010",
                    entity.path,
                    f"{entity.id} est post-V3.2 sans schema_patch_date.",
                    'Ajouter schema_patch_date: "2026-05-23" à la nouvelle entrée.',
                )
            )
        else:
            legacy_missing += 1
    if legacy_missing:
        findings.append(
            finding(
                "WARNING",
                "W009",
                "legacy entries",
                f"{legacy_missing} entrée(s) pré-V3.2 sans schema_patch_date.",
                "Ne pas migrer de force; doctor utilise entry.date comme fallback.",
            )
        )
    return findings


def check_required_v32_fields(entities: list[Entity], file_is_v32: bool) -> list[Finding]:
    findings: list[Finding] = []
    for entity in entities:
        if entity.collection not in {"vaccins", "patterns"}:
            continue
        is_new = is_post_v32(entity, file_is_v32)
        if not entity.value.get("scope"):
            findings.append(required_finding("E001", "W001", entity, is_new, "scope"))
        if not has_evidence_type(entity.value):
            findings.append(required_finding("E002", "W002", entity, is_new, "evidence.type"))
    return findings


def check_broken_references(entities: list[Entity], registry: dict[str, list[str]]) -> list[Finding]:
    findings: list[Finding] = []
    all_ids = set(registry)
    journal_ids = {entity.id for entity in entities if entity.collection == "journal" and entity.id}
    for entity in entities:
        location = entity.id or entity.path
        for target in as_refs(entity.value.get("superseded_by")):
            if target not in all_ids:
                findings.append(broken_reference("E005", location, "superseded_by", target))
        findings.extend(check_causal_refs(entity, all_ids, location))
        findings.extend(check_session_refs(entity, journal_ids, location))
    return findings


def check_causal_refs(entity: Entity, all_ids: set[str], location: str) -> list[Finding]:
    causal = entity.value.get("liens_causaux")
    if not isinstance(causal, dict):
        return []
    findings: list[Finding] = []
    for target in as_refs(causal.get("prevenu_par")):
        if target not in all_ids:
            findings.append(broken_reference("E006", location, "liens_causaux.prevenu_par", target))
    return findings


def check_session_refs(entity: Entity, journal_ids: set[str | None], location: str) -> list[Finding]:
    findings: list[Finding] = []
    for session_id in confirmed_session_ids(entity.value):
        if session_id not in journal_ids:
            findings.append(broken_reference("E006", location, "confirmed_sessions", session_id))
    validated_by = entity.value.get("validated_by_session")
    if isinstance(validated_by, str) and validated_by not in journal_ids:
        findings.append(broken_reference("E006", location, "validated_by_session", validated_by))
    evidence = entity.value.get("evidence")
    source = evidence.get("source") if isinstance(evidence, dict) else None
    if isinstance(source, str) and source.startswith("JOURNAL-") and source not in journal_ids:
        findings.append(broken_reference("E006", location, "evidence.source", source))
    return findings


def check_edge_type_ratio(entities: list[Entity], registry: dict[str, list[str]]) -> list[Finding]:
    counts = edge_type_reference_counts(entities, set(registry))
    total = sum(counts.values())
    if total == 0 or counts["causal"] / total >= 0.20:
        return []
    return [
        finding(
            "WARNING",
            "W010",
            "edge type distribution",
            f"causal_edges={counts['causal']} sur {total} edge(s) typés (<20%).",
            "Séparer consultation/journal du causal réel; relier aux SCAR/GHOST seulement si une cause prouvée existe.",
        )
    ]


def check_warm_pattern_causal_audit(entities: list[Entity], data: dict[str, Any]) -> list[Finding]:
    sessions_total = session_count(data)
    if sessions_total < 20:
        return []
    stale = [
        entity
        for entity in entities
        if entity.collection == "patterns"
        and str(entity.value.get("tier", "")).lower() == "warm"
        and not has_scar_or_ghost_causal_source(entity.value)
        and sessions_total - source_session_number(entity.value) >= 20
    ]
    if not stale:
        return []
    sample = ", ".join(entity.id or entity.path for entity in stale[:6])
    suffix = "" if len(stale) <= 6 else f", +{len(stale) - 6}"
    return [
        finding(
            "WARNING",
            "W014",
            "patterns warm causal audit",
            f"{len(stale)} PAT warm sans SCAR/GHOST source depuis >=20 sessions: {sample}{suffix}.",
            "Auditer: rattacher à un SCAR/GHOST réel seulement si la causalité existe; sinon rétrograder cold si la règle n'a pas prouvé sa valeur.",
        )
    ]


def has_scar_or_ghost_causal_source(value: dict[str, Any]) -> bool:
    refs: list[str] = []
    refs.extend(flatten_ref_strings(value.get("superseded_by")))
    causal = value.get("liens_causaux")
    if isinstance(causal, dict):
        for field in ("source", "prevenu_par", "contribue_a", "complements"):
            refs.extend(flatten_ref_strings(causal.get(field)))
    return any(ref.startswith(("SCAR-", "GHOST-")) for ref in refs)


def source_session_number(value: dict[str, Any]) -> int:
    refs: list[str] = []
    for field in ("validated_by_session", "scribe_delta"):
        refs.extend(flatten_ref_strings(value.get(field)))
    for field in ("liens_causaux", "evidence", "validite"):
        refs.extend(flatten_ref_strings(value.get(field)))
    return max((int(match) for ref in refs for match in re.findall(r"JOURNAL-(\d+)", ref)), default=0)


def flatten_ref_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        items: list[str] = []
        for child in value:
            items.extend(flatten_ref_strings(child))
        return items
    if isinstance(value, dict):
        items: list[str] = []
        for child in value.values():
            items.extend(flatten_ref_strings(child))
        return items
    return []


def edge_type_reference_counts(entities: list[Entity], all_ids: set[str]) -> dict[str, int]:
    counts = {"causal": 0, "evidence": 0, "consultation": 0, "journal": 0}
    for entity in entities:
        counts["causal"] += count_known_refs(entity.value.get("superseded_by"), all_ids)
        counts["consultation"] += count_known_refs(entity.value.get("hot_entries_consulted"), all_ids)
        counts["journal"] += count_known_refs(entity.value.get("scribe_delta"), all_ids)
        count_causal_fields(entity.value, all_ids, counts)
        count_evidence_fields(entity.value, all_ids, counts)
    return counts


def count_causal_fields(value: dict[str, Any], all_ids: set[str], counts: dict[str, int]) -> None:
    causal = value.get("liens_causaux")
    if not isinstance(causal, dict):
        return
    for field in ("source", "prevenu_par", "contribue_a", "complements"):
        counts["causal"] += count_known_refs(causal.get(field), all_ids)
    counts["journal"] += count_known_refs(causal.get("renforce"), all_ids)


def count_evidence_fields(value: dict[str, Any], all_ids: set[str], counts: dict[str, int]) -> None:
    evidence = value.get("evidence")
    if isinstance(evidence, dict):
        counts["evidence"] += count_known_refs(evidence.get("source"), all_ids)
    counts["evidence"] += count_known_refs(value.get("validated_by_session"), all_ids)
    counts["evidence"] += len([session for session in confirmed_session_ids(value) if session in all_ids])


def count_known_refs(value: Any, all_ids: set[str]) -> int:
    return len([target for target in as_refs(value) if target in all_ids])


def check_status_and_evidence_rules(entities: list[Entity]) -> list[Finding]:
    findings: list[Finding] = []
    for entity in entities:
        entity_id = entity.id or entity.path
        status = canonical_status(entity.value)
        if status and status != "ACTIVE" and not entity.value.get("status_log"):
            findings.append(finding("ERROR", "E003", entity.path, f"{entity_id} a status={status} sans status_log.", "Ajouter un status_log traçable."))
        findings.extend(check_evidence_rules(entity, entity_id))
    return findings


def check_evidence_rules(entity: Entity, entity_id: str) -> list[Finding]:
    findings: list[Finding] = []
    evidence = entity.value.get("evidence")
    evidence_type = str(evidence.get("type", "")).upper() if isinstance(evidence, dict) else ""
    if evidence_type == "OBSERVED" and not (evidence.get("source") and evidence.get("observable")):
        findings.append(finding("ERROR", "E007", entity.path, f"{entity_id} est OBSERVED sans source + observable.", "Ajouter source JOURNAL et observable concret."))
    if evidence_type == "ASSUMED" and str(entity.value.get("tier", "")).lower() == "hot":
        findings.append(finding("WARNING", "W006", entity.path, f"{entity_id} est hot avec evidence.type ASSUMED.", "Rétrograder en warm ou fournir une preuve."))
    if evidence_type == "REASONED" and str(entity.value.get("tier", "")).lower() == "hot":
        if len(confirmed_session_ids(entity.value)) < 2:
            findings.append(finding("WARNING", "W008", entity.path, f"{entity_id} est REASONED hot sans 2 confirmed_sessions.", "Ajouter deux confirmations JOURNAL réelles ou rester warm."))
    if entity.value.get("scope") == "universal":
        human_ok = entity.value.get("human_validated") is True and bool(entity.value.get("validated_by_session"))
        evidence_ok = evidence_type and evidence_type != "ASSUMED"
        if not (human_ok and evidence_ok):
            findings.append(finding("ERROR", "E009", entity.path, f"{entity_id} a scope universal sans validation forte.", "Exiger validation humaine et evidence non-ASSUMED."))
    return findings


def check_hot_consultation(entities: list[Entity], data: dict[str, Any]) -> list[Finding]:
    sessions_total = session_count(data)
    if sessions_total < 10:
        return []
    consulted = set()
    for entity in entities:
        if entity.collection == "journal":
            consulted.update(as_refs(entity.value.get("hot_entries_consulted")))
    findings: list[Finding] = []
    for entity in entities:
        if entity.collection == "journal" or not entity.id:
            continue
        if str(entity.value.get("tier", "")).lower() == "hot" and entity.id not in consulted:
            findings.append(finding("WARNING", "W003", entity.path, f"{entity.id} hot jamais consulté après {sessions_total} sessions.", "Réévaluer hot→warm ou consigner hot_entries_consulted."))
    return findings


def check_ghost_index(entities: list[Entity], data: dict[str, Any]) -> list[Finding]:
    tiers = data.get("tiers")
    tier_ids = set()
    if isinstance(tiers, dict):
        for ids in tiers.values():
            tier_ids.update(as_refs(ids))
    findings: list[Finding] = []
    for entity in entities:
        if entity.collection != "ghosts" or not entity.value.get("ne_pas_reproposer"):
            continue
        if entity.id and tier_ids and entity.id not in tier_ids:
            findings.append(finding("WARNING", "W004", entity.path, f"{entity.id} contient ne_pas_reproposer mais n'est pas indexé en tiers.", "Ajouter le GHOST à hot/warm selon sa portée."))
    return findings


def check_debts(entities: list[Entity]) -> list[Finding]:
    findings: list[Finding] = []
    for entity in entities:
        if entity.collection not in {"dettes", "debts"}:
            continue
        if canonical_status(entity.value) == "ACTIVE" and not entity.value.get("plan_remboursement"):
            findings.append(finding("WARNING", "W005", entity.path, f"{entity.id or entity.path} ACTIVE sans plan_remboursement.", "Ajouter un plan de remboursement concret."))
    return findings


def check_scope_contradictions(entities: list[Entity]) -> list[Finding]:
    scoped = [entity for entity in entities if entity.collection in {"vaccins", "patterns"} and isinstance(entity.value.get("scope"), str)]
    findings: list[Finding] = []
    for left_index, left in enumerate(scoped):
        for right in scoped[left_index + 1:]:
            if left.value.get("scope") != right.value.get("scope"):
                continue
            if not looks_inverse(left.value, right.value):
                continue
            if has_causal_link(left.value, right.id) or has_causal_link(right.value, left.id):
                continue
            findings.append(
                finding(
                    "WARNING",
                    "W007",
                    f"{left.path} <-> {right.path}",
                    f"Règles potentiellement inverses dans le même scope: {left.id} / {right.id}.",
                    "Ajouter un lien causal explicite ou clarifier le scope.",
                )
            )
    return findings


def check_state_file(scribe_path: Path) -> list[Finding]:
    check = check_sync(scribe_path)
    findings: list[Finding] = []
    if check.missing:
        findings.append(
            finding(
                "WARNING",
                "W011",
                str(check.state_path),
                "state.json absent: risque de désynchronisation multi-agent.",
                "Initialiser avec `scribe sync --repair --agent <name> --type <type> --session <JOURNAL-ID>`.",
            )
        )
        return findings
    if check.hash_mismatch:
        findings.append(
            finding(
                "WARNING",
                "W012",
                str(check.state_path),
                "state.json ne correspond pas au hash du SCRIBE réel.",
                "Relire le SCRIBE puis réparer state.json avec `scribe sync --repair` si la modification est légitime.",
            )
        )
    if check.invalid_write_kind:
        findings.append(
            finding(
                "WARNING",
                "W013",
                str(check.state_path),
                f"write_kind invalide: {check.state.get('write_kind') if check.state else '-'}",
                f"Utiliser un write_kind fermé: {', '.join(sorted(WRITE_KINDS))}.",
            )
        )
    return findings
