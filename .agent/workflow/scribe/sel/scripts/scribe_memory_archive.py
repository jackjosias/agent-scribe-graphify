from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from scribe_doctor_model import Entity, canonical_status, collect_entities, collect_registry, parse_yaml
from scribe_doctor_report import has_errors
from scribe_lock import mutation_lock_guard
from scribe_state import update_state_from_active_lock
from scribe_memory_admin import (
    VALID_TIERS,
    doctor_findings,
    json_default,
    next_entity_boundary,
    normalized_current_tiers,
    replace_tiers_block,
    write_atomic,
)
from scribe_store import ScribeStore, build_index, compact_entity, load_scribe


DEFAULT_ARCHIVE_PATH = Path("AGENT-MEMOIRE_ARCHIVE.yaml")
ARCHIVE_SKIP_ACTIVE_COLLECTIONS = {"debts", "dettes"}


@dataclass(frozen=True)
class ArchivePlan:
    tier: str
    candidates: list[Entity]
    skipped: list[tuple[str, str]]

    @property
    def candidate_ids(self) -> set[str]:
        return {entity.id for entity in self.candidates if entity.id}


def cmd_archive(args: argparse.Namespace) -> int:
    store = load_scribe(Path(args.scribe))
    if has_errors(doctor_findings(store)):
        print("SCRIBE ARCHIVE: refusé, doctor signale des erreurs préexistantes.")
        return 1

    plan = build_archive_plan(store, tier=args.tier)
    output_path = Path(args.output)
    print("SCRIBE ARCHIVE")
    print(f"  file: {store.path}")
    print(f"  output: {output_path}")
    print(f"  tier: {args.tier}")
    print(f"  apply: {bool(args.apply)}")
    print(f"  candidates: {len(plan.candidates)}")
    print(f"  skipped: {len(plan.skipped)}")
    for entity in plan.candidates[: args.limit]:
        print(f"    - {compact_entity(entity)}")
    if len(plan.candidates) > args.limit:
        print(f"    ... {len(plan.candidates) - args.limit} more")
    for entity_id, reason in plan.skipped:
        print(f"    skipped {entity_id}: {reason}")

    if not plan.candidates:
        print("  verdict: nothing to archive")
        return 0
    if not args.apply:
        print("  verdict: dry-run; rerun with --apply to write the archive and prune active SCRIBE blocks.")
        return 0
    lock_code = mutation_lock_guard()
    if lock_code:
        return lock_code
    return apply_archive_plan(store, output_path, plan)


def build_archive_plan(store: ScribeStore, tier: str) -> ArchivePlan:
    tier_ids = normalized_current_tiers(store).get(tier, [])
    candidates: list[Entity] = []
    skipped: list[tuple[str, str]] = []
    for entity_id in tier_ids:
        entity = store.by_id(entity_id)
        if entity is None:
            skipped.append((entity_id, "ID introuvable dans le SCRIBE actif"))
            continue
        if should_skip_archive(entity):
            skipped.append((entity_id, "entrée active non archivable automatiquement"))
            continue
        candidates.append(entity)
    return ArchivePlan(tier, candidates, skipped)


def should_skip_archive(entity: Entity) -> bool:
    return entity.collection in ARCHIVE_SKIP_ACTIVE_COLLECTIONS and canonical_status(entity.value) == "ACTIVE"


def apply_archive_plan(store: ScribeStore, output_path: Path, plan: ArchivePlan) -> int:
    archive_before = output_path.read_text(encoding="utf-8") if output_path.exists() else None
    main_before = store.raw
    updated_main = remove_entity_blocks(remove_ids_from_tiers(main_before, plan.candidate_ids), plan.candidate_ids)
    updated_archive = append_archive_batch(archive_before, store.path, plan)
    archive_data, archive_findings = parse_yaml(updated_archive, output_path)
    if archive_data is None or has_errors(archive_findings):
        print("  verdict: archive aborted, generated archive YAML is invalid.")
        return 1

    try:
        write_atomic(output_path, updated_archive)
        write_atomic(store.path, updated_main)
        post_store = load_scribe(store.path)
        findings = doctor_findings(post_store)
        if has_errors(findings):
            restore_archive(output_path, archive_before)
            write_atomic(store.path, main_before)
            print("  verdict: archive rolled back, doctor found post-write errors.")
            return 1
        update_state_from_active_lock(store.path, ["tiers", *sorted(plan.candidate_ids)], "archive_apply")
    except Exception as exc:  # noqa: BLE001 - command must rollback best-effort.
        restore_archive(output_path, archive_before)
        write_atomic(store.path, main_before)
        print(f"  verdict: archive rolled back after error: {exc}")
        return 1

    print(f"  verdict: archived {len(plan.candidates)} entrie(s)")
    return 0


def remove_ids_from_tiers(raw: str, entity_ids: set[str]) -> str:
    store = load_scribe_from_text(raw)
    current = normalized_current_tiers(store)
    updated = {tier: [entity_id for entity_id in ids if entity_id not in entity_ids] for tier, ids in current.items()}
    return replace_tiers_block(raw, updated)


def load_scribe_from_text(raw: str) -> ScribeStore:
    tmp_path = Path("<memory>")
    data, findings = parse_yaml(raw, tmp_path)
    if data is None:
        data = {}
    entities = collect_entities(data)
    registry = collect_registry(data)
    index = build_index(entities, registry)
    return ScribeStore(tmp_path, raw, data, findings, entities, registry, index)


def remove_entity_blocks(raw: str, entity_ids: set[str]) -> str:
    lines = raw.splitlines(keepends=True)
    ranges: list[tuple[int, int]] = []
    for entity_id in entity_ids:
        start = find_entity_id_line(lines, entity_id)
        if start is not None:
            ranges.append((start, next_entity_boundary(lines, start + 1)))
    for start, end in sorted(ranges, reverse=True):
        del lines[start:end]
    return "".join(lines)


def find_entity_id_line(lines: list[str], entity_id: str) -> int | None:
    quoted = {f'  - id: "{entity_id}"\n', f"  - id: '{entity_id}'\n", f"  - id: {entity_id}\n"}
    for index, line in enumerate(lines):
        if line in quoted:
            return index
    return None


def append_archive_batch(existing: str | None, source_path: Path, plan: ArchivePlan) -> str:
    content = existing or archive_header()
    batch = render_archive_batch(source_path, plan)
    return content.rstrip() + "\n" + batch


def archive_header() -> str:
    return (
        'schema_version: "TENOR_SCRIBE_ARCHIVE_v1"\n'
        f'created_at: "{date.today().isoformat()}"\n'
        "batches:\n"
    )


def render_archive_batch(source_path: Path, plan: ArchivePlan) -> str:
    lines = [
        f'  - archived_at: "{date.today().isoformat()}"\n',
        f'    source: "{source_path}"\n',
        f'    tier: "{plan.tier}"\n',
        "    entries:\n",
    ]
    for entity in plan.candidates:
        lines.extend(render_archive_entry(entity))
    return "".join(lines)


def render_archive_entry(entity: Entity) -> list[str]:
    value = json.dumps(entity.value, ensure_ascii=False, sort_keys=True, default=json_default)
    return [
        f'      - id: "{entity.id}"\n',
        f'        collection: "{entity.collection}"\n',
        f"        value: {value}\n",
    ]


def restore_archive(path: Path, previous: str | None) -> None:
    if previous is None:
        if path.exists():
            path.unlink()
        return
    write_atomic(path, previous)
