from __future__ import annotations

from html import escape
from typing import Any


TIER_LABELS = {"hot": "chaud", "warm": "tiède", "cold": "froid", "-": "-"}
COLLECTION_LABELS = {
    "debts": "dettes",
    "ghosts": "fantômes",
    "invariants": "invariants",
    "journal": "journal",
    "patterns": "patterns",
    "vaccins": "vaccins",
}
STATUS_LABELS = {"ACTIVE": "actif", "active": "actif", "-": "-"}


def ops_metric(label: str, value: Any, detail: str) -> str:
    return (
        '<div class="ops-metric">'
        f'<span>{escape(label)}</span><strong>{escape(str(value))}</strong><small>{escape(detail)}</small>'
        "</div>"
    )


def health_card(summary: dict[str, Any]) -> str:
    return (
        f'<aside class="health-card {health_class(summary)}" aria-label="Santé du diagnostic SCRIBE">'
        '<div><p class="eyebrow">Santé doctor</p>'
        f'<strong>{escape(health_label(summary))}</strong></div>'
        f'<span>{escape(health_detail(summary))}</span>'
        "</aside>"
    )


def chart_card(title: str, note: str, chart_name: str) -> str:
    return (
        '<article class="chart-card">'
        f'<div><h2>{escape(title)}</h2><p class="panel-note">{escape(note)}</p></div>'
        f'<div class="chart" data-chart="{escape(chart_name)}"></div></article>'
    )


def recommendation_cards(recommendations: Any) -> str:
    items = recommendations if isinstance(recommendations, list) else []
    if not items:
        return '<p class="panel-note">Aucune action calculée.</p>'
    rendered = []
    for item in items[:6]:
        if not isinstance(item, dict):
            continue
        rendered.append(
            '<article class="action-card">'
            f'<span class="action-priority {priority_class(str(item.get("priority") or ""))}">{escape(str(item.get("priority") or "-"))}</span>'
            f'<h3>{escape(str(item.get("title") or "-"))}</h3>'
            f'<p>{escape(str(item.get("detail") or ""))}</p>'
            f'<code>{escape(str(item.get("command") or ""))}</code>'
            '</article>'
        )
    return "".join(rendered) or '<p class="panel-note">Aucune action calculée.</p>'


def priority_class(priority: str) -> str:
    value = priority.lower()
    if value in {"p0", "p1", "p2", "ok"}:
        return value
    return "p2"


def select_control(name: str, label: str, options: list[str], labels: dict[str, str]) -> str:
    rendered = [f'<option value="">{escape(label)}</option>']
    rendered.extend(f'<option value="{escape(option.lower())}">{escape(display_label(option, labels))}</option>' for option in options)
    return f'<select class="control" data-filter="{escape(name)}">{"".join(rendered)}</select>'


def sort_control() -> str:
    options = [
        ("degree", "Trier par degré"),
        ("title", "Trier par titre"),
        ("tier", "Trier par niveau"),
        ("collection", "Trier par collection"),
    ]
    rendered = "".join(f'<option value="{value}">{label}</option>' for value, label in options)
    return f'<select class="control" data-filter="sort">{rendered}</select>'


def distribution_rows(counts: dict[str, int], labels: dict[str, str]) -> str:
    maximum = max(counts.values(), default=1)
    rows = []
    for name, count in sorted(counts.items()):
        width = max(4, round((count / maximum) * 100)) if maximum else 0
        rows.append(
            '<div class="dist-row">'
            f'<strong>{escape(display_label(name, labels))}</strong>'
            f'<div class="bar" aria-hidden="true"><span data-bar-width="{width}"></span></div>'
            f'<span>{count}</span>'
            '</div>'
        )
    return "".join(rows) or '<p class="panel-note">Aucune donnée.</p>'


def entity_rows(entities: list[dict[str, Any]]) -> str:
    return "".join(entity_row(entity) for entity in entities)


