from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scribe_doctor_checks import check_all
from scribe_doctor_model import Entity, as_refs
from scribe_doctor_report import has_errors
from scribe_lock import mutation_lock_guard
from scribe_recommendations import build_recommendations
from scribe_state import update_state_from_active_lock
from scribe_store import ScribeStore, compact_entity, edge_type_counts, entity_abstract, entity_title, load_scribe
from scribe_index import INDEX_VERSION, session_total, stale_warm_patterns_without_causal_source, warm_patterns_without_causal_source

VALID_TIERS = ("hot", "warm", "cold")


class CompactionPlan:
    def __init__(
        self,
        current: dict[str, list[str]],
        normalized: dict[str, list[str]],
        duplicates: list[str],
        orphans: list[str],
        added: list[str],
    ) -> None:
        self.current = current
        self.normalized = normalized
        self.duplicates = duplicates
        self.orphans = orphans
        self.added = added

    @property
    def changed(self) -> bool:
        return self.current != self.normalized

def cmd_compact(args: argparse.Namespace) -> int:
    store = load_scribe(Path(args.scribe))
    if has_errors(doctor_findings(store)):
        print("SCRIBE COMPACT: refusé, doctor signale des erreurs préexistantes.")
        return 1

    plan = build_compaction_plan(store)
    print("SCRIBE COMPACT")
    print(f"  file: {store.path}")
    print(f"  apply: {bool(args.apply)}")
    for tier in VALID_TIERS:
        before = len(plan.current[tier])
        after = len(plan.normalized[tier])
        print(f"  tier.{tier}: {before} -> {after}")
    print(f"  duplicate refs removed: {len(plan.duplicates)}")
    print(f"  orphan refs removed: {len(plan.orphans)}")
    print(f"  entity tier refs added: {len(plan.added)}")

    if not plan.changed:
        print("  verdict: already compact")
        return 0
    if not args.apply:
        print("  verdict: changes available; rerun with --apply to rewrite only the tiers registry.")
        return 0

    lock_code = mutation_lock_guard()
    if lock_code:
        return lock_code
    updated = replace_tiers_block(store.raw, plan.normalized)
    return write_validated_scribe(store, updated, "compact", ["tiers", *plan.added], "tier_rebalance")

def cmd_promote(args: argparse.Namespace) -> int:
    target_tier = args.tier.lower()
    store = load_scribe(Path(args.scribe))
    entity = store.by_id(args.entity_id)
    if entity is None:
        return print_missing(store, args.entity_id)
    if has_errors(doctor_findings(store)):
        print("SCRIBE PROMOTE: refusé, doctor signale des erreurs préexistantes.")
        return 1

    current = normalized_current_tiers(store)
    updated_tiers = {tier: [item for item in ids if item != args.entity_id] for tier, ids in current.items()}
    updated_tiers[target_tier].append(args.entity_id)
    updated_tiers = normalize_tiers(updated_tiers, set(store.registry))

    updated = replace_tiers_block(store.raw, updated_tiers)
    updated = replace_entity_tier(updated, args.entity_id, target_tier)

    print("SCRIBE PROMOTE")
    print(f"  id: {args.entity_id}")
    print(f"  target tier: {target_tier}")
    print(f"  dry-run: {bool(args.dry_run)}")
    if args.dry_run:
        print("  verdict: patch calculé, fichier inchangé.")
        return 0
    lock_code = mutation_lock_guard()
    if lock_code:
        return lock_code
    return write_validated_scribe(store, updated, "promote", [args.entity_id, "tiers"], "tier_rebalance")

def cmd_export(args: argparse.Namespace) -> int:
    store = load_scribe(Path(args.scribe))
    if args.format != "json":
        print(f"Format non supporté: {args.format}")
        return 2

    payload = export_payload(store, include_values=args.include_values)
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=json_default)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content + "\n", encoding="utf-8")
        print(f"SCRIBE EXPORT: wrote {output_path}")
    else:
        print(content)
    return 0

def doctor_findings(store: ScribeStore):
    findings = store.findings[:]
    if store.data:
        findings.extend(check_all(store.data, store.raw, store.entities, store.registry, store.path))
    return findings


