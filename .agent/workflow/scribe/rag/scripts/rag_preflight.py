from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rag_challenge import challenge
from rag_context import context as format_context
from rag_eval import run_eval
from rag_text import compact
from rag_whoami import format_whoami


CHALLENGE_TIERS = {"QUICK", "STANDARD", "CRITICAL"}


@dataclass(frozen=True)
class PreflightResult:
    code: int
    lines: list[str]


def _indent_block(text: str, *, limit: int) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    return [f"  {line}" for line in lines[:limit]]


def _eval_status(index: dict[str, Any]) -> tuple[int, int, list[str]]:
    checks = run_eval(index)
    passed = sum(1 for check in checks if check.passed)
    total = len(checks)
    failed = [check.name for check in checks if not check.passed]
    return passed, total, failed


def _challenge_lines(plan: str | None, index: dict[str, Any], *, limit: int) -> tuple[str, list[str]]:
    if not plan:
        return "MISSING", ["SCRIBE-RAG CHALLENGE: missing plan", "VERDICT: MISSING_PLAN"]
    verdict, lines = challenge(plan, index, limit=limit)
    return verdict, lines


def preflight(
    index: dict[str, Any],
    *,
    tier: str,
    plan: str | None,
    module: str | None,
    limit: int,
    strict: bool,
) -> PreflightResult:
    normalized_tier = tier.upper()
    context_text = format_context(index, module=module or "bundle")
    eval_passed, eval_total, eval_failed = _eval_status(index)
    should_challenge = normalized_tier in CHALLENGE_TIERS
    verdict = "SKIPPED"
    challenge_text = ["SCRIBE-RAG CHALLENGE: skipped for READ_ONLY tier", "VERDICT: SKIPPED"]
    if should_challenge:
        verdict, challenge_text = _challenge_lines(plan, index, limit=limit)

    eval_ok = eval_passed == eval_total
    challenge_ok = verdict not in {"STOP", "MISSING"} and not (strict and verdict == "MISSING_PLAN")
    if strict and should_challenge and not plan:
        challenge_ok = False

    code = 0
    if verdict == "STOP":
        code = 2
    elif strict and not eval_ok:
        code = 4
    elif strict and not challenge_ok:
        code = 3

    eval_gate = "PASS" if eval_ok else "HYBRID_REQUIRED"
    proof_bits = [
        "whoami OK",
        "context OK",
        f"eval {eval_passed}/{eval_total} {eval_gate}",
        f"challenge {verdict}",
    ]
    lines = [
        "SCRIBE-RAG PREFLIGHT",
        f"tier     : {normalized_tier}",
        f"mode     : {index.get('mode')}",
        f"source   : {index.get('source')}",
        "proof    : " + " | ".join(proof_bits),
        f"SCRIBE_RAG_PROOF: preflight {normalized_tier} | eval {eval_passed}/{eval_total} {eval_gate} | challenge {verdict}",
        "required : use scribe-rag for memory retrieval; SEL direct is maintenance/write only",
        "required : read graphify-out/GRAPH_REPORT.md before application architecture work",
    ]
    if eval_failed:
        lines.append("eval_fix : run `scribe-rag build --with-embeddings --force` if eval stays below threshold")
        lines.append("eval_fail: " + compact(", ".join(eval_failed), 120))
    if should_challenge and not plan:
        lines.append("next     : rerun preflight with a concrete plan before editing")

    lines.append("whoami:")
    lines.extend(_indent_block(format_whoami(), limit=6))
    lines.append("context:")
    lines.extend(_indent_block(context_text, limit=14))
    lines.append("challenge:")
    lines.extend(_indent_block("\n".join(challenge_text), limit=8))
    return PreflightResult(code=code, lines=lines[:45])
