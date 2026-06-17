from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from scribe_doctor_report import has_errors
from scribe_lock import mutation_lock_guard
from scribe_memory_admin import (
    doctor_findings,
    normalize_tiers,
    normalized_current_tiers,
    replace_entity_tier,
    replace_tiers_block,
    write_validated_scribe,
)
from scribe_memory_context import ranked_hot_entities
from scribe_store import ScribeStore, compact_entity, entity_title, load_scribe


DEFAULT_HOT_TARGET = 12
DEFAULT_WARM_OVERFLOW = 8
HOT_PROTECTED_COLLECTIONS = {"scars", "vaccins"}
HOT_PROTECTED_DEBT_SEVERITIES = {"CRITICAL", "HIGH"}


@dataclass(frozen=True)
class HotReviewPlan:
    current_count: int
    target: int
    warm_overflow: int
    effective_target: int
    keep_ids: list[str]
    warm_ids: list[str]
    cold_ids: list[str]
    protected_ids: list[str]

    @property
    def changed(self) -> bool:
        return bool(self.warm_ids or self.cold_ids)


def cmd_review_hot(args: argparse.Namespace) -> int:
    store = load_scribe(Path(args.scribe))
    if has_errors(doctor_findings(store)):
        print("SCRIBE HOT REVIEW: refusé, doctor signale des erreurs préexistantes.")
        return 1

    plan = build_hot_review_plan(store, args.target, args.warm_overflow)
    print("SCRIBE HOT REVIEW")
    print(f"  file: {store.path}")
    print(f"  apply: {bool(args.apply)}")
    print(f"  hot: {plan.current_count} -> {plan.effective_target}")
    print(f"  protected: {len(plan.protected_ids)}")
    print(f"  demote_to_warm: {len(plan.warm_ids)}")
    print(f"  demote_to_cold: {len(plan.cold_ids)}")
    print_candidate_list(store, "warm", plan.warm_ids, args.limit)
    print_candidate_list(store, "cold", plan.cold_ids, args.limit)

    if not plan.changed:
        print("  verdict: hot tier within pressure budget")
        return 0
    if not args.apply:
        print("  verdict: pressure detected; rerun with --apply to rebalance overflow entries to warm/cold.")
        return 0

    lock_code = mutation_lock_guard()
    if lock_code:
        return lock_code
    updated = apply_hot_review_plan(store, plan)
    return write_validated_scribe(store, updated, "review-hot", ["tiers", *plan.warm_ids, *plan.cold_ids], "tier_rebalance")


def build_hot_review_plan(
    store: ScribeStore,
    target: int = DEFAULT_HOT_TARGET,
    warm_overflow: int = DEFAULT_WARM_OVERFLOW,
) -> HotReviewPlan:
    ranked = ranked_hot_entities(store)
    ranked_ids = [entity.id for entity in ranked if entity.id]
    protected_ids = [entity.id for entity in ranked if entity.id and is_hot_protected(entity)]
    effective_target = max(target, len(protected_ids))
    keep: list[str] = []
    for entity_id in ranked_ids:
        if entity_id in protected_ids or len(keep) < effective_target:
            keep.append(entity_id)
    demote = [entity_id for entity_id in ranked_ids if entity_id not in keep]
    return HotReviewPlan(
        current_count=len(ranked_ids),
        target=target,
        warm_overflow=warm_overflow,
        effective_target=len(keep),
        keep_ids=keep,
        warm_ids=demote[:warm_overflow],
        cold_ids=demote[warm_overflow:],
        protected_ids=protected_ids,
    )


def is_hot_protected(entity) -> bool:
    status = str(entity.value.get("status", entity.value.get("statut", ""))).upper()
    if status != "ACTIVE":
        return False
    if entity.collection in HOT_PROTECTED_COLLECTIONS:
        return True
    if entity.collection in {"debts", "dettes"}:
        severity = str(entity.value.get("severite", entity.value.get("severity", ""))).upper()
        return severity in HOT_PROTECTED_DEBT_SEVERITIES
    return False


def apply_hot_review_plan(store: ScribeStore, plan: HotReviewPlan) -> str:
    current = normalized_current_tiers(store)
    updated = {tier: ids[:] for tier, ids in current.items()}
    for entity_id in [*plan.warm_ids, *plan.cold_ids]:
        updated["hot"] = [item for item in updated["hot"] if item != entity_id]
    for entity_id in plan.warm_ids:
        if entity_id not in updated["warm"]:
            updated["warm"].append(entity_id)
    for entity_id in plan.cold_ids:
        updated["warm"] = [item for item in updated["warm"] if item != entity_id]
        if entity_id not in updated["cold"]:
            updated["cold"].append(entity_id)
    updated = normalize_tiers(updated, set(store.registry))
    raw = replace_tiers_block(store.raw, updated)
    for entity_id in plan.warm_ids:
        raw = replace_entity_tier(raw, entity_id, "warm")
    for entity_id in plan.cold_ids:
        raw = replace_entity_tier(raw, entity_id, "cold")
    return raw


def summarize_hot_pressure(store: ScribeStore) -> dict[str, int]:
    plan = build_hot_review_plan(store)
    return {
        "current": plan.current_count,
        "target": plan.target,
        "warm_overflow": plan.warm_overflow,
        "effective_target": plan.effective_target,
        "protected": len(plan.protected_ids),
        "demote_to_warm": len(plan.warm_ids),
        "demote_to_cold": len(plan.cold_ids),
    }


def print_candidate_list(store: ScribeStore, target_tier: str, entity_ids: list[str], limit: int) -> None:
    for entity_id in entity_ids[:limit]:
        entity = store.by_id(entity_id)
        label = entity_title(entity) if entity is not None else entity_id
        print(f"    - {target_tier}: {entity_id}: {label}")
    if len(entity_ids) > limit:
        print(f"    ... {len(entity_ids) - limit} more {target_tier} candidate(s)")