def build_compaction_plan(store: ScribeStore) -> CompactionPlan:
    known_ids = set(store.registry)
    current = normalized_current_tiers(store)
    duplicates = duplicate_tier_refs(current)
    orphans = [entity_id for ids in current.values() for entity_id in ids if entity_id not in known_ids]
    desired = {tier: ids[:] for tier, ids in current.items()}
    added: list[str] = []
    for entity in store.entities:
        entity_id = entity.id
        tier = str(entity.value.get("tier", "")).lower()
        if entity_id and tier in VALID_TIERS and entity_id not in tier_membership(desired):
            desired[tier].append(entity_id)
            added.append(entity_id)
    normalized = normalize_tiers(desired, known_ids)
    return CompactionPlan(current, normalized, duplicates, orphans, added)


def normalized_current_tiers(store: ScribeStore) -> dict[str, list[str]]:
    tiers = store.data.get("tiers", {}) if isinstance(store.data.get("tiers"), dict) else {}
    return {tier: as_refs(tiers.get(tier)) for tier in VALID_TIERS}


def duplicate_tier_refs(tiers: dict[str, list[str]]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for entity_id in [item for ids in tiers.values() for item in ids]:
        if entity_id in seen:
            duplicates.append(entity_id)
        seen.add(entity_id)
    return duplicates


def tier_membership(tiers: dict[str, list[str]]) -> set[str]:
    return {entity_id for ids in tiers.values() for entity_id in ids}


def normalize_tiers(tiers: dict[str, list[str]], known_ids: set[str]) -> dict[str, list[str]]:
    claimed: set[str] = set()
    normalized: dict[str, list[str]] = {}
    for tier in VALID_TIERS:
        normalized[tier] = []
        for entity_id in tiers.get(tier, []):
            if entity_id in known_ids and entity_id not in claimed:
                normalized[tier].append(entity_id)
                claimed.add(entity_id)
    return normalized


def replace_tiers_block(raw: str, tiers: dict[str, list[str]]) -> str:
    lines = raw.splitlines(keepends=True)
    start = find_top_level_line(lines, "tiers")
    if start is None:
        insert_at = find_top_level_line(lines, "invariants") or 0
        return "".join(lines[:insert_at] + render_tiers_block(tiers) + lines[insert_at:])
    end = next_top_level_line(lines, start + 1)
    return "".join(lines[:start] + render_tiers_block(tiers) + lines[end:])


def render_tiers_block(tiers: dict[str, list[str]]) -> list[str]:
    rendered = ["tiers:\n"]
    for tier in VALID_TIERS:
        ids = tiers.get(tier, [])
        if ids:
            rendered.append(f"  {tier}:\n")
            rendered.extend(f'    - "{entity_id}"\n' for entity_id in ids)
        else:
            rendered.append(f"  {tier}: []\n")
    return rendered


def find_top_level_line(lines: list[str], key: str) -> int | None:
    prefix = f"{key}:"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            return index
    return None


def next_top_level_line(lines: list[str], start: int) -> int:
    for index in range(start, len(lines)):
        line = lines[index]
        if line and not line.startswith((" ", "\n")) and ":" in line:
            return index
    return len(lines)


def replace_entity_tier(raw: str, entity_id: str, tier: str) -> str:
    lines = raw.splitlines(keepends=True)
    id_index = find_entity_id_line(lines, entity_id)
    if id_index is None:
        raise ValueError(f"Cannot locate entity in SCRIBE text: {entity_id}")
    end = next_entity_boundary(lines, id_index + 1)
    for index in range(id_index + 1, end):
        if lines[index].startswith("    tier:"):
            lines[index] = f'    tier: "{tier}"\n'
            return "".join(lines)
    lines.insert(id_index + 1, f'    tier: "{tier}"\n')
    return "".join(lines)


def find_entity_id_line(lines: list[str], entity_id: str) -> int | None:
    quoted = {f'  - id: "{entity_id}"\n', f"  - id: '{entity_id}'\n", f"  - id: {entity_id}\n"}
    for index, line in enumerate(lines):
        if line in quoted:
            return index
    return None


def next_entity_boundary(lines: list[str], start: int) -> int:
    for index in range(start, len(lines)):
        line = lines[index]
        if line.startswith("  - id: ") or (line and not line.startswith((" ", "\n")) and ":" in line):
            return index
    return len(lines)


def write_validated_scribe(
    store: ScribeStore,
    updated: str,
    action: str,
    changed_ids: list[str] | None = None,
    write_kind: str = "memory_append",
) -> int:
    if updated == store.raw:
        print(f"  verdict: {action} produced no file change")
        return 0
    write_atomic(store.path, updated)
    post_store = load_scribe(store.path)
    findings = doctor_findings(post_store)
    if has_errors(findings):
        write_atomic(store.path, store.raw)
        print(f"  verdict: {action} rolled back, doctor found post-write errors.")
        return 1
    try:
        update_state_from_active_lock(store.path, changed_ids or [action], write_kind)
    except Exception as exc:  # noqa: BLE001 - state write failure must not leave memory half-coordinated.
        write_atomic(store.path, store.raw)
        print(f"  verdict: {action} rolled back, state update failed: {exc}")
        return 1
    print(f"  verdict: {action} applied")
    return 0


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def export_payload(store: ScribeStore, include_values: bool = False) -> dict[str, Any]:
    findings = doctor_findings(store)
    errors = sum(1 for item in findings if item.severity == "ERROR")
    warnings = sum(1 for item in findings if item.severity == "WARNING")
    from scribe_memory_eval import SURFACES, default_eval_cases, evaluate_store

    retrieval_quality = evaluate_store(store, default_eval_cases(store), SURFACES, top_k=5)
    edges = edge_type_counts(store.index.edge_types)
    payload = {
        "source": str(store.path),
        "schema_version": store.data.get("schema_version"),
        "summary": {
            "entities": len(store.entities),
            "ids": len(store.index.id_index),
            "doctor_errors": errors,
            "doctor_warnings": warnings,
            "causal_edges": edges["causal"],
            "edges": {"total": sum(edges.values()), **edges},
            "index_version": INDEX_VERSION,
            "index_complete": True,
            "source_line_count": len(store.raw.splitlines()),
            "warm_patterns_without_causal_source": len(warm_patterns_without_causal_source(store.entities)),
            "stale_warm_patterns_without_causal_source": len(stale_warm_patterns_without_causal_source(store.entities, session_total(store))),
        },
        "tiers": normalized_current_tiers(store),
        "doctor_findings": [finding_payload(item) for item in findings],
        "retrieval_quality": retrieval_quality,
        "entities": [export_entity(store, entity, include_values) for entity in store.entities],
    }
    payload["recommendations"] = build_recommendations(payload)
    return payload


def export_entity(store: ScribeStore, entity: Entity, include_values: bool) -> dict[str, Any]:
    outgoing, incoming = store.related(entity.id or "")
    payload: dict[str, Any] = {
        "id": entity.id,
        "collection": entity.collection,
        "tier": entity.value.get("tier"),
        "status": entity.value.get("status", entity.value.get("statut")),
        "title": entity_title(entity),
        "abstract": entity_abstract(entity),
        "outgoing": [item.id for item in outgoing if item.id],
        "incoming": [item.id for item in incoming if item.id],
    }
    if include_values:
        payload["value"] = entity.value
    return payload


def json_default(value: Any) -> str:
    return str(value)


def finding_payload(item: Any) -> dict[str, str]:
    return {
        "severity": str(getattr(item, "severity", "")),
        "code": str(getattr(item, "code", "")),
        "location": str(getattr(item, "location", "")),
        "message": str(getattr(item, "message", "")),
        "suggestion": str(getattr(item, "suggestion", "")),
    }


def print_missing(store: ScribeStore, entity_id: str) -> int:
    print(f"ID introuvable: {entity_id}")
    alternatives = store.search(entity_id, limit=5)
    if alternatives:
        print("Suggestions:")
        for score, doc in alternatives:
            print(f"  - score={score} {compact_entity(doc.entity)}")
    return 2
