from __future__ import annotations

"""Canonical task-context facade for V2.16.

The storage implementation remains isolated in ``_task_context_impl``. This
module is the public policy boundary: every caller sees one canonical intent
vocabulary, including contexts created by older hosts that persisted aliases.

A zero SCRIBE result is security-significant only when it was written by the
explicit first-write no-history path.  Older contexts may have ``scribe_done``
with a zero/default result count, so they are projected as legacy-ready unless
the managed marker is present.
"""

from typing import Any

try:
    from . import _task_context_impl as _impl
except ImportError:  # pragma: no cover - direct script/import compatibility
    import _task_context_impl as _impl  # type: ignore

TaskContextError = _impl.TaskContextError
DEFAULT_TTL_SECONDS = _impl.DEFAULT_TTL_SECONDS
FIRST_WRITE_NO_HISTORY_PREFIX = "FIRST_WRITE_NO_HISTORY:"
CANONICAL_INTENTS = ("read", "write", "delete")

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


def require_machine_intent(intent: str) -> str:
    """Return one canonical machine intent or fail before state is created.

    Exact aliases remain accepted for compatibility (for example ``fix`` or
    ``inspect``), but descriptive prose is not a machine contract.  Persisting
    free-form text here makes every later authorization comparison ambiguous.
    """

    canonical = normalize_intent(intent)
    if canonical not in CANONICAL_INTENTS:
        raise TaskContextError(
            "TASK_INTENT_ENUM_REQUIRED",
            {
                "intent": (intent or "").strip(),
                "allowed_intents": sorted(CANONICAL_INTENTS),
            },
        )
    return canonical


# Backward-compatible internal name used by older modules/tests.
_normalize_intent = normalize_intent


def _canonical_task(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    original = str(result.get("intent") or "")
    canonical = normalize_intent(original)
    result["intent"] = canonical
    if original and original.strip().lower() != canonical:
        result["intent_original"] = original

    resources = str(result.get("scribe_result_resources") or "")
    explicit_no_history = resources.startswith(FIRST_WRITE_NO_HISTORY_PREFIX)
    result["scribe_history_absent"] = explicit_no_history
    if explicit_no_history:
        result["scribe_history_resource"] = resources[len(FIRST_WRITE_NO_HISTORY_PREFIX):]
    elif result.get("scribe_done"):
        try:
            count = int(result.get("scribe_result_count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count == 0:
            # Compatibility projection only: pre-V2.16 contexts did not persist
            # an explicit no-history marker.  They were already considered
            # context-ready and must not be reclassified by a schema default.
            result["scribe_result_count"] = 1
            result["scribe_result_count_legacy_assumed_ready"] = True
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
    canonical = require_machine_intent(intent)
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


def mark_scribe_done(
    agent_id: str,
    task_id: str,
    context_token: str,
    result_count: int = 0,
    result_resources: str = "",
) -> dict[str, Any]:
    resources = result_resources or ""
    if int(result_count or 0) == 0 and not resources.startswith(FIRST_WRITE_NO_HISTORY_PREFIX):
        resources = f"{FIRST_WRITE_NO_HISTORY_PREFIX}{resources}"
    result = _impl.mark_scribe_done(
        agent_id,
        task_id,
        context_token,
        result_count=result_count,
        result_resources=resources,
    )
    result["scribe_history_absent"] = int(result_count or 0) == 0
    if result["scribe_history_absent"]:
        result["scribe_history_resource"] = resources[len(FIRST_WRITE_NO_HISTORY_PREFIX):]
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
    result = _canonical_task(dict(data))
    result["intent"] = canonical
    return result


# Re-export the storage API without overriding the policy functions above.
for _name in dir(_impl):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_impl, _name)


def __getattr__(name: str) -> Any:
    return getattr(_impl, name)
