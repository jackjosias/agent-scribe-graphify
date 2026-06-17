from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scribe_doctor_model import as_refs
from scribe_memory_context import entity_payload, ranked_hot_entities
from scribe_store import ScribeStore, entity_title, load_scribe


SURFACES = ("query", "hot", "context")
METRIC_DEPTHS = (1, 3, 5)


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    query: str
    expected_ids: list[str]


SMOKE_CASES = (
    EvalCase("smoke-graphify-dual-noise", "graphify application bundle graph confusion", ["PAT-024"]),
    EvalCase("smoke-portable-clean", "portable bootstrap clean generated noise", ["PAT-023"]),
    EvalCase("smoke-multi-agent-sync", "lock multi agent stale sync repair writer", ["GHOST-002", "GHOST-003"]),
    EvalCase("smoke-legacy-next-lint", "old legacy next lint", ["VAC-001"]),
)


def cmd_eval(args: argparse.Namespace) -> int:
    store = load_scribe(Path(args.scribe))
    cases = smoke_eval_cases() if args.smoke else ad_hoc_cases(args) if args.query else default_eval_cases(store)
    surfaces = selected_surfaces(args)
    payload = evaluate_store(store, cases, surfaces, args.top_k)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=json_default))
    else:
        print_eval_text(payload)
    return 0 if payload["summary"]["failed_cases"] == 0 else 1


def smoke_eval_cases() -> list[EvalCase]:
    return list(SMOKE_CASES)


def selected_surfaces(args: argparse.Namespace) -> tuple[str, ...]:
    if args.smoke and args.surface == "all":
        return ("query",)
    return SURFACES if args.surface == "all" else (args.surface,)


def ad_hoc_cases(args: argparse.Namespace) -> list[EvalCase]:
    expected = parse_expected(args.expect)
    if not expected:
        raise SystemExit("--expect is required when --query is provided")
    return [EvalCase("adhoc", args.query, expected)]


def default_eval_cases(store: ScribeStore) -> list[EvalCase]:
    configured = configured_eval_cases(store)
    if configured:
        return configured
    cases: list[EvalCase] = []
    for entity in ranked_hot_entities(store)[:5]:
        if entity.id:
            cases.append(EvalCase(f"smoke-{entity.id}", entity_title(entity), [entity.id]))
    return cases


def configured_eval_cases(store: ScribeStore) -> list[EvalCase]:
    raw_cases = store.data.get("retrieval_evals")
    if not isinstance(raw_cases, list):
        return []
    cases: list[EvalCase] = []
    for index, raw in enumerate(raw_cases, start=1):
        if not isinstance(raw, dict):
            continue
        query = raw.get("query")
        expected = parse_expected(raw.get("expect", raw.get("expected_ids")))
        if isinstance(query, str) and query and expected:
            cases.append(EvalCase(str(raw.get("id") or f"eval-{index}"), query, expected))
    return cases


