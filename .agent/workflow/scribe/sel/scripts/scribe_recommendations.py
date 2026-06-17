from __future__ import annotations

from typing import Any


def build_recommendations(payload: dict[str, Any]) -> list[dict[str, str]]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    findings = payload.get("doctor_findings") if isinstance(payload.get("doctor_findings"), list) else []
    retrieval = payload.get("retrieval_quality") if isinstance(payload.get("retrieval_quality"), dict) else {}
    retrieval_summary = retrieval.get("summary") if isinstance(retrieval.get("summary"), dict) else {}
    entities = payload.get("entities") if isinstance(payload.get("entities"), list) else []
    recommendations: list[dict[str, str]] = []

    if int(summary.get("doctor_errors", 0) or 0) > 0:
        recommendations.append(action("P0", "Doctor bloquant", "Corriger les erreurs doctor avant toute mutation SCRIBE.", "scribe doctor --suggest-fix"))

    stale_warm = int(summary.get("stale_warm_patterns_without_causal_source", 0) or 0)
    if stale_warm:
        recommendations.append(
            action(
                "P1",
                "Auditer les PAT warm sans source causale",
                f"{stale_warm} PAT warm ont dépassé 20 sessions sans SCAR/GHOST source.",
                'scribe-rag query "PAT warm sans SCAR GHOST"',
            )
        )

    active_debts = active_debt_count(entities)
    if active_debts:
        recommendations.append(
            action(
                "P1",
                "Rembourser les dettes actives",
                f"{active_debts} dette(s) active(s) restent visibles avant montée en charge.",
                'scribe-rag query "dette active remboursement"',
            )
        )

    failed_cases = int(retrieval_summary.get("failed_cases", 0) or 0)
    if failed_cases:
        recommendations.append(
            action(
                "P1",
                "Corriger la qualité retrieval",
                f"{failed_cases} cas eval échoue(nt); ne pas flatter le score, documenter la limite.",
                "scribe-rag eval --force",
            )
        )

    causal_edges = int(summary.get("causal_edges", 0) or 0)
    total_edges = int((summary.get("edges") or {}).get("total", 0) if isinstance(summary.get("edges"), dict) else 0)
    if total_edges and causal_edges / total_edges < 0.20:
        recommendations.append(
            action(
                "P2",
                "Surveiller la densité causale stricte",
                "Signal informatif: ne jamais créer SCAR/GHOST/PAT pour nettoyer le score; attendre vraie douleur, bug, décision ou alternative rejetée.",
                'scribe-rag challenge "vraie douleur applicative à documenter"',
            )
        )

    if index_is_missing_or_partial(summary):
        recommendations.append(
            action(
                "P2",
                "Reconstruire l’index complet",
                "Le cache doit contenir toutes les entités pour query/stats/dashboard sous charge.",
                "scribe-rag build",
            )
        )

    for finding in findings:
        if not isinstance(finding, dict) or finding.get("code") != "W003":
            continue
        recommendations.append(
            action(
                "P2",
                "Réduire la pression hot",
                "Au moins une entrée hot n'est jamais consultée; réduire ou justifier le tier.",
                "scribe review-hot --target 12",
            )
        )
        break

    if not recommendations:
        recommendations.append(
            action(
                "OK",
                "Aucune action bloquante",
                "Continuer sur le projet réel; capturer SCAR/GHOST seulement après douleur causale concrète.",
                'scribe-rag preflight --tier READ_ONLY "prochaine douleur causale"',
            )
        )
    return recommendations[:6]


def action(priority: str, title: str, detail: str, command: str) -> dict[str, str]:
    return {"priority": priority, "title": title, "detail": detail, "command": command}


def active_debt_count(entities: list[Any]) -> int:
    count = 0
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if entity.get("collection") in {"debts", "dettes"} and str(entity.get("status", "")).upper() == "ACTIVE":
            count += 1
    return count


def index_is_missing_or_partial(summary: dict[str, Any]) -> bool:
    return int(summary.get("index_version", 0) or 0) < 2 or summary.get("index_complete") is not True
