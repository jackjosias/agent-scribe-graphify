#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from scribe_memory_admin import VALID_TIERS, cmd_compact, cmd_export, cmd_promote
from scribe_memory_archive import DEFAULT_ARCHIVE_PATH, cmd_archive
from scribe_memory_context import cmd_context, ranked_hot_entities
from scribe_memory_dashboard import DEFAULT_DASHBOARD_DATA_PATH, DEFAULT_DASHBOARD_PATH, cmd_dashboard
from scribe_memory_eval import cmd_eval
from scribe_memory_tiers import DEFAULT_HOT_TARGET, DEFAULT_WARM_OVERFLOW, cmd_review_hot
from scribe_index import ensure_quick_index, search_index_entities
from scribe_store import ScribeStore, compact_entity, entity_abstract, entity_title, load_scribe


DEFAULT_LIMIT = 8
DEFAULT_HOT_LIMIT = 8
CHALLENGE_COLLECTIONS = {"scars", "vaccins", "patterns", "ghosts", "hypotheses", "debts", "dettes"}


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scribe",
        default="AGENT-MEMOIRE_PROJECT_STATUS.scribe",
        help="Path to the SCRIBE YAML file.",
    )


def print_entity(entity, verbose: bool = False) -> None:
    print(compact_entity(entity))
    title = entity_title(entity)
    abstract = entity_abstract(entity)
    if title:
        print(f"  titre: {title}")
    if abstract:
        print(f"  l0: {abstract}")
    for field in ("pourquoi", "virus", "antidote", "contexte", "plan_remboursement"):
        value = entity.value.get(field)
        if isinstance(value, str) and value:
            print(f"  {field}: {value}")
    if verbose:
        causal = entity.value.get("liens_causaux")
        if causal:
            print(f"  liens_causaux: {causal}")


def cmd_hot(args: argparse.Namespace) -> int:
    store = load_scribe(Path(args.scribe))
    hot_entities = ranked_hot_entities(store, args.topic)
    hot = hot_entities[: args.limit]
    suffix = "" if not args.topic else f" topic={args.topic}"
    print(f"SCRIBE HOT: {len(hot)}/{len(hot_entities)} entrées affichées{suffix}")
    for entity in hot:
        print()
        print_entity(entity)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    quick_index = ensure_quick_index(Path(args.scribe))
    payload = quick_index.payload
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    edges = summary.get("edges", {}) if isinstance(summary.get("edges"), dict) else {}
    tiers = payload.get("tiers", {}) if isinstance(payload.get("tiers"), dict) else {}
    by_collection = payload.get("collections", {}) if isinstance(payload.get("collections"), dict) else {}
    errors = int(summary.get("doctor_errors", 0) or 0)
    warnings = int(summary.get("doctor_warnings", 0) or 0)

    print("SCRIBE STATS")
    print(f"  file: {payload.get('source') or args.scribe}")
    print(f"  index: {quick_index.path} ({'rebuilt' if quick_index.rebuilt else 'fresh'})")
    print(f"  entities: {int(summary.get('entities', 0) or 0)}")
    print(f"  ids: {int(summary.get('ids', 0) or 0)}")
    print(f"  doctor: {errors} error(s), {warnings} warning(s)")
    print(f"  edges.total: {int(edges.get('total', 0) or 0)}")
    for edge_type in ("causal", "evidence", "consultation", "journal"):
        print(f"  edges.{edge_type}: {edges.get(edge_type, 0)}")
    for tier in ("hot", "warm", "cold"):
        print(f"  tier.{tier}: {len(tiers.get(tier, [])) if isinstance(tiers.get(tier, []), list) else 0}")
    print(f"  audit.warm_patterns_without_causal_source: {int(summary.get('warm_patterns_without_causal_source', 0) or 0)}")
    print(f"  audit.stale_warm_patterns_without_causal_source: {int(summary.get('stale_warm_patterns_without_causal_source', 0) or 0)}")
    print("  collections:")
    for collection, count in sorted(by_collection.items()):
        print(f"    - {collection}: {count}")
    return 1 if errors else 0


def cmd_explain(args: argparse.Namespace) -> int:
    store = load_scribe(Path(args.scribe))
    entity = store.by_id(args.entity_id)
    if entity is None:
        return print_missing(store, args.entity_id)
    print_entity(entity, verbose=True)
    outgoing, incoming = store.related(args.entity_id)
    print_related_summary(outgoing, incoming)
    return 0