def parse_expected(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return as_refs(value)


def evaluate_store(
    store: ScribeStore,
    cases: list[EvalCase],
    surfaces: tuple[str, ...] = SURFACES,
    top_k: int = 5,
) -> dict[str, Any]:
    results = [evaluate_case(store, case, surfaces, top_k) for case in cases]
    return {
        "source": str(store.path),
        "top_k": top_k,
        "surfaces": list(surfaces),
        "summary": summarize_results(results, surfaces),
        "cases": results,
    }


def evaluate_case(store: ScribeStore, case: EvalCase, surfaces: tuple[str, ...], top_k: int) -> dict[str, Any]:
    surface_results = {surface: evaluate_surface(store, case, surface, top_k) for surface in surfaces}
    return {
        "id": case.case_id,
        "query": case.query,
        "expected_ids": case.expected_ids,
        "surfaces": surface_results,
        "passed": all(result["passed"] for result in surface_results.values()),
    }


def evaluate_surface(store: ScribeStore, case: EvalCase, surface: str, top_k: int) -> dict[str, Any]:
    retrieved = retrieve_ids(store, case.query, surface, top_k)
    expected = set(case.expected_ids)
    metrics = {f"precision_at_{depth}": precision_at(retrieved, expected, depth) for depth in METRIC_DEPTHS}
    metrics.update({f"hit_at_{depth}": bool(expected & set(retrieved[:depth])) for depth in METRIC_DEPTHS})
    return {
        "retrieved_ids": retrieved,
        "found_ids": [entity_id for entity_id in retrieved if entity_id in expected],
        "missing_ids": [entity_id for entity_id in case.expected_ids if entity_id not in set(retrieved[:top_k])],
        "passed": expected.issubset(set(retrieved[:top_k])),
        "metrics": metrics,
    }


def retrieve_ids(store: ScribeStore, query: str, surface: str, top_k: int) -> list[str]:
    if surface == "query":
        return [doc.entity.id or doc.entity.path for _, doc in store.search(query, limit=top_k)]
    if surface == "hot":
        return [entity.id or entity.path for entity in ranked_hot_entities(store, query)[:top_k]]
    if surface == "context":
        return dedupe([*retrieve_ids(store, query, "hot", top_k), *retrieve_ids(store, query, "query", top_k)])[:top_k]
    raise ValueError(f"Unknown evaluation surface: {surface}")


def precision_at(retrieved: list[str], expected: set[str], depth: int) -> float:
    if depth <= 0:
        return 0.0
    return round(len(expected & set(retrieved[:depth])) / depth, 4)


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            unique.append(item)
            seen.add(item)
    return unique


def summarize_results(results: list[dict[str, Any]], surfaces: tuple[str, ...]) -> dict[str, Any]:
    total_surface_cases = len(results) * len(surfaces)
    failed_cases = sum(1 for result in results if not result["passed"])
    surface_summary = {surface: summarize_surface(results, surface) for surface in surfaces}
    passed_surface_cases = sum(summary["passed"] for summary in surface_summary.values())
    return {
        "cases": len(results),
        "surface_cases": total_surface_cases,
        "passed_surface_cases": passed_surface_cases,
        "failed_cases": failed_cases,
        "pass_rate": round(passed_surface_cases / total_surface_cases, 4) if total_surface_cases else 1.0,
        "by_surface": surface_summary,
    }


def summarize_surface(results: list[dict[str, Any]], surface: str) -> dict[str, Any]:
    items = [result["surfaces"][surface] for result in results if surface in result["surfaces"]]
    return {
        "cases": len(items),
        "passed": sum(1 for item in items if item["passed"]),
        "precision_at_1": average_metric(items, "precision_at_1"),
        "precision_at_3": average_metric(items, "precision_at_3"),
        "precision_at_5": average_metric(items, "precision_at_5"),
    }


def average_metric(items: list[dict[str, Any]], metric: str) -> float:
    if not items:
        return 0.0
    return round(sum(float(item["metrics"][metric]) for item in items) / len(items), 4)


def print_eval_text(payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    print("SCRIBE EVAL")
    print(f"  source: {payload['source']}")
    print(f"  cases: {summary['cases']} | surfaces: {', '.join(payload['surfaces'])}")
    print(f"  pass_rate: {summary['pass_rate']:.2%}")
    for surface, item in summary["by_surface"].items():
        print(
            f"  {surface}: {item['passed']}/{item['cases']} "
            f"p@1={item['precision_at_1']:.2f} p@3={item['precision_at_3']:.2f} p@5={item['precision_at_5']:.2f}"
        )
    for case in payload["cases"]:
        marker = "PASS" if case["passed"] else "FAIL"
        print(f"  - {marker} {case['id']}: {case['query']}")
        for surface, result in case["surfaces"].items():
            print(f"    {surface}: {compact_ids(result['retrieved_ids'])}")
            if result["missing_ids"]:
                print(f"      missing: {', '.join(result['missing_ids'])}")


def compact_ids(ids: list[str]) -> str:
    return ", ".join(ids) if ids else "-"


def json_default(value: Any) -> str:
    return str(value)
