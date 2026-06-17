from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rag_challenge import challenge
from rag_compact import format_results
from rag_context import context
from rag_scoring import retrieve

@dataclass(frozen=True)
class EvalResult:
    name: str
    passed: bool
    detail: str


def run_eval(index: dict[str, Any]) -> list[EvalResult]:
    checks: list[EvalResult] = []
    ids = lambda results: [str(result.entity.get("id")) for result in results]
    graph_results = retrieve("graphify structural facts causal memory separation", index, top_k=5, context="dev")
    checks.append(EvalResult("semantic-graphify-separation", "PAT-GRAPH-001" in ids(graph_results), ", ".join(ids(graph_results))))
    git_results = retrieve("commit product source keep graphify-out scribe-out generated state out", index, top_k=5, context="dev")
    checks.append(EvalResult("semantic-artifact-boundary", bool({"PAT-GIT-001", "INV-F004"} & set(ids(git_results))), ", ".join(ids(git_results))))
    rag_results = retrieve("agent retrieval interface scribe-rag context challenge SEL internal", index, top_k=5, context="dev")
    checks.append(EvalResult("semantic-scriberag-interface", "PAT-SCRIBE-RAG-001" in ids(rag_results), ", ".join(ids(rag_results))))
    direct_results = retrieve("SEL direct context query retrieval agent interdit", index, top_k=5, context="dev")
    checks.append(EvalResult("negative-sel-direct-query", "GHOST-SCRIBE-RAG-SEL-DIRECT-001" in ids(direct_results), ", ".join(ids(direct_results))))
    verdict, _ = challenge("appeler SEL direct context query pour retrieval agent", index)
    checks.append(EvalResult("challenge-sel-direct-stop", verdict == "STOP", verdict))
    verdict, _ = challenge("utiliser scribe-rag comme interface agent et garder SEL comme moteur interne", index)
    checks.append(EvalResult("challenge-scriberag-proceed", verdict == "PROCEED", verdict))
    query_lines = len(format_results("SCRIBE-RAG QUERY: scribe-rag", retrieve("scribe-rag interface agent", index, top_k=5)).splitlines())
    checks.append(EvalResult("query-compact-lines", query_lines <= 15, str(query_lines)))
    context_lines = len(context(index).splitlines())
    checks.append(EvalResult("context-compact-lines", context_lines <= 35, str(context_lines)))
    return checks


def format_eval(checks: list[EvalResult]) -> str:
    passed = sum(1 for check in checks if check.passed)
    lines = [f"SCRIBE-RAG EVAL: {passed}/{len(checks)}"]
    for check in checks:
        lines.append(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {check.detail}")
    lines.append("HYBRID: skip" if passed == len(checks) else "HYBRID: required")
    return "\n".join(lines)