def cmd_related(args: argparse.Namespace) -> int:
    store = load_scribe(Path(args.scribe))
    if store.by_id(args.entity_id) is None:
        return print_missing(store, args.entity_id)
    outgoing, incoming = store.related(args.entity_id)
    print(f"SCRIBE RELATED: {args.entity_id}")
    print_related_list("outgoing", outgoing)
    print_related_list("incoming", incoming)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    quick_index = ensure_quick_index(Path(args.scribe))
    results = search_index_entities(quick_index.payload, args.text, limit=args.limit)
    print(f"SCRIBE QUERY: {args.text}")
    print(f"  index: {quick_index.path} ({'rebuilt' if quick_index.rebuilt else 'fresh'})")
    if not results:
        print("  Aucun résultat causal local.")
        return 0
    for score, item in results:
        print()
        print(f"score={score} {compact_index_entity(item)}")
        if item.get("title"):
            print(f"  titre: {item['title']}")
        if item.get("abstract"):
            print(f"  l0: {item['abstract']}")
    return 0


def compact_index_entity(item: dict) -> str:
    return f"{item.get('id') or item.get('path')} [{item.get('collection')}] tier={item.get('tier', '-')} status={item.get('status') or '-'}"


def cmd_challenge(args: argparse.Namespace) -> int:
    store = load_scribe(Path(args.scribe))
    results = store.search(args.plan, limit=args.limit, collections=CHALLENGE_COLLECTIONS)
    print("SCRIBE CHALLENGE")
    print(f"  plan: {args.plan}")
    if not results:
        print("  verdict: PASS")
        print("  mémoire: aucun vaccin/scar/pattern/dette pertinent trouvé.")
        return 0

    severity = challenge_severity(results)
    print(f"  verdict: {severity}")
    print("  mémoire pertinente:")
    for score, doc in results:
        print(f"    - score={score} {compact_entity(doc.entity)}")
        if doc.abstract:
            print(f"      {doc.abstract}")
    print("  décision:")
    if severity == "BLOCK":
        print("    Corriger le plan avant exécution: au moins une mémoire causale active contredit ou bloque l'approche.")
    elif severity == "WARN":
        print("    Exécutable, mais appliquer les vaccins/patterns listés et surveiller les dettes.")
    else:
        print("    Exécutable: les mémoires trouvées sont informatives, pas bloquantes.")
    return 0


def challenge_severity(results) -> str:
    for _, doc in results:
        value = doc.entity.value
        if doc.entity.collection == "scars" and str(value.get("severite", "")).upper() in {"CRITICAL", "HIGH"}:
            return "BLOCK"
        if doc.entity.collection in {"debts", "dettes"} and str(value.get("severite", "")).upper() in {"CRITICAL", "HIGH"}:
            return "WARN"
        if doc.entity.collection == "vaccins" and str(value.get("tier", "")).lower() == "hot":
            return "WARN"
    return "PASS"


def print_missing(store: ScribeStore, entity_id: str) -> int:
    print(f"ID introuvable: {entity_id}")
    alternatives = store.search(entity_id, limit=5)
    if alternatives:
        print("Suggestions:")
        for score, doc in alternatives:
            print(f"  - score={score} {compact_entity(doc.entity)}")
    return 2


def print_related_summary(outgoing, incoming) -> None:
    if outgoing or incoming:
        print(f"  related: {len(outgoing)} outgoing, {len(incoming)} incoming")


