from __future__ import annotations

from typing import Any

_VALID_MODES = frozenset({"NANO", "QUICK", "STANDARD", "CRITICAL"})
_VALID_INTENTS = frozenset({"read", "write", "delete"})
_READ_ALIASES = frozenset({"read", "inspect", "query", "research", "explain"})
_WRITE_ALIASES = frozenset({"write", "edit", "patch", "modify", "code", "fix", "refactor", "test", "debug", "create"})
_DELETE_ALIASES = frozenset({"delete", "remove"})
_VALID_MODEL_TIERS = frozenset({"small", "large", "unknown"})

_DEFAULT_MODE = "STANDARD"
_DEFAULT_INTENT = "write"
_DEFAULT_MODEL_TIER = "large"

_REQUIRED_FIRST_ACTIONS = ["tenor_task_start"]
_REQUIRED_FINISH_ACTIONS = ["tenor_apply_changeset", "tenor_task_control"]
_FORBIDDEN = ["direct_write", "legacy_manual_choreography", "invent_tool_result", "finish_without_terminal_verdict"]

_PROMPT_TEMPLATE = """\
Tache : {task}
Mode : {mode}. Intent : {intent}.
Ressource cible : {resource}

Avant toute action :
1. Appelle tenor_task_start une seule fois avec objectif, intent, resources et scope.
2. Laisse TENOR executer SCRIBE et Graphify en interne ; ne rejoue jamais le workflow MCP historique outil par outil.
3. N invente jamais agent_id, context_token, verrou, lease ou tache de remplacement.
4. Aucune ecriture directe.

Pour toute modification de code :
- envoie tous les fichiers dans un seul tenor_apply_changeset atomique ;
- fournis le base_hash frais de chaque fichier et des validateurs argv bornes, sans shell ;
- laisse TENOR preflighter les chemins, hashes et verrous, appliquer, valider, rollback si necessaire, enregistrer SCRIBE et clore la tache ;
- si Graphify doit etre reconstruit pendant TENOR INIT, appelle graphify_project_build ; n execute jamais graphify update . ;
- pour une lecture, termine par tenor_task_control(action="finish") ;
- utilise tenor_activity pour l etat consolide, pas une suite d appels de diagnostic.

Si tu ne peux pas appeler les tools MCP, STOP et affiche exactement :
HOST_MCP_UNBOUND

Tu n as pas le droit de dire termine sans fournir :
- task_id ;
- verdict terminal machine ;
- changeset_id si modification ;
- preuve des validateurs et du rollback ou commit atomique ;
- tests executes ou raison claire si non executes."""


def normalize_mode(mode: str) -> str:
    if not mode:
        return _DEFAULT_MODE
    m = mode.strip().upper()
    return m if m in _VALID_MODES else _DEFAULT_MODE


def normalize_intent(intent: str) -> str:
    if not intent:
        return _DEFAULT_INTENT
    i = intent.strip().lower()
    if i in _READ_ALIASES:
        return "read"
    if i in _WRITE_ALIASES:
        return "write"
    if i in _DELETE_ALIASES:
        return "delete"
    return _DEFAULT_INTENT


def normalize_model_tier(tier: str) -> str:
    if not tier:
        return _DEFAULT_MODEL_TIER
    t = tier.strip().lower()
    return t if t in _VALID_MODEL_TIERS else _DEFAULT_MODEL_TIER


def generate_task_prompt(
    task: str = "",
    mode: str = _DEFAULT_MODE,
    intent: str = _DEFAULT_INTENT,
    resource: str = "",
    model_tier: str = _DEFAULT_MODEL_TIER,
) -> dict[str, Any]:
    if not task or not task.strip():
        return {
            "ok": False,
            "verdict": "TENOR_TASK_PROMPT_INVALID",
            "reason": "task is required and must not be empty.",
            "prompt": "",
        }

    normalized_mode = normalize_mode(mode)
    normalized_intent = normalize_intent(intent)
    normalized_tier = normalize_model_tier(model_tier)
    resource_str = resource.strip() if resource.strip() else "a determiner via Graphify/SCRIBE"

    parts: list[str] = []

    if normalized_tier == "small":
        parts.append(
            "ALERTE : Mode petit modele : API TENOR compacte, au plus deux appels normaux pour une ecriture. "
            "Aucun Edit/Bash natif, aucune orchestration MCP manuelle, aucune identite ou tache de remplacement.\n"
        )

    parts.append(
        _PROMPT_TEMPLATE.format(
            task=task.strip(),
            mode=normalized_mode,
            intent=normalized_intent,
            resource=resource_str,
        )
    )

    if normalized_mode == "NANO":
        parts.append(
            "\nMode NANO : tache < 30 min, 1 fichier, pas de surface partagee. "
            "Conserver tenor_task_start puis un changeset valide ; aucun rituel MCP supplementaire."
        )
    elif normalized_mode == "CRITICAL":
        parts.append(
            "\nMode CRITICAL : mutation de surface partagee ou SCRIBE. "
            "Validators renforces obligatoires dans le changeset atomique. "
            "Les verrous, rollback, preuve et cloture restent geres par TENOR."
        )

    return {
        "ok": True,
        "verdict": "TENOR_TASK_PROMPT_READY",
        "prompt": "\n".join(parts),
        "required_first_actions": list(_REQUIRED_FIRST_ACTIONS),
        "required_finish_actions": list(_REQUIRED_FINISH_ACTIONS),
        "forbidden": list(_FORBIDDEN),
    }
