from __future__ import annotations

from typing import Any, Dict

from runtime import task_context, task_discovery
from runtime.first_write_scribe import FORBIDDEN

_COARSE = {
    "", ".", "(whole repo)", "whole repo", "whole-repo",
    "repository", "repo", "project", "project-wide",
}


def _coarse(resource: str) -> bool:
    value = (resource or "").strip().lower()
    return value in _COARSE or "whole repo" in value


def build_workflow_next(server: Any, base_workflow: Any):
    def workflow_next(
        agent_id: str = "", request: str = "", intent: str = "",
        resource: str = "", mode: str = "patch_queue", base_hash: str = "",
        patch_id: str = "", claim_id: str = "", last_verdict: str = "",
        host_tool: str = "unknown", model_name: str = "",
        task_id: str = "", context_token: str = "",
    ) -> Dict[str, Any]:
        task = None
        if task_id:
            try:
                task = task_context.task_status(task_id)
            except task_context.TaskContextError:
                task = None
        canonical = task_context.normalize_intent(
            str((task or {}).get("intent") or intent or "")
        )
        if (
            task and task.get("status") == "active"
            and canonical in {"write", "delete"}
            and _coarse(str(task.get("resource") or ""))
        ):
            requested = (resource or "").strip()
            if not requested or _coarse(requested):
                return server.ok({
                    "ok": False,
                    "verdict": "TASK_EXACT_RESOURCE_REQUIRED",
                    "state": "RESOURCE_SCOPING_REQUIRED",
                    "reason": (
                        "Whole-repository discovery may inspect broadly, but "
                        "writes require one exact project-relative file."
                    ),
                    "required_inputs": ["resource"],
                    "forbidden": FORBIDDEN,
                })
            return server._next_payload(
                state="RESOURCE_SCOPING_REQUIRED",
                tool="scope_task_resource",
                args={
                    "agent_id": agent_id, "task_id": task_id,
                    "context_token": context_token, "resource": requested,
                },
                reason=(
                    "Narrow the active discovery task to the exact file before "
                    "targeted context, locks, claims or patches."
                ),
                forbidden=FORBIDDEN,
                context={
                    "previous_resource": str(task.get("resource") or ""),
                    "required_resource": requested,
                },
            )

        active_miss = bool(
            task and task.get("status") == "active"
            and canonical in {"write", "delete"}
            and task_discovery.scribe_miss_exists(task_id)
        )
        if active_miss:
            stored = str(task.get("resource") or "")
            requested = (resource or stored).strip()
            if requested and stored and requested != stored:
                return server.ok({
                    "ok": False, "verdict": "TASK_CONTEXT_RESOURCE_MISMATCH",
                    "state": "HARD_STOP",
                    "reason": "Requested resource differs from active task resource.",
                    "forbidden": FORBIDDEN,
                })
            if bool(task.get("requires_graphify")) and not bool(task.get("graphify_done")):
                return server._next_payload(
                    state="GRAPHIFY_CONTEXT_REQUIRED", tool="graphify_query",
                    args={
                        "agent_id": agent_id, "task_id": task_id,
                        "context_token": context_token,
                        "query": f"impact dependencies blast radius for {stored}",
                        "resource": stored,
                    },
                    reason="Graphify evidence is required before first-write discovery.",
                    forbidden=FORBIDDEN,
                )
            discovery = task_discovery.status(task_id)
            if not discovery.get("discovery_done"):
                if not base_hash:
                    return server._next_payload(
                        state="DISCOVERY_BASE_HASH_REQUIRED", tool="file_hash",
                        args={"resource": stored},
                        reason=(
                            "First-write discovery must bind to the exact current "
                            "file hash before any lock or claim."
                        ),
                        forbidden=FORBIDDEN,
                    )
                return server._next_payload(
                    state="FIRST_WRITE_DISCOVERY_REQUIRED",
                    tool="record_task_discovery",
                    args={
                        "agent_id": agent_id, "task_id": task_id,
                        "context_token": context_token, "resource": stored,
                        "base_hash": base_hash,
                    },
                    reason=(
                        "Record concrete task-local discovery. It is not canonical "
                        "SCRIBE and cannot replace validated finish memory."
                    ),
                    forbidden=FORBIDDEN,
                    missing_inputs=["summary", "evidence"],
                    context={"resource": stored, "base_hash": base_hash},
                )
            if last_verdict in {
                "SCRIBE_CONTEXT_MISS_FOR_WRITE",
                "TASK_DISCOVERY_RECORDED", "FILE_HASH",
            }:
                last_verdict = (
                    "GRAPHIFY_QUERY_DONE"
                    if bool(task.get("requires_graphify"))
                    else "SCRIBE_QUERY_DONE"
                )
        return base_workflow(
            agent_id=agent_id, request=request, intent=intent,
            resource=resource, mode=mode, base_hash=base_hash,
            patch_id=patch_id, claim_id=claim_id,
            last_verdict=last_verdict, host_tool=host_tool,
            model_name=model_name, task_id=task_id,
            context_token=context_token,
        )
    return workflow_next
