#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

from scribe_index import ensure_quick_index, rank_index_entities, topic_matches


HOT_LIMIT = 5
TOPIC_LIMIT = 3
DEBT_LIMIT = 1


class QuickArgs:
    def __init__(self) -> None:
        self.scribe = "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
        self.mode = "quick"
        self.topic: str | None = None
        self.limit: int | None = None
        self.topic_limit: int | None = None
        self.format = "text"


def main() -> int:
    args = parse_args(sys.argv[1:])
    quick_index = ensure_quick_index(Path(args.scribe))
    hot_limit = args.limit if args.limit is not None else HOT_LIMIT
    topic_limit = args.topic_limit if args.topic_limit is not None else TOPIC_LIMIT
    if args.format == "json":
        print(json.dumps(context_payload(quick_index, hot_limit, args.topic, topic_limit), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print_context_text(quick_index, hot_limit, args.topic, topic_limit)
    return 0


def parse_args(argv: list[str]) -> QuickArgs:
    args = QuickArgs()
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {"-h", "--help"}:
            print_usage()
            raise SystemExit(0)
        if arg in {"--scribe", "--mode", "--topic", "--limit", "--topic-limit", "--format"}:
            if index + 1 >= len(argv):
                raise SystemExit(f"{arg} requires a value")
            apply_option(args, arg, argv[index + 1])
            index += 2
            continue
        if arg.startswith("--") and "=" in arg:
            key, value = arg.split("=", 1)
            apply_option(args, key, value)
            index += 1
            continue
        raise SystemExit(f"Unknown scribe context option: {arg}")
    if args.mode != "quick":
        raise SystemExit("scribe_context_quick only supports --mode quick")
    if args.format not in {"text", "json"}:
        raise SystemExit("--format must be text or json")
    return args


def apply_option(args: QuickArgs, key: str, value: str) -> None:
    if key == "--scribe":
        args.scribe = value
    elif key == "--mode":
        args.mode = value
    elif key == "--topic":
        args.topic = value
    elif key == "--limit":
        args.limit = int(value)
    elif key == "--topic-limit":
        args.topic_limit = int(value)
    elif key == "--format":
        args.format = value
    else:
        raise SystemExit(f"Unknown scribe context option: {key}")


def print_usage() -> None:
    print("Usage: scribe context [--mode quick] [--scribe PATH] [--topic TEXT] [--limit N] [--topic-limit N] [--format text|json]")


def context_payload(quick_index, hot_limit: int, topic: str | None, topic_limit: int) -> dict:
    payload = quick_index.payload
    hot_ranked = rank_index_entities(payload.get("hot_entities", []), topic)
    topic_ranked = topic_matches(hot_ranked)[:topic_limit] if topic else []
    return {
        "mode": "quick",
        "source": str(payload.get("source", "")),
        "index": {"path": str(quick_index.path), "rebuilt": quick_index.rebuilt},
        "policy": "minimal context; use doctor/challenge only when risk escalates.",
        "doctor": {"included": False, "errors": 0, "warnings": 0},
        "hot_label": "hot" if not topic else "hot_by_topic",
        "hot": [entity_payload(item) for _, item in hot_ranked[:hot_limit]],
        "topic": topic,
        "topic_results": [entity_payload(item, score) for score, item in topic_ranked],
        "active_debts": [entity_payload(item) for item in payload.get("active_debts", [])[:DEBT_LIMIT]],
    }


def print_context_text(quick_index, hot_limit: int, topic: str | None, topic_limit: int) -> None:
    payload = quick_index.payload
    hot_ranked = rank_index_entities(payload.get("hot_entities", []), topic)
    hot_entities = [item for _, item in hot_ranked]
    label = "hot" if not topic else "hot_by_topic"
    print("SCRIBE CONTEXT [quick]")
    print(f"  source: {payload.get('source') or '-'}")
    print("  policy: minimal context; use doctor/challenge only when risk escalates.")
    print("  doctor: skipped for quick mode")
    print(f"  index: {quick_index.path} ({'rebuilt' if quick_index.rebuilt else 'fresh'})")
    print(f"  {label}: {min(hot_limit, len(hot_entities))}/{len(hot_entities)}")
    print_entities(hot_entities[:hot_limit])
    if topic:
        print_topic(hot_ranked, topic, topic_limit)
    debts = payload.get("active_debts", [])
    print(f"  active_debts: {min(DEBT_LIMIT, len(debts))}/{len(debts)}")
    print_entities(debts[:DEBT_LIMIT], indent="    ")


def print_topic(hot_ranked: list[tuple[int, dict]], topic: str, limit: int) -> None:
    print(f"  topic: {topic}")
    matches = topic_matches(hot_ranked)[:limit]
    if not matches:
        print("    - no hot causal match")
        return
    for score, item in matches:
        print(f"    - score={score} {compact_entity(item)} :: {short_text(str(item.get('title') or item.get('id') or ''), width=90)}")
        abstract = short_text(str(item.get("abstract") or ""))
        if abstract:
            print(f"      {abstract}")


def print_entities(entities: Iterable[dict], indent: str = "    ") -> None:
    printed = False
    for item in entities:
        printed = True
        print(f"{indent}- {compact_entity(item)} :: {short_text(str(item.get('title') or item.get('id') or ''), width=90)}")
        abstract = short_text(str(item.get("abstract") or ""))
        if abstract:
            print(f"{indent}  {abstract}")
    if not printed:
        print(f"{indent}- none")


def entity_payload(item: dict, score: int | None = None) -> dict:
    payload = {
        "id": item.get("id"),
        "collection": item.get("collection"),
        "tier": item.get("tier"),
        "status": item.get("status"),
        "title": item.get("title") or item.get("id"),
        "abstract": item.get("abstract") or "",
    }
    if score is not None:
        payload["score"] = score
    return payload


def compact_entity(item: dict) -> str:
    return f"{item.get('id') or item.get('path')} [{item.get('collection')}] tier={item.get('tier', '-')} status={item.get('status') or '-'}"


def short_text(text: str, width: int = 140) -> str:
    clean = " ".join(text.split())
    if len(clean) <= width:
        return clean
    return clean[: max(0, width - 3)].rstrip() + "..."


if __name__ == "__main__":
    raise SystemExit(main())
