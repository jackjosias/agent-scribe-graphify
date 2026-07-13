from __future__ import annotations

"""Canonical task-context facade for V2.16.

The storage implementation remains isolated in ``_task_context_impl``. This
module is the public policy boundary: every caller sees one canonical intent
vocabulary, including contexts created by older hosts that persisted aliases.
It also enforces first-write discovery evidence when SCRIBE has no relevant
historical result for the exact target resource.
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
_COARSE_RESOURCES = frozenset({
    "", ".", "(whole repo)", "whole repo", "whole-repo",
    "repository", "repo", "project", "project-wide",
})


def _is_coarse_resource(resource: str) -> bool:
    value = (resource or "").strip().lower()
    return value in _COARSE_RESOURCES or "whole repo" in value


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
    payload = {
        **result,
        "intent": task.get("intent") or "",
        "resource": task.get("resource") or "",
        "requires_graphify": bool(task.get("requires_graphify")),
    }
    try:
        from . import task_discovery
        payload["first_write_discovery"] = task_discovery.status(task_id)
    except Exception:
        pass
    return payload


def task_status(task_id: str) -> dict[str, Any]:
    return _canonical_task(_impl.task_status(task_id))


def get_task_context(agent_id: str, task_id: str) -> dict[str, Any]:
    return _canonical_task(_impl.get_task_context(agent_id, task_id))


def verify_active_context(
    agent_id: str,
    task_id: str,
    context_token: str,
) -> dict[str, Any]:
    """Validate ownership, token, active status and expiry without readiness gates."""

    return _canonical_task(_impl._load_ready(agent_id, task_id, context_token))


def scope_task_resource(
    agent_id: str,
    task_id: str,
    context_token: str,
    resource: str,
) -> dict[str, Any]:
    """Narrow one active coarse write task to one exact file resource.

    Scoping is one-way and is allowed only before any active claim, resource
    lock or pending patch exists. SCRIBE and Graphify readiness are reset so
    the exact file receives fresh targeted context; no child task is created.
    """

    from . import patch_queue, resource_locks, task_discovery

    data = verify_active_context(agent_id, task_id, context_token)
    canonical = normalize_intent(str(data.get("intent") or ""))
    if canonical not in {"write", "delete"}:
        raise TaskContextError(
            "TASK_RESOURCE_SCOPE_MUTATING_INTENT_REQUIRED",
            {"intent": canonical},
        )
    current = str(data.get("resource") or "")
    safe = patch_queue.safe_resource(resource)
    if _is_coarse_resource(safe):
        raise TaskContextError("TASK_EXACT_RESOURCE_REQUIRED")
    if not _is_coarse_resource(current):
        if current == safe:
            return {
                "task_id": task_id,
                "agent_id": agent_id,
                "resource": safe,
                "intent": canonical,
                "already_scoped": True,
            }
        raise TaskContextError(
            "TASK_CONTEXT_RESOURCE_MISMATCH",
            {"task_resource": current, "requested_resource": safe},
        )

    patch_queue.ensure_schema()
    resource_locks.ensure_schema()
    ensure_schema()
    with _impl.connect() as con:
        active_claims = con.execute(
            "SELECT COUNT(*) FROM claims WHERE agent_id=? AND status='active'",
            (agent_id,),
        ).fetchone()[0]
        pending_patches = con.execute(
            "SELECT COUNT(*) FROM patches_v2 "
            "WHERE agent_id=? AND status IN ('proposed','conflict')",
            (agent_id,),
        ).fetchone()[0]
        active_locks = con.execute(
            f"SELECT COUNT(*) FROM {resource_locks.LOCK_TABLE} "
            "WHERE agent_id=? AND expires_at>?",
            (agent_id, _impl.now_ts()),
        ).fetchone()[0]
        if active_claims or pending_patches or active_locks:
            raise TaskContextError(
                "TASK_RESOURCE_SCOPE_ALREADY_MUTATING",
                {
                    "active_claims": int(active_claims),
                    "pending_patches": int(pending_patches),
                    "active_locks": int(active_locks),
                },
            )

        request_hash = _impl._request_hash(
            str(data.get("request") or ""), canonical, safe
        )
        con.execute(
            """
            UPDATE task_context_v2
            SET resource=?,request_hash=?,scribe_done=0,graphify_done=0,
                memory_hash=NULL,scribe_result_count=0,
                scribe_result_resources='',scribe_record_done=0,
                scribe_record_required=0,scribe_record_policy=NULL,
                scribe_record_path=NULL,scribe_record_digest=NULL,
                scribe_record_promoted=0,scribe_record_entry_id=NULL,
                scribe_record_skip_reason=NULL
            WHERE task_id=? AND agent_id=? AND status='active'
            """,
            (safe, request_hash, task_id, agent_id),
        )
        _impl.add_event(
            con,
            "task.resource_scoped",
            {
                "task_id": task_id,
                "previous_resource": current,
                "resource": safe,
                "intent": canonical,
            },
            agent_id,
        )
    task_discovery.clear_task(task_id, agent_id)
    return {
        "task_id": task_id,
        "agent_id": agent_id,
        "previous_resource": current,
        "resource": safe,
        "intent": canonical,
        "scribe_done": False,
        "graphify_done": False,
        "already_scoped": False,
    }



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
    """Validate readiness, canonical intent and first-write discovery evidence.

    The storage implementation validates token, ownership, resource, SCRIBE
    freshness and Graphify. Intent authorization and first-write policy live
    here so every public caller receives the same security decision.
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
            raise TaskContextError(
                "TASK_CONTEXT_INTENT_REQUIRED: task context intent is required"
            )
        if canonical not in allowed:
            code = (
                "READ_INTENT_CANNOT_WRITE"
                if canonical == "read"
                else "TASK_CONTEXT_INTENT_MISMATCH"
            )
            raise TaskContextError(
                code,
                {"intent": canonical, "allowed_intents": sorted(allowed)},
            )

    if canonical in {"write", "delete"}:
        try:
            from . import task_discovery
            task_discovery.require_discovery_ready(
                agent_id,
                task_id,
                resource=resource or str(data.get("resource") or ""),
            )
        except task_discovery.TaskDiscoveryError as exc:
            raise TaskContextError(exc.code, exc.details) from exc

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
