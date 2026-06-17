from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from scribe_doctor_model import Finding


def render_report(path: Path, findings: list[Finding], suggest_fix: bool) -> str:
    errors = [item for item in findings if item.severity == "ERROR"]
    warnings = [item for item in findings if item.severity == "WARNING"]
    status = "FAIL" if errors else "PASS"
    lines = [
        "# SCRIBE Doctor Report",
        "",
        f"- Input: `{path}`",
        f"- Generated: `{datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')}`",
        "- Mode: read-only",
        f"- Result: `{status}`",
        "",
        f"## ERRORS ({len(errors)})",
        "",
    ]
    lines.extend(render_findings(errors))
    lines.extend(["", f"## WARNINGS ({len(warnings)})", ""])
    lines.extend(render_findings(warnings))
    lines.extend(["", "## SUGGESTIONS --suggest-fix", ""])
    if suggest_fix:
        lines.extend(render_suggestions(findings))
    else:
        lines.append("Run with `--suggest-fix` to include non-mutating repair suggestions.")
    lines.append("")
    return "\n".join(lines)


def render_findings(findings: list[Finding]) -> list[str]:
    if not findings:
        return ["Aucun."]
    ordered = sorted(findings, key=lambda item: (item.code, item.location, item.message))
    return [f"- `{item.code}` `{item.location}` — {item.message}" for item in ordered]


def render_suggestions(findings: list[Finding]) -> list[str]:
    if not findings:
        return ["Aucune suggestion: aucun problème détecté."]
    ordered = sorted(findings, key=lambda item: (item.code, item.location, item.suggestion))
    return [f"- `{item.code}` `{item.location}` — {item.suggestion}" for item in ordered]


def print_summary(report_path: Path, findings: list[Finding]) -> None:
    errors = sum(1 for item in findings if item.severity == "ERROR")
    warnings = sum(1 for item in findings if item.severity == "WARNING")
    print(f"scribe doctor: {errors} error(s), {warnings} warning(s)")
    print(f"report: {report_path}")


def has_errors(findings: list[Finding]) -> bool:
    return any(item.severity == "ERROR" for item in findings)
