from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - surfaced as E008.
    yaml = None

if yaml is not None:
    YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
else:
    YAML_LOADER = None

PATCH_DATE = date(2026, 5, 23)
ENTITY_COLLECTIONS = {
    "invariants",
    "scars",
    "vaccins",
    "patterns",
    "ghosts",
    "dettes",
    "debts",
    "adrs",
    "lecons",
    "lessons",
    "hypotheses",
    "mutations",
    "journal",
}
NEGATION_MARKERS = ("ne jamais", "ne pas", "never", "interdit", "forbid", "avoid")
POSITIVE_MARKERS = ("toujours", "obligatoire", "use ", "utiliser", "doit", "must")
STOPWORDS = {
    "avec",
    "dans",
    "pour",
    "sans",
    "the",
    "and",
    "une",
    "des",
    "les",
    "that",
    "this",
    "toujours",
    "jamais",
    "utiliser",
    "obligatoire",
}


@dataclass(frozen=True)
class Entity:
    collection: str
    index: int
    path: str
    value: dict[str, Any]

    @property
    def id(self) -> str | None:
        raw_id = self.value.get("id")
        return raw_id if isinstance(raw_id, str) else None


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    location: str
    message: str
    suggestion: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_yaml(raw: str, path: Path) -> tuple[Any | None, list[Finding]]:
    if yaml is None:
        return None, [
            finding(
                "ERROR",
                "E008",
                str(path),
                "PyYAML is not installed, so the SCRIBE cannot be parsed.",
                "Install PyYAML or use an environment with yaml.safe_load.",
            )
        ]
    try:
        data = yaml.load(raw, Loader=YAML_LOADER)
    except Exception as exc:  # noqa: BLE001 - parse detail is user-facing.
        return None, [
            finding(
                "ERROR",
                "E008",
                str(path),
                f"YAML invalide: {exc}",
                "Corriger la syntaxe YAML avant toute autre vérification.",
            )
        ]
    if not isinstance(data, dict):
        return None, [
            finding(
                "ERROR",
                "E008",
                str(path),
                "Le SCRIBE doit parser vers un mapping YAML racine.",
                "Utiliser un objet YAML racine avec les sections causales.",
            )
        ]
    return data, []


def collect_entities(data: dict[str, Any]) -> list[Entity]:
    entities: list[Entity] = []
    for key in ENTITY_COLLECTIONS:
        items = data.get(key)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if isinstance(item, dict):
                entities.append(Entity(key, index, f"{key}[{index}]", item))
    return sorted(entities, key=lambda entity: entity.path)


def collect_registry(root: Any) -> dict[str, list[str]]:
    registry: dict[str, list[str]] = {}

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            raw_id = value.get("id")
            if isinstance(raw_id, str):
                registry.setdefault(raw_id, []).append(path)
            for key, child in value.items():
                walk(child, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(root, "")
    return registry


def detect_v32_document(data: dict[str, Any], raw: str) -> bool:
    schema_version = str(data.get("schema_version", ""))
    if "3.2" in schema_version or "V3.2" in schema_version:
        return True
    if parse_date(data.get("schema_patch_date")) is not None:
        return True
    return bool(re.search(r"Scribe_Version\s*;\s*3\.2", raw))


def is_post_v32(entity: Entity, file_is_v32: bool) -> bool:
    schema_date = parse_date(entity.value.get("schema_patch_date"))
    if schema_date is not None:
        return schema_date >= PATCH_DATE
    fallback = parse_date(entity.value.get("date") or entity.value.get("date_creation"))
    if fallback is None:
        return False
    if fallback > PATCH_DATE:
        return True
    return file_is_v32 and fallback >= PATCH_DATE


def parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def has_evidence_type(value: dict[str, Any]) -> bool:
    evidence = value.get("evidence")
    if not isinstance(evidence, dict):
        return False
    return str(evidence.get("type", "")).upper() in {"OBSERVED", "REASONED", "ASSUMED"}


def as_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def confirmed_session_ids(value: dict[str, Any]) -> list[str]:
    sessions = value.get("confirmed_sessions")
    if not isinstance(sessions, list):
        return []
    return [
        item["session"]
        for item in sessions
        if isinstance(item, dict) and isinstance(item.get("session"), str)
    ]


def canonical_status(value: dict[str, Any]) -> str:
    raw = value.get("status", value.get("statut"))
    return str(raw).upper() if raw is not None else ""


def session_count(data: dict[str, Any]) -> int:
    metrics = data.get("metrics")
    if isinstance(metrics, dict) and isinstance(metrics.get("sessions_total"), int):
        return metrics["sessions_total"]
    journal = data.get("journal")
    return len(journal) if isinstance(journal, list) else 0


def looks_inverse(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_text = searchable_text(left)
    right_text = searchable_text(right)
    if len(tokens(left_text) & tokens(right_text)) < 2:
        return False
    left_negative = contains_any(left_text, NEGATION_MARKERS)
    right_negative = contains_any(right_text, NEGATION_MARKERS)
    left_positive = contains_any(left_text, POSITIVE_MARKERS)
    right_positive = contains_any(right_text, POSITIVE_MARKERS)
    return (left_negative and right_positive) or (right_negative and left_positive)


def has_causal_link(value: dict[str, Any], target_id: str | None) -> bool:
    if not target_id:
        return False
    causal = value.get("liens_causaux")
    if not isinstance(causal, dict):
        return False
    for field_value in causal.values():
        if target_id == field_value or target_id in as_refs(field_value):
            return True
    return False


def searchable_text(value: dict[str, Any]) -> str:
    fields = [value.get("l0_abstract"), value.get("virus"), value.get("antidote"), value.get("titre")]
    return " ".join(str(field).lower() for field in fields if field)


def tokens(text: str) -> set[str]:
    raw_tokens = re.findall(r"[a-zA-ZÀ-ÿ0-9_]{4,}", text.lower())
    return {token for token in raw_tokens if token not in STOPWORDS}


def contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def finding(severity: str, code: str, location: str, message: str, suggestion: str) -> Finding:
    return Finding(severity, code, location, message, suggestion)


def required_finding(error_code: str, warn_code: str, entity: Entity, is_new: bool, field: str) -> Finding:
    severity = "ERROR" if is_new else "WARNING"
    code = error_code if is_new else warn_code
    age = "post-V3.2" if is_new else "pré-V3.2"
    message = f"{entity.id or entity.path} ({age}) manque {field}."
    return finding(severity, code, entity.path, message, f"Ajouter {field} avant promotion en hot.")


def broken_reference(code: str, location: str, field: str, target: str) -> Finding:
    message = f"{field} pointe vers un ID inexistant: {target}"
    return finding("ERROR", code, location, message, "Créer l'entrée cible ou corriger la référence.")
