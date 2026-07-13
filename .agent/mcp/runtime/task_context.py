from __future__ import annotations

"""Canonical task-context facade for V2.16.

The storage implementation remains isolated in ``_task_context_impl``. This
module is the public policy boundary: every caller sees one canonical intent
vocabulary, including contexts created by older hosts that persisted aliases.
"""

from typing import Any

try:
    from . import _task_context_impl as _impl
except ImportError:  # pragma: no cover - direct script/import compatibility
    import _task_context_impl as _impl  # type: ignore

TaskContextError = _impl.TaskContextError
DEFAULT_TTL_SECONDS = _impl.DEFAULT_TTL_SECONDS

_READ_ALIASES = frozenset({
    "read",
    "read_or_research",
    "read-or-research",
    "research",
    "research_only",
    "research-only",
    "inspect",
    "query",
    "ask",
    "explain",
    "list",
    "show",
    "status",
})
_WRITE_ALIASES = frozenset({
    "write", "edit", "patch", "modify", "code", "fix", "refactor", "test", "create",
})
_DELETE_ALIASES = frozenset({"delete", "remove"})


def normalize_intent(intent: str) -> str:
    """Return the canonical security intent used by all task-context gates."""

    value = (intent or "").strip().lower()
    if value in _READ_ALIASES:
        return "read"
    if value in _WRITE_ALIASES:
        return "write"
    if value in _DELETE_ALIASES:
        return "delete"
    return value


# Backward-compatible internal name used by older modules/tests.
_normalize_intent = normalize_intent


def _canonical_task(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    original = str(result.get("intent") or "")
    canonical = normalize_intent(original)
    result["intent"] = canonical
    if original and original.strip().lower() != canonical:
        result["intent_original"] = original
    return result


def find_active_task(agent_id: str, intent: str, resource: str) -> dict[str, Any] | None:
    found = _impl.find_active_task(agent_id, normalize_intent(intent), resource)
    return _canonical_task(found) if found is not None else None


def create_task_context(
    agent_id: str,
    request: str,
    intent: str = "",
    resource: str = "",
    requires_graphify: bool = False,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    canonical = normalize_intent(intent)
    result = _impl.create_task_context(
        agent_id,
        request,
        intent=canonical,
        resource=resource,
        requires_graphify=requires_graphify,
        ttl_seconds=ttl_seconds,
    )
    return {**result, "intent": canonical}


def resume_task_context(agent_id: str, task_id: str) -> dict[str, Any]:
    result = _impl.resume_task_context(agent_id, task_id)
    task = _canonical_task(_impl.task_status(task_id))
    return {
        **result,
        "intent": task.get("intent") or "",
        "resource": task.get("resource") or "",
        "requires_graphify": bool(task.get("requires_graphify")),
    }


def task_status(task_id: str) -> dict[str, Any]:
    return _canonical_task(_impl.task_status(task_id))


def get_task_context(agent_id: str, task_id: str) -> dict[str, Any]:
    return _canonical_task(_impl.get_task_context(agent_id, task_id))


def list_tasks(agent_id: str = "", status: str = "") -> dict[str, Any]:
    result = dict(_impl.list_tasks(agent_id=agent_id, status=status))
    result["tasks"] = [_canonical_task(task) for task in result.get("tasks", [])]
    result["count"] = len(result["tasks"])
    return result


def require_context_ready(
    agent_id: str,
    task_id: str,
    context_token: str,
    resource: str = "",
    require_graphify: bool | None = None,
    strict_resource: bool = False,
    allowed_intents: set[str] | None = None,
) -> dict[str, Any]:
    """Validate readiness, then enforce canonical intent authorization.

    Readiness (token, ownership, resource, SCRIBE freshness and Graphify) stays
    delegated to the storage implementation. Intent authorization is applied
    here so aliases cannot accidentally gain write privileges or become
    impossible to finish.
    """

    data = _impl.require_context_ready(
        agent_id,
        task_id,
        context_token,
        resource=resource,
        require_graphify=require_graphify,
        strict_resource=strict_resource,
        allowed_intents=None,
    )
    canonical = normalize_intent(str(data.get("intent") or ""))
    if allowed_intents is not None:
        allowed = {normalize_intent(value) for value in allowed_intents}
        if not canonical:
            raise TaskContextError("TASK_CONTEXT_INTENT_REQUIRED: task context intent is required")
        if canonical not in allowed:
            code = "READ_INTENT_CANNOT_WRITE" if canonical == "read" else "TASK_CONTEXT_INTENT_MISMATCH"
            raise TaskContextError(code, {"intent": canonical, "allowed_intents": sorted(allowed)})
    result = dict(data)
    result["intent"] = canonical
    return result


# Re-export the storage API without overriding the policy functions above.
for _name in dir(_impl):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_impl, _name)


def __getattr__(name: str) -> Any:
    return getattr(_impl, name)