def entity_row(entity: dict[str, Any]) -> str:
    entity_id = str(entity.get("id") or "-")
    collection = str(entity.get("collection") or "-")
    title = str(entity.get("title") or entity_id)
    abstract = str(entity.get("abstract") or "")
    tier = str(entity.get("tier") or "-")
    status = str(entity.get("status") or "-")
    score = connection_score(entity)
    search = " ".join([entity_id, collection, title, abstract, tier, status])
    return (
        '<article class="entity-card" data-entity-card '
        f'data-id="{escape(entity_id)}" data-title="{escape(title)}" '
        f'data-collection="{escape(collection.lower())}" data-tier="{escape(tier.lower())}" '
        f'data-status="{escape(status.lower())}" data-degree="{score}" data-search="{escape(search)}">'
        '<div class="entity-top"><div class="entity-title">'
        f'<h3>{escape(title)}</h3><code>{escape(entity_id)}</code></div>'
        f'<span class="degree">degré {score}</span></div>'
        f'<p>{escape(abstract)}</p>'
        '<div class="meta">'
        f'<span class="tag">{escape(display_label(collection, COLLECTION_LABELS))}</span>'
        f'<span class="tag {escape(tier.lower())}">niveau {escape(display_label(tier, TIER_LABELS))}</span>'
        f'<span class="tag {escape(status.lower())}">{escape(display_label(status, STATUS_LABELS))}</span>'
        f'<span class="tag">sortants {len(as_string_list(entity.get("outgoing")))}</span>'
        f'<span class="tag">entrants {len(as_string_list(entity.get("incoming")))}</span>'
        '</div></article>'
    )


def display_label(value: str, labels: dict[str, str]) -> str:
    return labels.get(value, labels.get(value.lower(), value or "-"))


def count_by_key(entities: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entity in entities:
        name = str(entity.get(key) or "-")
        counts[name] = counts.get(name, 0) + 1
    return counts


def connection_score(entity: dict[str, Any]) -> int:
    outgoing = entity.get("outgoing")
    incoming = entity.get("incoming")
    return len(outgoing if isinstance(outgoing, list) else []) + len(incoming if isinstance(incoming, list) else [])


def health_class(summary: dict[str, Any]) -> str:
    if int(summary.get("doctor_errors", 0) or 0) > 0:
        return "bad"
    if int(summary.get("doctor_warnings", 0) or 0) > 0:
        return "warn"
    return "ok"


def health_label(summary: dict[str, Any]) -> str:
    errors = int(summary.get("doctor_errors", 0) or 0)
    warnings = int(summary.get("doctor_warnings", 0) or 0)
    return f"{errors} erreur(s), {warnings} avertissement(s)"


def health_detail(summary: dict[str, Any]) -> str:
    if int(summary.get("doctor_errors", 0) or 0) > 0:
        return "Arrêter et corriger la corruption mémoire bloquante avant d’utiliser ce tableau."
    if int(summary.get("doctor_warnings", 0) or 0) > 0:
        return "Utilisable avec avertissements historiques ; garder le doctor à zéro erreur."
    return "La mémoire est structurellement saine."


def active_debt_count(entities: list[dict[str, Any]]) -> int:
    return sum(1 for entity in entities if entity.get("collection") == "debts" and entity.get("status") == "ACTIVE")


def edge_counts(summary: dict[str, Any]) -> dict[str, int]:
    edges = summary.get("edges")
    if not isinstance(edges, dict):
        causal = int(summary.get("causal_edges", 0) or 0)
        return {"total": causal, "causal": causal, "evidence": 0, "consultation": 0, "journal": 0}
    return {name: int(edges.get(name, 0) or 0) for name in ("total", "causal", "evidence", "consultation", "journal")}


def density_label(summary: dict[str, Any]) -> str:
    entities = int(summary.get("entities", 0) or 0)
    edges = edge_counts(summary)["causal"]
    if entities == 0:
        return "0 lien causal/entrée"
    return f"{edges / entities:.1f} causal/entrée"


def retrieval_quality_label(payload: dict[str, Any]) -> str:
    summary = retrieval_quality_summary(payload)
    return f"{float(summary.get('pass_rate', 1.0) or 0.0) * 100:.0f}%"


def retrieval_quality_detail(payload: dict[str, Any]) -> str:
    summary = retrieval_quality_summary(payload)
    cases = int(summary.get("cases", 0) or 0)
    surface_cases = int(summary.get("surface_cases", 0) or 0)
    return f"{cases} cas / {surface_cases} surfaces"


def retrieval_quality_summary(payload: dict[str, Any]) -> dict[str, Any]:
    quality = payload.get("retrieval_quality")
    if not isinstance(quality, dict):
        return {}
    summary = quality.get("summary")
    return summary if isinstance(summary, dict) else {}


def entity_priority(entity: dict[str, Any]) -> tuple[int, int, str]:
    tier_weight = {"hot": 0, "warm": 1, "cold": 2}.get(str(entity.get("tier") or "").lower(), 3)
    debt_weight = 0 if entity.get("collection") == "debts" and entity.get("status") == "ACTIVE" else 1
    return (debt_weight, tier_weight, -connection_score(entity), str(entity.get("id") or ""))


def as_entities(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [entity for entity in value if isinstance(entity, dict)]


def as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
