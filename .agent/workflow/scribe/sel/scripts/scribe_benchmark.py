#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from scribe_memory_context import ranked_hot_entities
from scribe_store import load_scribe


DEFAULT_ENTITY_COUNTS = (1000, 10000)
DEFAULT_QUERIES = (
    "hot tier pressure degradation",
    "semantic retrieval friction",
    "dashboard memory benchmark",
    "archive retention cold entries",
    "doctor validation regression",
)


def parse_entity_counts(raw: str | None) -> list[int]:
    if not raw:
        return list(DEFAULT_ENTITY_COUNTS)
    counts: list[int] = []
    for part in raw.split(","):
        count = int(part.strip())
        if count <= 0:
            raise argparse.ArgumentTypeError("entity counts must be positive")
        counts.append(count)
    return counts


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[index]


def generate_scribe(entity_count: int) -> str:
    patterns = max(1, int(entity_count * 0.5))
    vaccins = max(1, int(entity_count * 0.15))
    debts = max(1, int(entity_count * 0.05))
    journal = max(1, entity_count - patterns - vaccins - debts)
    hot_ids = [f"PAT-{index:06d}" for index in range(min(32, patterns))]
    warm_ids = [f"VAC-{index:06d}" for index in range(min(32, vaccins))]
    lines = ['schema_version: "TENOR_SCRIBE_BENCH_v1"\n', 'tiers:\n']
    lines.append(render_ref_list("hot", hot_ids))
    lines.append(render_ref_list("warm", warm_ids))
    lines.append('  cold: []\n')
    lines.append('patterns:\n')
    for index in range(patterns):
        tier = "hot" if index < len(hot_ids) else "warm"
        source = max(0, index - 1)
        lines.append(
            f'  - id: "PAT-{index:06d}"\n'
            f'    tier: "{tier}"\n'
            '    status: "ACTIVE"\n'
            f'    date: "2026-05-{(index % 24) + 1:02d}"\n'
            f'    titre: "Synthetic retrieval pattern {index}"\n'
            f'    l0_abstract: "Benchmark memory for semantic retrieval, hot tier pressure, dashboard latency, and agent friction {index}."\n'
            '    liens_causaux:\n'
            f'      source: "JOURNAL-{source:06d}"\n'
        )
    lines.append('vaccins:\n')
    for index in range(vaccins):
        tier = "warm" if index < len(warm_ids) else "cold"
        lines.append(
            f'  - id: "VAC-{index:06d}"\n'
            f'    tier: "{tier}"\n'
            '    status: "ACTIVE"\n'
            f'    titre: "Synthetic vaccine {index}"\n'
            '    l0_abstract: "Prevents regression in local retrieval and SCRIBE maintenance."\n'
            '    virus: "Unbounded memory scans create agent latency."\n'
            '    antidote: "Use bounded local retrieval and benchmark it."\n'
        )
    lines.append('debts:\n')
    for index in range(debts):
        lines.append(
            f'  - id: "DEBT-{index:06d}"\n'
            '    tier: "warm"\n'
            '    status: "ACTIVE"\n'
            '    severite: "MEDIUM"\n'
            f'    titre: "Synthetic debt {index}"\n'
            '    l0_abstract: "Represents deferred hardening for benchmark load."\n'
        )
    lines.append('journal:\n')
    for index in range(journal):
        lines.append(
            f'  - id: "JOURNAL-{index:06d}"\n'
            '    date: "2026-05-24"\n'
            '    mode: "STANDARD"\n'
            f'    hot_entries_consulted: ["PAT-{index % patterns:06d}"]\n'
            f'    l0_abstract: "Synthetic journal entry {index} for benchmark retrieval."\n'
            '    pourquoi: "Exercise local parse, index, causal edges, and query scoring under load."\n'
        )
    lines.append('metrics:\n')
    lines.append(f'  sessions_total: {journal}\n')
    return "".join(lines)


def render_ref_list(name: str, refs: list[str]) -> str:
    if not refs:
        return f"  {name}: []\n"
    quoted = ", ".join(f'"{item}"' for item in refs)
    return f"  {name}: [{quoted}]\n"


def run_benchmark(entity_count: int, query_count: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"synthetic-{entity_count}.scribe"
        content = generate_scribe(entity_count)
        path.write_text(content, encoding="utf-8")
        load_start = time.perf_counter()
        store = load_scribe(path)
        load_ms = (time.perf_counter() - load_start) * 1000
        hot_start = time.perf_counter()
        ranked_hot_entities(store)
        hot_rank_ms = (time.perf_counter() - hot_start) * 1000
        query_times: list[float] = []
        queries = list(DEFAULT_QUERIES)
        for index in range(query_count):
            query = queries[index % len(queries)]
            start = time.perf_counter()
            store.search(query, limit=8)
            query_times.append((time.perf_counter() - start) * 1000)
        return {
            "entities": entity_count,
            "bytes": len(content.encode("utf-8")),
            "load_ms": round(load_ms, 3),
            "hot_rank_ms": round(hot_rank_ms, 3),
            "query_count": query_count,
            "query_p50_ms": round(percentile(query_times, 0.5), 3),
            "query_p95_ms": round(percentile(query_times, 0.95), 3),
            "query_max_ms": round(max(query_times, default=0.0), 3),
        }


def print_text(results: list[dict[str, Any]]) -> None:
    print("SCRIBE BENCHMARK")
    for result in results:
        print(
            f"  entities={result['entities']} bytes={result['bytes']} "
            f"load_ms={result['load_ms']} hot_rank_ms={result['hot_rank_ms']} "
            f"query_p50_ms={result['query_p50_ms']} query_p95_ms={result['query_p95_ms']} "
            f"query_max_ms={result['query_max_ms']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(prog="scribe benchmark", description="Run synthetic SCRIBE retrieval benchmarks.")
    parser.add_argument("--entities", help="Comma-separated entity counts. Defaults to 1000,10000.")
    parser.add_argument("--queries", type=int, default=20, help="Queries per entity count.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    counts = parse_entity_counts(args.entities)
    results = [run_benchmark(count, args.queries) for count in counts]
    if args.json:
        print(json.dumps({"results": results}, indent=2, sort_keys=True))
    else:
        print_text(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
