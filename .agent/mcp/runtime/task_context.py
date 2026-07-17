from __future__ import annotations

"""Canonical task-context facade with exact first-write discovery binding."""

from typing import Any
from pathlib import Path

from .state_paths import project_root_from

try:
    from . import _task_context_first_write_impl as _impl
except ImportError:  # pragma: no cover
    import _task_context_first_write_impl as _impl  # type: ignore

TaskContextError = _impl.TaskContextError
DEFAULT_TTL_SECONDS = _impl.DEFAULT_TTL_SECONDS
FIRST_WRITE_NO_HISTORY_PREFIX = _impl.FIRST_WRITE_NO_HISTORY_PREFIX

_COARSE_RESOURCES = frozenset({
    "", ".", "(whole repo)", "whole repo", "whole-repo",
    "repository", "repo", "project", "project-wide",
})


def _is_coarse_resource(resource: str) -> bool:
    value = (resource or "").strip().lower()
    if value in _COARSE_RESOURCES or "whole repo" in value:
        return True
    normalized = (resource or "").strip().replace("\\", "/").rstrip("/")
    if not normalized:
        return True
    candidate = Path(normalized)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return False
    return (project_root_from() / candidate).is_dir()


def is_scope_container(resource: str) -> bool:
    return _is_coarse_resource(resource)


def _scope_contains(container: str, resource: str) -> bool:
    normalized = (container or "").strip().replace("\\", "/").rstrip("/")
    if normalized.lower() in _COARSE_RESOURCES or "whole repo" in normalized.lower():
        return True
    return resource == normalized or resource.startswith(normalized + "/")


# Preserve every established policy/storage API before overriding the two
# first-write authority functions below.
for _name in dir(_impl):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_impl, _name)


def verify_active_context(agent_id: str, task_id: str, context_token: str) -> dict[str, Any]:
    storage = _impl._impl
    return _impl._canonical_task(storage._load_ready(agent_id, task_id, context_token))


def scope_task_resource(
    agent_id: str,
    task_id: str,
    context_token: str,
    resource: str,
) -> dict[str, Any]:
    """Scope one mutating task to one exact file without multiplying tasks.

    A coarse task may select its first file.  An exact task may move to the
    next file only after the previous file has an MCP-authorized applied-patch
    receipt and all ownership for that file has been released.
    """

    from . import direct_fs_tripwire, patch_queue, resource_locks, task_discovery

    storage = _impl._impl
    data = verify_active_context(agent_id, task_id, context_token)
    canonical = normalize_intent(str(data.get("intent") or ""))
    if canonical not in {"write", "delete"}:
        raise TaskContextError("TASK_RESOURCE_SCOPE_MUTATING_INTENT_REQUIRED", {"intent": canonical})
    current = str(data.get("resource") or "")
    safe = patch_queue.safe_resource(resource)
    if _is_coarse_resource(safe):
        raise TaskContextError("TASK_EXACT_RESOURCE_REQUIRED")
    if current == safe:
        return {
            "task_id": task_id,
            "agent_id": agent_id,
            "resource": safe,
            "intent": canonical,
            "already_scoped": True,
        }
    rescoping_exact_resource = not _is_coarse_resource(current)
    if not rescoping_exact_resource and not _scope_contains(current, safe):
        raise TaskContextError(
            "TASK_RESOURCE_OUTSIDE_CONTAINER_SCOPE",
            {"task_resource": current, "requested_resource": safe},
        )

    patch_queue.ensure_schema()
    resource_locks.ensure_schema()
    ensure_schema()
    with storage.connect() as con:
        active_claims = con.execute(
            "SELECT COUNT(*) FROM claims WHERE agent_id=? AND status='active'",
            (agent_id,),
        ).fetchone()[0]
        pending_patches = con.execute(
            "SELECT COUNT(*) FROM patches_v2 WHERE agent_id=? AND status IN ('proposed','conflict')",
            (agent_id,),
        ).fetchone()[0]
        active_locks = con.execute(
            f"SELECT COUNT(*) FROM {resource_locks.LOCK_TABLE} WHERE agent_id=? AND expires_at>?",
            (agent_id, storage.now_ts()),
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
        if rescoping_exact_resource:
            receipts = direct_fs_tripwire.applied_patch_ids(
                None, task_id, agent_id, resource=current
            )
            if not receipts:
                raise TaskContextError(
                    "TASK_RESOURCE_RESCOPE_PATCH_REQUIRED",
                    {
                        "task_resource": current,
                        "requested_resource": safe,
                        "required_receipt": "MCP_APPLIED_PATCH",
                    },
                )
        request_hash = storage._request_hash(str(data.get("request") or ""), canonical, safe)
        con.execute(
            """
            UPDATE task_context_v2
            SET resource=?,request_hash=?,scribe_done=0,graphify_done=0,
                memory_hash=NULL,scribe_result_count=0,scribe_result_resources='',
                scribe_record_done=0,scribe_record_required=0,scribe_record_policy=NULL,
                scribe_record_path=NULL,scribe_record_digest=NULL,
                scribe_record_promoted=0,scribe_record_entry_id=NULL,
                scribe_record_skip_reason=NULL
            WHERE task_id=? AND agent_id=? AND status='active'
            """,
            (safe, request_hash, task_id, agent_id),
        )
        storage.add_event(
            con,
            "task.resource_scoped",
            {"task_id": task_id, "previous_resource": current, "resource": safe, "intent": canonical},
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
        "rescoping": rescoping_exact_resource,
    }


_BASE_REQUIRE_CONTEXT_READY = _impl.require_context_ready


def require_context_ready(
    agent_id: str,
    task_id: str,
    context_token: str,
    resource: str = "",
    require_graphify: bool | None = None,
    strict_resource: bool = False,
    allowed_intents: set[str] | None = None,
) -> dict[str, Any]:
    data = _BASE_REQUIRE_CONTEXT_READY(
        agent_id,
        task_id,
        context_token,
        resource=resource,
        require_graphify=require_graphify,
        strict_resource=strict_resource,
        allowed_intents=allowed_intents,
    )
    canonical = normalize_intent(str(data.get("intent") or ""))
    result = dict(data)
    result["intent"] = canonical
    if canonical in {"write", "delete"} and bool(result.get("scribe_history_absent")):
        from . import task_discovery
        try:
            task_discovery.require_discovery_ready(
                agent_id,
                task_id,
                resource=resource or str(result.get("resource") or ""),
            )
        except task_discovery.TaskDiscoveryError as exc:
            raise TaskContextError(exc.code, exc.details) from exc
    return result
