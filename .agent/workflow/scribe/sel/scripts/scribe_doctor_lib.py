from __future__ import annotations

from pathlib import Path

from scribe_doctor_checks import check_all
from scribe_doctor_model import collect_entities, collect_registry, parse_yaml, read_text
from scribe_doctor_report import has_errors, print_summary, render_report


def run_doctor(scribe_path: Path, report_path: Path, suggest_fix: bool) -> int:
    raw = read_text(scribe_path)
    data, findings = parse_yaml(raw, scribe_path)

    if data is not None:
        entities = collect_entities(data)
        registry = collect_registry(data)
        findings.extend(check_all(data, raw, entities, registry, scribe_path))

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(scribe_path, findings, suggest_fix), encoding="utf-8")
    print_summary(report_path, findings)
    return 1 if has_errors(findings) else 0
