from __future__ import annotations

"""Canonical task-context facade with exact first-write discovery binding."""

from typing import Any

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
    return value in _COARSE_RESOURCES or "whole repo" in value


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
    """Narrow one coarse mutating task to one exact file before ownership exists."""

    from . import patch_queue, resource_locks, task_discovery

    storage = _impl._impl
    data = verify_active_context(agent_id, task_id, context_token)
    canonical = normalize_intent(str(data.get("intent") or ""))
    if canonical not in {"write", "delete"}:
        raise TaskContextError("TASK_RESOURCE_SCOPE_MUTATING_INTENT_REQUIRED", {"intent": canonical})
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
        "previous_resource": current°(€€€€€€€€‰É•Í½ÕÉ”ˆèÍ…™”°(€€€€€€€€‰¥¹Ñ•¹Ğˆè…¹½¹¥…°°(€€€€€€€€‰ÍÉ¥‰•}‘½¹”ˆè…±Í”°(€€€€€€€€‰É…Á¥™å}‘½¹”ˆè…±Í”°(€€€€€€€€‰…±É•…‘å}Í½Á•ˆè…±Í”°(€€€ô(()}	M}IEU%I}=9QaQ}Id€ô}¥µÁ°¹É•ÅÕ¥É•}½¹Ñ•áÑ}É•…‘ä(()‘•˜É•ÅÕ¥É•}½¹Ñ•áÑ}É•…‘ä (€€€…•¹Ñ}¥èÍÑÈ°(€€€Ñ…Í­}¥èÍÑÈ°(€€€½¹Ñ•áÑ}Ñ½­•¸èÍÑÈ°(€€€É•Í½ÕÉ”èÍÑÈ€ô€ˆˆ°(€€€É•ÅÕ¥É•}É…Á¡¥™äè‰½½°ğ9½¹”€ô9½¹”°(€€€ÍÑÉ¥Ñ}É•Í½ÕÉ”è‰½½°€ô…±Í”°(€€€…±±½İ•‘}¥¹Ñ•¹ÑÌèÍ•ÑmÍÑÉtğ9½¹”€ô9½¹”°(¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€‘…Ñ„€ô}	M}IEU%I}=9QaQ}Id (€€€€€€€…•¹Ñ}¥°(€€€€€€€Ñ…Í­}¥°(€€€€€€€½¹Ñ•áÑ}Ñ½­•¸°(€€€€€€€É•Í½ÕÉ”õÉ•Í½ÕÉ”°(€€€€€€€É•ÅÕ¥É•}É…Á¥™äõÉ•ÅÕ¥É•}É…Á¡¥™ä°(€€€€€€€ÍÑÉ¥Ñ}É•Í½ÕÉ”õÍÑÉ¥Ñ}É•Í½ÕÉ”°(€€€€€€€…±±½İ•‘}¥¹Ñ•¹ÑÌõ…±±½İ•‘}¥¹Ñ•¹ÑÌ°(€€€€¤(€€€…¹½¹¥…°€ô¹½Éµ…±¥é•}¥¹Ñ•¹Ğ¡ÍÑÈ¡‘…Ñ„¹•Ğ ‰¥¹Ñ•¹Ğˆ¤½È€ˆˆ¤¤(€€€É•ÍÕ±Ğ€ô‘¥Ğ¡‘…Ñ„¤(€€€É•ÍÕ±Ñl‰¥¹Ñ•¹Ğ‰t€ô…¹½¹¥…°(€€€¥˜…¹½¹¥…°¥¸ì‰İÉ¥Ñ”ˆ°€‰‘•±•Ñ”‰ô…¹‰½½°¡É•ÍÕ±Ğ¹•Ğ ‰ÍÉ¥‰•}¡¥ÍÑ½Éå}…‰Í•¹Ğˆ¤¤è(€€€€€€€™É½´€¸¥µÁ½ÉĞÑ…Í­}‘¥Í½Ù•Éä(€€€€€€€ÑÉäè(€€€€€€€€€€€Ñ…Í­}‘¥Í½Ù•Éä¹É•ÅÕ¥É•}‘¥Í½Ù•Éå}É•…‘ä (€€€€€€€€€€€€€€€…•¹Ñ}¥°(€€€€€€€€€€€€€€€Ñ…Í­}¥°(€€€€€€€€€€€€€€€É•Í½ÕÉ”õÉ•Í½ÕÉ”½ÈÍÑÈ¡É•ÍÕ±Ğ¹•Ğ ‰É•Í½ÕÉ”ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€¤(€€€€€€€•á•ÁĞÑ…Í­}‘¥Í½Ù•Éä¹Q…Í­¥Í½Ù•ÉåÉÉ½È…Ì•áŒè(€€€€€€€€€€€É…¥Í”Q…Í­½¹Ñ•áÑÉÉ½È¡•áŒ¹½‘”°•áŒ¹‘•Ñ…¥±Ì¤™É½´•áŒ(€€€É•ÑÕÉ¸É•ÍÕ±Ğ(