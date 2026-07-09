from __future__ import annotations

from typing import Any

_VALID_MODES = frozenset({"NANO", "QUICK", "STANDARD", "CRITICAL"})
_VALID_INTENTS = frozenset({"read", "write", "refactor", "delete", "test", "debug"})
_VALID_MODEL_TIERS = frozenset({"small", "large", "unknown"})

_DEFAULT_MODE = "STANDARD"
_DEFAULT_INTENT = "write"
_DEFAULT_MODEL_TIER = "large"

_REQUIRED_FIRST_ACTIONS = ["discipline_ping", "workflow_next"]
_REQUIRED_FINISH_ACTIONS = ["workspace_audit", "scribe_record", "finish_task"]
_FORBIDDEN = ["direct_write", "invent_tool_result", "finish_without_audit"]

_PROMPT_TEMPLATE = """\
Tache : {task}
Mode : {mode}. Intent : {intent}.
Ressource cible : {resource}

Avant toute action :
1. Fais discipline_ping.
2. Fais workflow_next.
3. Donne le prochain tool MCP obligatoire.
4. Respecte agent-scribe-graphify.
5. Aucune ecriture directe.

Pour toute modification de code :
- utilise SCRIBE pour le contexte ;
- utilise Graphify pour l impact structurel ;
- utilise pre_action_guard avant toute action sensible ;
- utilise action_lease_id quand necessaire ;
- applique les changements uniquement via le workflow MCP ;
- termine avec workspace_audit, scribe_record, puis scribe_promote_record si le record est durable, et finish_task.

Si tu ne peux pas appeler les tools MCP, STOP et affiche exactement :
HOST_MCP_UNBOUND

Tu n as pas le droit de dire termine sans fournir :
- task_id ;
- context_token ;
- workflow_next ou workflow_snapshot ;
- patch_id si patch applique ;
- workspace_audit ;
- scribe_record ;
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
    return i if i in _VALID_INTENTS else _DEFAULT_INTENT


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
            "ALERTE : Mode petit modele : lecture/analyse/proposition uniquement. "
            "Pas d ecriture directe.\n"
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
            "Preflight minimal : scribe-rag context. Pas de doctor, lock, sync, worktree."
        )
    elif normalized_mode == "CRITICAL":
        parts.append(
            "\nMode CRITICAL : mutation de surface partagee ou SCRIBE. "
            "Workflow read/check obligatoire avant. Lock acquire requis avant ecriture. "
            "Doctor avant et apres. Sync repair apres."
        )

    return {
        "ok": True,
        "verdict": "TENOR_TASK_PROMPT_READY",
        "prompt": "\n".join(parts),
        "required_first_actions": list(_REQUIRED_FIRST_ACTIONS),
        "required_finish_actions": list(_REQUIRED_FINISH_ACTIONS),
        "forbidden": list(_FORBIDDEN),
    }