def print_related_list(label: str, entities) -> None:
    print(f"  {label}: {len(entities)}")
    for entity in entities:
        print(f"    - {compact_entity(entity)}")
        abstract = entity_abstract(entity)
        if abstract:
            print(f"      {abstract}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scribe", description="Query the causal SCRIBE memory.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hot = subparsers.add_parser("hot", help="Print hot memory entries for immediate agent grounding.")
    add_common_args(hot)
    hot.add_argument("--limit", type=int, default=DEFAULT_HOT_LIMIT)
    hot.add_argument("--topic", help="Rank hot memories by relevance to this topic before recency.")
    hot.set_defaults(func=cmd_hot)

    context = subparsers.add_parser("context", help="Print a low-friction SCRIBE context pack for agents.")
    add_common_args(context)
    context.add_argument("--mode", default="quick", choices=("quick", "standard"))
    context.add_argument("--topic", help="Optional focus query for relevant causal memory.")
    context.add_argument("--limit", type=int, help="Override the hot memory limit for this context run.")
    context.add_argument("--topic-limit", type=int, help="Override the focused topic result limit.")
    context.add_argument("--format", default="text", choices=("text", "json"), help="Output format for agent context.")
    context.set_defaults(func=cmd_context)

    stats = subparsers.add_parser("stats", help="Print SCRIBE health and memory statistics.")
    add_common_args(stats)
    stats.set_defaults(func=cmd_stats)

    explain = subparsers.add_parser("explain", help="Explain one SCRIBE entity by ID.")
    add_common_args(explain)
    explain.add_argument("entity_id")
    explain.set_defaults(func=cmd_explain)

    related = subparsers.add_parser("related", help="Show causal neighbors for one SCRIBE entity.")
    add_common_args(related)
    related.add_argument("entity_id")
    related.set_defaults(func=cmd_related)

    query = subparsers.add_parser("query", help="Search the causal SCRIBE memory locally.")
    add_common_args(query)
    query.add_argument("text")
    query.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    query.set_defaults(func=cmd_query)

    challenge = subparsers.add_parser("challenge", help="Challenge a plan against SCRIBE memory.")
    add_common_args(challenge)
    challenge.add_argument("plan")
    challenge.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    challenge.set_defaults(func=cmd_challenge)

    eval_parser = subparsers.add_parser("eval", help="Measure local SCRIBE retrieval quality.")
    add_common_args(eval_parser)
    eval_parser.add_argument("--smoke", action="store_true", help="Run fixed real-world retrieval smoke cases.")
    eval_parser.add_argument("--query", help="Ad-hoc evaluation query.")
    eval_parser.add_argument("--expect", help="Comma-separated expected IDs for an ad-hoc query.")
    eval_parser.add_argument("--surface", default="all", choices=("all", "query", "hot", "context"))
    eval_parser.add_argument("--top-k", type=int, default=5)
    eval_parser.add_argument("--format", default="text", choices=("text", "json"))
    eval_parser.set_defaults(func=cmd_eval)

    compact = subparsers.add_parser("compact", help="Report or apply safe SCRIBE tier registry compaction.")
    add_common_args(compact)
    compact.add_argument("--apply", action="store_true", help="Rewrite only the root tiers registry when compaction is available.")
    compact.set_defaults(func=cmd_compact)

    review_hot = subparsers.add_parser("review-hot", help="Review hot-tier pressure and optionally demote overflow entries to warm.")
    add_common_args(review_hot)
    review_hot.add_argument("--target", type=int, default=DEFAULT_HOT_TARGET, help="Desired hot tier size before protected entries force expansion.")
    review_hot.add_argument("--warm-overflow", type=int, default=DEFAULT_WARM_OVERFLOW, help="Overflow entries to demote to warm before using cold.")
    review_hot.add_argument("--limit", type=int, default=12, help="Maximum demotion candidates to print.")
    review_hot.add_argument("--apply", action="store_true", help="Apply safe demotions to the SCRIBE after doctor validation.")
    review_hot.set_defaults(func=cmd_review_hot)

    promote = subparsers.add_parser("promote", help="Move one SCRIBE entity to hot, warm, or cold.")
    add_common_args(promote)
    promote.add_argument("entity_id")
    promote.add_argument("--tier", required=True, choices=VALID_TIERS)
    promote.add_argument("--dry-run", action="store_true", help="Preview the promotion without writing the SCRIBE.")
    promote.set_defaults(func=cmd_promote)

    export = subparsers.add_parser("export", help="Export indexed SCRIBE memory for external tools.")
    add_common_args(export)
    export.add_argument("--format", default="json", choices=("json",))
    export.add_argument("--output", help="Write export to this file instead of stdout.")
    export.add_argument("--include-values", action="store_true", help="Include full entity YAML values in the export.")
    export.set_defaults(func=cmd_export)

    archive = subparsers.add_parser("archive", help="Archive cold SCRIBE entries into AGENT-MEMOIRE_ARCHIVE.yaml.")
    add_common_args(archive)
    archive.add_argument("--apply", action="store_true", help="Write the archive and prune archived entries from the active SCRIBE.")
    archive.add_argument("--output", default=str(DEFAULT_ARCHIVE_PATH), help="Archive YAML output path.")
    archive.add_argument("--tier", default="cold", choices=VALID_TIERS, help="Tier to archive. Defaults to cold.")
    archive.add_argument("--limit", type=int, default=20, help="Maximum candidate rows to print.")
    archive.set_defaults(func=cmd_archive)

    dashboard = subparsers.add_parser("dashboard", help="Generate a static HTML dashboard from the indexed SCRIBE memory.")
    add_common_args(dashboard)
    dashboard.add_argument("--output", default=str(DEFAULT_DASHBOARD_PATH), help="Dashboard HTML output path.")
    dashboard.add_argument("--data-output", default=str(DEFAULT_DASHBOARD_DATA_PATH), help="Dashboard JSON data output path.")
    dashboard.add_argument("--no-data", action="store_true", help="Do not write a separate JSON data file.")
    dashboard.add_argument("--include-values", action="store_true", help="Include full entity YAML values in the dashboard data payload.")
    dashboard.add_argument("--serve", action="store_true", help="Serve a lightweight live dashboard that reloads when the SCRIBE file changes.")
    dashboard.add_argument("--host", default="127.0.0.1", help="Host for --serve. Defaults to localhost only.")
    dashboard.add_argument("--port", type=int, default=8765, help="Port for --serve. Use 0 to pick a free port.")
    dashboard.add_argument("--poll-interval-ms", type=int, default=2000, help="Server-side file watch interval for --serve SSE live reload. Browser fallback polls no faster than 30000ms. Minimum 1000ms.")
    dashboard.set_defaults(func=cmd_dashboard)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
