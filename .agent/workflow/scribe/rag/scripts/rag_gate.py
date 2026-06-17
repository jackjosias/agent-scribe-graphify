from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rag_eval import format_eval, run_eval


@dataclass(frozen=True)
class GateResult:
    code: int
    lines: list[str]


def gate(index: dict[str, Any], *, min_passed: int = 8) -> GateResult:
    checks = run_eval(index)
    passed = sum(1 for check in checks if check.passed)
    total = len(checks)
    failed = [check.name for check in checks if not check.passed]
    ok = passed == total and passed >= min_passed
    lines = [format_eval(checks)]
    if ok:
        lines.append(f"SCRIBE-RAG GATE: PASS eval {passed}/{total}")
        return GateResult(0, lines)
    lines.append(f"SCRIBE-RAG GATE: FAIL eval {passed}/{total}; required all checks pass and >= {min_passed}/{total}")
    if failed:
        lines.append("failed: " + ", ".join(failed))
    lines.append('fix: run `scribe-rag preflight --tier STANDARD "<plan>"` and repair SCRIBE memory or retrieval scoring before committing')
    return GateResult(1, lines)
