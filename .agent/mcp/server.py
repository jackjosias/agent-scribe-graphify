#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as _datetime
import json
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as pool_wait
from pathlib import Path
from typing import Any, Callable, Dict, List

MCP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MCP_DIR.parents[1]
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

try:
    from runtime.db import (
        CoordinationError,
        claim_resource as db_claim_resource,
        agent_status as db_agent_status,
        finish_task as db_finish_task,
        heartbeat as db_heartbeat,
        init_db,
        list_agents as db_list_agents,
        register_agent as db_register_agent,
        release_claim as db_release_claim,
        resume_agent as db_resume_agent,
        retire_agent as db_retire_agent,
        session_status as db_session_status,
    )
    from runtime.state_paths import graphify_report_candidates
    from runtime import patch_queue
except Exception:
    from .runtime.db import (  # type: ignore
        CoordinationError,
        claim_resource as db_claim_resource,
        agent_status as db_agent_status,
        finish_task as db_finish_task,
        heartbeat as db_heartbeat,
        init_db,
        list_agents as db_list_agents,
        register_agent as db_register_agent,
        release_claim as db_release_claim,
        resume_agent as db_resume_agent,
        retire_agent as db_retire_agent,
        session_status as db_session_status,
    )
    from .runtime.state_paths import graphify_report_candidates  # type: ignore
    from .runtime import patch_queue  # type: ignore

SERVER_NAME = "agent-scribe-graphify"
SERVER_VERSION = "0.2.3"
ROOT = PROJECT_ROOT.resolve()
AGENT_DIR = ROOT / ".agent"
WRITE_MODES = {"write", "exclusive", "patch_queue"}
WRITE_INTENTS = {"write", "edit", "patch", "modify", "code", "fix", "refactor", "test", "create", "delete"}
FINISH_INTENTS = {"finish", "done", "complete", "end", "finalize"}


class ToolError(RuntimeError):
    pass


def _json_default(value: Any) -> str:
    if isinstance(value, (_datetime.date, _datetime.datetime)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default)


def ok(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": dumps({"ok": True, **data})}]}


def err(message: str, code: str = "TOOL_ERROR", extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = {"ok": False, "code": code, "error": message}
    if extra:
        payload.update(extra)
    return {"isError": True, "content": [{"type": "text", "text": dumps(payload)}]}


def run_cmd(cmd: List[str], timeout: int = 20) -> Dict[str, Any]:
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, timeout=timeout)
        return {"returncode": proc.returncode, "stdout": proc.stdout[-12000:], "stderr": proc.stderr[-12000:]}
    except FileNotFoundError as exc:
        return {"returncode": 127, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {"returncode": 124, "stdout": (exc.stdout or "")[-12000:], "stderr": f"timeout after {timeout}s"}


def bootstrap(host_tool: str = "unknown", model_name: str = "", run_legacy_bootstrap: bool = False) -> Dict[str, Any]:
    if not AGENT_DIR.is_dir():
        raise ToolError(".agent directory not found from server entrypoint project root")
    result = init_db(ROOT)
    legacy = None
    if run_legacy_bootstrap:
        legacy_cmd = AGENT_DIR / "workflow" / "scribe" / "scribe"
        if legacy_cmd.exists():
            legacy = run_cmd([str(legacy_cmd), "bootstrap"], timeout=40)
    agent = db_register_agent(host_tool=host_tool or "unknown", model_name=model_name or "")
    status = db_session_status()
    return ok({"verdict": "BOOT_OK_MCP", "server": SERVER_NAME, "version": SERVER_VERSION, "runtime": result, "agent": agent, "session": status, "legacy_bootstrap": legacy})


def register_agent(host_tool: str, model_name: str = "", agent_id: str | None = None) -> Dict[str, Any]:
    return ok({"verdict": "AGENT_REGISTERED", "agent": db_register_agent(host_tool=host_tool, model_name=model_name, agent_id=agent_id)})


def heartbeat(agent_id: str) -> Dict[str, Any]:
    return ok({"verdict": "HEARTBEAT_OK", "heartbeat": db_heartbeat(agent_id)})


def list_agents() -> Dict[str, Any]:
    return ok({"verdict": "AGENTS_LISTED", **db_list_agents()})


def agent_status(agent_id: str) -> Dict[str, Any]:
    return ok({"verdict": "AGENT_STATUS", "agent": db_agent_status(agent_id)})


def resume_agent(agent_id: str) -> Dict[str, Any]:
    return ok({"verdict": "AGENT_RESUMED", "agent": db_resume_agent(agent_id)})


def retire_agent(agent_id: str, reason: str = "") -> Dict[str, Any]:
    return ok({"verdict": "AGENT_RETIRED", "agent": db_retire_agent(agent_id, reason=reason)})


def session_status() -> Dict[str, Any]:
    return ok({"verdict": "SESSION_STATUS", "session": db_session_status()})


def classify_request(request: str) -> Dict[str, Any]:
    r = (request or "").lower()
    code_words = ["code", "fonction", "class", "api", "backend", "frontend", "component", "hook", "refactor", "fix", "bug", "test", "module"]
    critical_words = ["auth", "security", "payment", "database", "migration", "registry", "concurrent", "multi-agent", "production"]
    is_code = any(w in r for w in code_words)
    is_critical = any(w in r for w in critical_words)
    tier = "CRITICAL" if is_critical else "STANDARD" if is_code else "NANO"
    required = ["scribe_query"]
    if is_code:
        required.append("graphify_query")
    if is_critical:
        required.append("claim_resource")
    return {"tier": tier, "is_code": is_code, "is_critical": is_critical, "required_steps": required}


def before_task(request: str, agent_id: str = "") -> Dict[str, Any]:
    if not request or not isinstance(request, str):
        raise ToolError("request is required")
    policy = classify_request(request)
    return ok({
        "verdict": "BEFORE_TASK_OK",
        "request": request,
        "policy": policy,
        "mechanical_rule": "After this tool, call workflow_next again. Do not guess the next step.",
        "write_gate": "All accepted writes must go through propose_patch then apply_patch.",
        "agent_id": agent_id,
    })


def _agent_states() -> Dict[str, str]:
    init_db(ROOT)
    status = db_session_status()
    return {row.get("agent_id", ""): row.get("status", "") for row in status.get("agents", [])}


def _active_agent_ids() -> set[str]:
    return {agent_id for agent_id, status in _agent_states().items() if status == "active"}


def _safe_now() -> int:
    return int(time.time())


def _active_claims_for(agent_id: str, resource: str = "") -> Dict[str, Any]:
    init_db(ROOT)
    patch_queue.ensure_schema()
    now = _safe_now()
    safe = patch_queue.safe_resource(resource) if resource else ""
    with patch_queue.connect() as con:
        if safe:
            rows = [dict(row) for row in con.execute(
                "SELECT * FROM claims WHERE resource=? AND status='active' AND expires_at>=? ORDER BY created_at ASC",
                (safe, now),
            ).fetchall()]
        else:
            rows = [dict(row) for row in con.execute(
                "SELECT * FROM claims WHERE agent_id=? AND status='active' AND expires_at>=? ORDER BY created_at ASC",
                (agent_id, now),
            ).fetchall()]
    owned = [row for row in rows if row.get("agent_id") == agent_id]
    foreign = [row for row in rows if row.get("agent_id") != agent_id]
    return {"resource": safe, "owned": owned, "foreign": foreign, "all": rows}


def _agent_pending_patches(agent_id: str, resource: str = "") -> List[Dict[str, Any]]:
    patch_queue.ensure_schema()
    target = patch_queue.safe_resource(resource) if resource else None
    pending = patch_queue.list_patches(target=target, status="proposed")["patches"]
    conflicts = patch_queue.list_patches(target=target, status="conflict")["patches"]
    return [patch for patch in pending + conflicts if patch.get("agent_id") == agent_id]


def _next_payload(
    state: str,
    tool: str,
    args: Dict[str, Any],
    reason: str,
    forbidden: List[str] | None = None,
    missing_inputs: List[str] | None = None,
    context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return ok({
        "verdict": "NEXT_ACTION_REQUIRED",
        "state": state,
        "must_call": {"tool": tool, "args": args},
        "missing_inputs": missing_inputs or [],
        "forbidden": forbidden or [],
        "reason": reason,
        "context": context or {},
        "invariants": [
            "Do not invent MCP results.",
            "Do not edit without a compatible claim.",
            "All accepted writes must pass through MCP apply_patch unless a future sandbox layer explicitly says otherwise.",
            "Do not finish with pending proposed/conflict patches or active claims.",
            "Call workflow_next again after executing must_call.",
        ],
    })


def workflow_next(
    agent_id: str = "",
    request: str = "",
    intent: str = "",
    resource: str = "",
    mode: str = "patch_queue",
    base_hash: str = "",
    patch_id: str = "",
    claim_id: str = "",
    last_verdict: str = "",
    host_tool: str = "unknown",
    model_name: str = "",
) -> Dict[str, Any]:
    normalized_intent = (intent or "").strip().lower()
    normalized_mode = "patch_queue" if mode in WRITE_MODES else "patch_queue"
    last = (last_verdict or "").strip()

    if not AGENT_DIR.is_dir():
        raise ToolError(".agent directory not found from server entrypoint project root")

    states = _agent_states()
    if not agent_id:
        return ok({
            "verdict": "INPUT_REQUIRED",
            "state": "AGENT_ID_REQUIRED",
            "reason": "A durable agent_id is required. Call register_agent explicitly; workflow_next never bootstraps implicitly.",
            "required_inputs": ["agent_id"],
            "forbidden": ["bootstrap", "claim_resource", "before_edit", "propose_patch", "apply_patch", "finish_task", "direct_file_edit"],
        })
    agent_state = states.get(agent_id)
    if agent_state is None:
        return ok({
            "verdict": "AGENT_UNKNOWN_OR_UNREGISTERED",
            "state": "AGENT_UNKNOWN_OR_UNREGISTERED",
            "reason": "Unknown agent identity. Register or resume explicitly; no hidden bootstrap or agent rotation is allowed.",
            "required_inputs": ["register_agent.agent_id"],
            "forbidden": ["bootstrap", "claim_resource", "before_edit", "propose_patch", "apply_patch", "finish_task", "direct_file_edit"],
        })
    if agent_state != "active":
        return ok({
            "verdict": "AGENT_IDLE_RESUME_REQUIRED",
            "state": "AGENT_IDLE_RESUME_REQUIRED",
            "agent_status": agent_state,
            "reason": "Agent identity exists but presence is not active. Call resume_agent or heartbeat explicitly; do not create a replacement agent.",
            "must_call": {"tool": "resume_agent", "args": {"agent_id": agent_id}},
            "forbidden": ["bootstrap", "claim_resource", "before_edit", "propose_patch", "apply_patch", "finish_task", "direct_file_edit"],
        })

    finish_intent = normalized_intent in FINISH_INTENTS
    write_intent = normalized_intent in WRITE_INTENTS or bool(resource)
    pending = _agent_pending_patches(agent_id, resource)

    if pending and finish_intent:
        return _next_payload(
            state="PATCH_PENDING",
            tool="list_patches",
            args={"target": resource or "", "status": "proposed"},
            reason="This agent still owns proposed/conflict patches. finish_task is forbidden until each patch is applied or rejected.",
            forbidden=["finish_task", "direct_file_edit"],
            context={"pending_patches": pending, "acceptable_resolution_tools": ["apply_patch", "reject_patch"]},
        )

    if pending and not finish_intent:
        proposed = [patch for patch in pending if patch.get("status") == "proposed"]
        if proposed:
            selected = proposed[0]
            return _next_payload(
                state="PATCH_READY_TO_APPLY",
                tool="apply_patch",
                args={"agent_id": agent_id, "patch_id": selected["patch_id"]},
                reason="A proposed patch exists. The next accepted write must be performed by MCP apply_patch, not by a host direct file edit.",
                forbidden=["direct_file_edit", "finish_task", "confirm_patch_applied"],
                context={"patch": selected},
            )
        return _next_payload(
            state="PATCH_CONFLICT_REQUIRES_REVIEW",
            tool="list_patches",
            args={"target": resource or "", "status": "conflict"},
            reason="A conflict patch exists. It must be reviewed and rejected or resolved before more writes.",
            forbidden=["direct_file_edit", "finish_task", "apply_patch"],
            context={"pending_patches": pending},
        )

    if finish_intent:
        claims = _active_claims_for(agent_id)
        if claims["owned"]:
            selected = claims["owned"][0]
            return _next_payload(
                state="ACTIVE_CLAIM_BEFORE_FINISH",
                tool="release_claim",
                args={"agent_id": agent_id, "claim_id": selected["claim_id"], "summary": "release before finish"},
                reason="Active claims must be released before finish_task.",
                forbidden=["finish_task", "direct_file_edit"],
                context={"active_claims": claims["owned"]},
            )
        return _next_payload(
            state="READY_TO_FINISH",
            tool="finish_task",
            args={"agent_id": agent_id, "summary": "task completed"},
            reason="No pending patches and no active claims remain for this agent.",
            forbidden=["direct_file_edit"],
        )

    if last.startswith("TASK_CONTEXT") or last in {"READ_INTENT_CANNOT_WRITE", "ACTIVE_TASK_EXISTS"}:
        retry = __import__("runtime.task_context", fromlist=["record_retry"]).record_retry(agent_id, resource, last)
        if retry.get("verdict") in {"RETRY_LOOP_DETECTED", "MAX_WORKFLOW_RETRIES_EXCEEDED"}:
            return ok({
                "verdict": retry["verdict"],
                "state": retry["verdict"],
                "reason": "The same agent/resource/error repeated. Stop instead of retrying or falling back to direct writes.",
                "retry": retry,
                "forbidden": ["bootstrap", "before_task", "claim_resource", "before_edit", "propose_patch", "apply_patch", "finish_task", "direct_file_edit"],
            })

    if request and last not in {"BEFORE_TASK_OK", "SCRIBE_QUERY_DONE", "GRAPHIFY_QUERY_DONE", "CLAIM_GRANTED", "FILE_HASH", "PATCH_PROPOSED", "PATCH_CONFLICT", "PATCH_APPLIED"}:
        return _next_payload(
            state="TASK_NOT_ACKED",
            tool="before_task",
            args={"request": request, "agent_id": agent_id},
            reason="The task must be acknowledged mechanically before write planning.",
            forbidden=["claim_resource", "before_edit", "propose_patch", "apply_patch", "finish_task", "direct_file_edit"],
        )

    if write_intent:
        if not resource:
            return ok({
                "verdict": "INPUT_REQUIRED",
                "state": "RESOURCE_REQUIRED",
                "reason": "A write/patch/edit intent requires an explicit project-relative resource.",
                "required_inputs": ["resource"],
                "forbidden": ["claim_resource", "before_edit", "propose_patch", "apply_patch", "finish_task", "direct_file_edit"],
            })
        safe = patch_queue.safe_resource(resource)
        claims = _active_claims_for(agent_id, safe)
        owned_write = [row for row in claims["owned"] if row.get("mode") in WRITE_MODES]
        if not owned_write:
            return _next_payload(
                state="CLAIM_REQUIRED",
                tool="claim_resource",
                args={"agent_id": agent_id, "resource": safe, "mode": normalized_mode, "ttl_seconds": 600},
                reason="A compatible claim is mandatory before any write. workflow_next always routes writes through patch_queue/apply_patch.",
                forbidden=["before_edit", "propose_patch", "apply_patch", "direct_file_edit", "finish_task"],
                context={"foreign_claims": claims["foreign"]},
            )

        selected_claim = owned_write[0]
        if not base_hash:
            return _next_payload(
                state="BASE_HASH_REQUIRED",
                tool="file_hash",
                args={"resource": safe},
                reason="MCP write gate requires a fresh base_hash from file_hash before propose_patch.",
                forbidden=["propose_patch", "apply_patch", "direct_file_edit", "finish_task"],
                context={"claim": selected_claim},
            )
        return _next_payload(
            state="READY_TO_PROPOSE_PATCH",
            tool="propose_patch",
            args={"agent_id": agent_id, "target": safe, "base_hash": base_hash},
            reason="Claim and base_hash are present. The next action is propose_patch with a real unified diff_text; actual writing must then happen through apply_patch.",
            forbidden=["direct_file_edit", "before_edit", "finish_task"],
            missing_inputs=["diff_text"],
            context={"claim": selected_claim},
        )

    return ok({
        "verdict": "INPUT_REQUIRED",
        "state": "NO_ACTION_INFERRED",
        "reason": "Provide request/intent/resource or intent=finish so workflow_next can return a mechanical next tool.",
        "required_inputs": ["request", "intent", "resource"],
        "forbidden": ["direct_file_edit", "finish_task"],
    })


def scribe_query(query: str, limit: int = 5) -> Dict[str, Any]:
    if not query:
        raise ToolError("query is required")
    scribe_rag = AGENT_DIR / "workflow" / "scribe" / "scribe-rag"
    if not scribe_rag.exists():
        return ok({"verdict": "SCRIBE_UNAVAILABLE", "query": query, "reason": "scribe-rag not found"})
    res = run_cmd([str(scribe_rag), "query", query, "--limit", str(max(1, min(int(limit), 20)))], timeout=30)
    return ok({"verdict": "SCRIBE_QUERY_DONE", "query": query, "result": res})


def graphify_query(query: str = "", resource: str = "") -> Dict[str, Any]:
    found = []
    for p in graphify_report_candidates(ROOT):
        if p.exists() and p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            found.append({"path": str(p), "excerpt": text[:12000]})
    if not found:
        return ok({"verdict": "GRAPHIFY_UNAVAILABLE", "query": query, "resource": resource, "reason": "No graphify report found in graphify-out or legacy locations"})
    return ok({"verdict": "GRAPHIFY_QUERY_DONE", "query": query, "resource": resource, "results": found})


def claim_resource(agent_id: str, resource: str, mode: str = "write", ttl_seconds: int = 1800) -> Dict[str, Any]:
    requested_mode = "patch_queue" if mode in WRITE_MODES else mode
    return ok(db_claim_resource(agent_id=agent_id, resource=resource, mode=requested_mode, ttl_seconds=ttl_seconds))


def release_claim(agent_id: str, claim_id: str, summary: str = "") -> Dict[str, Any]:
    return ok(db_release_claim(agent_id=agent_id, claim_id=claim_id, summary=summary))


def before_edit(agent_id: str, resource: str) -> Dict[str, Any]:
    safe = patch_queue.safe_resource(resource)
    current = patch_queue.file_hash(safe)
    return ok({
        "verdict": "DIRECT_EDIT_REFUSED_MCP_WRITE_GATE",
        "policy": "MCP_APPLY_PATCH_REQUIRED",
        "reason": "Direct host file edits are not accepted in V2.3. Use workflow_next → file_hash → propose_patch → apply_patch.",
        "agent_id": agent_id,
        **current,
    })


def finish_task(agent_id: str, summary: str = "") -> Dict[str, Any]:
    pending = patch_queue.list_patches(status="proposed")["patches"] + patch_queue.list_patches(status="conflict")["patches"]
    mine = [patch for patch in pending if patch.get("agent_id") == agent_id]
    if mine:
        return ok({"verdict": "FINISH_REFUSED_PENDING_PATCHES", "pending_patches": mine})
    return ok(db_finish_task(agent_id=agent_id, summary=summary))


def file_hash(resource: str) -> Dict[str, Any]:
    return ok({"verdict": "FILE_HASH", **patch_queue.file_hash(resource)})


def propose_patch(agent_id: str, target: str, base_hash: str, diff_text: str) -> Dict[str, Any]:
    return ok(patch_queue.propose_patch(agent_id=agent_id, target=target, base_hash=base_hash, diff_text=diff_text))


def apply_patch(agent_id: str, patch_id: str) -> Dict[str, Any]:
    return ok(patch_queue.apply_patch(agent_id=agent_id, patch_id=patch_id))


def list_patches(target: str = "", status: str = "") -> Dict[str, Any]:
    return ok(patch_queue.list_patches(target=target or None, status=status or None))


def confirm_patch_applied(agent_id: str, patch_id: str, new_hash: str) -> Dict[str, Any]:
    if not agent_id or not patch_id or not new_hash:
        raise ToolError("agent_id, patch_id and new_hash are required")
    patch_queue.ensure_schema()
    with patch_queue.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute("SELECT * FROM patches_v2 WHERE patch_id=?", (patch_id,)).fetchone()
            if not row:
                raise ToolError("unknown patch_id")
            if row["agent_id"] != agent_id:
                raise ToolError("only patch owner can confirm it")
            current = patch_queue.file_hash(row["target_path"])["hash"]
            if current != new_hash:
                raise ToolError("new_hash does not match current file hash")
            con.execute("UPDATE patches_v2 SET status='applied',updated_at=? WHERE patch_id=?", (_safe_now(), patch_id))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return ok({"verdict": "PATCH_APPLIED_CONFIRMED", "patch_id": patch_id, "new_hash": new_hash})


def reject_patch(agent_id: str, patch_id: str, reason: str) -> Dict[str, Any]:
    if not agent_id or not patch_id or not reason:
        raise ToolError("agent_id, patch_id and reason are required")
    patch_queue.ensure_schema()
    with patch_queue.connect() as con:
        con.execute("UPDATE patches_v2 SET status='rejected',updated_at=?,reason=? WHERE patch_id=? AND agent_id=? AND status IN ('proposed','conflict')", (_safe_now(), reason, patch_id, agent_id))
    return ok({"verdict": "PATCH_REJECTED", "patch_id": patch_id, "reason": reason})


def installation_required(host_tool: str = "unknown") -> Dict[str, Any]:
    return ok({
        "verdict": "MCP_INSTALLATION_REQUIRED",
        "host_tool": host_tool,
        "server_name": SERVER_NAME,
        "command": sys.executable or "python3",
        "args": [".agent/mcp/server_entry.py"],
        "message": "Autorise ce serveur MCP dans ton outil agentique, puis relance TENOR INIT.",
    })


PER_FILE_TIMEOUT = 15


def batch_file_hash(resources: List[str] | None = None, max_workers: int = 4) -> Dict[str, Any]:
    if not resources:
        return ok({"verdict": "BATCH_FILE_HASH", "count": 0, "results": [], "errors": []})
    count = len(resources)
    if count > 100:
        raise ToolError("batch_file_hash limited to 100 resources per call")
    actual_workers = min(max(max_workers, 1), 8, count)
    results: list[Dict[str, Any]] = []
    errors: list[Dict[str, Any]] = []
    resolved: list[str] = []
    for res in resources:
        if not isinstance(res, str) or not res.strip():
            errors.append({"resource": str(res), "error": "invalid resource path"})
            continue
        resolved.append(res.strip())
    with ThreadPoolExecutor(max_workers=actual_workers) as pool:
        fut_map: dict[Any, str] = {pool.submit(patch_queue.file_hash, rn): rn for rn in resolved}
        timeout_seconds = max(PER_FILE_TIMEOUT, PER_FILE_TIMEOUT * len(resolved))
        deadline = time.monotonic() + timeout_seconds
        while fut_map:
            remaining = max(0.0, deadline - time.monotonic())
            done, not_done = pool_wait(fut_map.keys(), timeout=min(remaining, PER_FILE_TIMEOUT), return_when=FIRST_COMPLETED)
            if not done:
                for fut in not_done:
                    res_name = fut_map[fut]
                    fut.cancel()
                    errors.append({"resource": res_name, "error": "timeout"})
                fut_map.clear()
                break
            for fut in done:
                res_name = fut_map.pop(fut)
                try:
                    exc = fut.exception(timeout=1)
                    if exc:
                        errors.append({"resource": res_name, "error": str(exc)})
                    else:
                        data = fut.result(timeout=1)
                        results.append({"resource": res_name, "verdict": "FILE_HASH", "exists": data.get("exists", False), "hash": data.get("hash", ""), "size_bytes": data.get("size_bytes", 0)})
                except Exception as exc:
                    errors.append({"resource": res_name, "error": str(exc)})
    verdict = "BATCH_FILE_HASH" if not errors else "BATCH_FILE_HASH_PARTIAL"
    results.sort(key=lambda r: r.get("resource", ""))
    return ok({"verdict": verdict, "count": len(results), "results": results, "errors": errors})


TOOLS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "bootstrap": bootstrap,
    "register_agent": register_agent,
    "heartbeat": heartbeat,
    "list_agents": list_agents,
    "agent_status": agent_status,
    "resume_agent": resume_agent,
    "retire_agent": retire_agent,
    "session_status": session_status,
    "workflow_next": workflow_next,
    "before_task": before_task,
    "scribe_query": scribe_query,
    "graphify_query": graphify_query,
    "claim_resource": claim_resource,
    "release_claim": release_claim,
    "before_edit": before_edit,
    "finish_task": finish_task,
    "file_hash": file_hash,
    "propose_patch": propose_patch,
    "apply_patch": apply_patch,
    "list_patches": list_patches,
    "confirm_patch_applied": confirm_patch_applied,
    "reject_patch": reject_patch,
    "installation_required": installation_required,
    "batch_file_hash": batch_file_hash,
}

# Filled by the final extension layer. Legacy tools remain callable by the
# internal compatibility engine, but only this bounded surface is advertised
# to host LLMs.
PUBLIC_TOOL_NAMES: tuple[str, ...] = ()


def tool_schema(name: str) -> Dict[str, Any]:
    schemas = {
        "bootstrap": {"host_tool": "string", "model_name": "string", "run_legacy_bootstrap": "boolean"},
        "register_agent": {"host_tool": "string", "model_name": "string", "agent_id": "string"},
        "heartbeat": {"agent_id": "string"},
        "list_agents": {},
        "agent_status": {"agent_id": "string"},
        "resume_agent": {"agent_id": "string"},
        "retire_agent": {"agent_id": "string", "reason": "string"},
        "session_status": {},
        "workflow_next": {
            "agent_id": "string",
            "request": "string",
            "intent": "string",
            "resource": "string",
            "mode": "string",
            "base_hash": "string",
            "patch_id": "string",
            "claim_id": "string",
            "last_verdict": "string",
            "host_tool": "string",
            "model_name": "string",
        },
        "before_task": {"request": "string", "agent_id": "string"},
        "scribe_query": {"query": "string", "limit": "integer"},
        "graphify_query": {"query": "string", "resource": "string"},
        "claim_resource": {"agent_id": "string", "resource": "string", "mode": "string", "ttl_seconds": "integer"},
        "release_claim": {"agent_id": "string", "claim_id": "string", "summary": "string"},
        "before_edit": {"agent_id": "string", "resource": "string"},
        "finish_task": {"agent_id": "string", "summary": "string"},
        "file_hash": {"resource": "string"},
        "propose_patch": {"agent_id": "string", "target": "string", "base_hash": "string", "diff_text": "string"},
        "apply_patch": {"agent_id": "string", "patch_id": "string"},
        "list_patches": {"target": "string", "status": "string"},
        "confirm_patch_applied": {"agent_id": "string", "patch_id": "string", "new_hash": "string"},
        "reject_patch": {"agent_id": "string", "patch_id": "string", "reason": "string"},
        "installation_required": {"host_tool": "string"},
        "batch_file_hash": {"resources": "array", "max_workers": "integer"},
    }[name]
    props: dict[str, dict[str, Any]] = {}
    for k, v in schemas.items():
        if v == "array":
            props[k] = {"type": "array", "items": {"type": "string"}}
        else:
            props[k] = {"type": v}
    return {"type": "object", "properties": props, "additionalProperties": False}


def list_tools() -> List[Dict[str, Any]]:
    names = PUBLIC_TOOL_NAMES or tuple(TOOLS)
    return [
        {
            "name": name,
            "description": f"{SERVER_NAME}.{name}",
            "inputSchema": tool_schema(name),
        }
        for name in names
        if name in TOOLS
    ]


def handle(req: Dict[str, Any]) -> Dict[str, Any]:
    method = req.get("method")
    req_id = req.get("id")
    try:
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}, "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": list_tools()}
        elif method == "tools/call":
            params = req.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name not in TOOLS:
                raise ToolError(f"unknown tool: {name}")
            result = TOOLS[name](**args)
        else:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except (ToolError, CoordinationError, TypeError, ValueError) as exc:
        return {"jsonrpc": "2.0", "id": req_id, "result": err(str(exc))}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": req_id, "result": err(str(exc), code="UNEXPECTED_ERROR")}


def run_stdio() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            print(json.dumps(handle(req), ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-tools", action="store_true")
    parser.add_argument("--call")
    parser.add_argument("--args", default="{}")
    ns = parser.parse_args()
    if ns.list_tools:
        print(dumps({"server": SERVER_NAME, "tools": list_tools()}))
        return 0
    if ns.call:
        if ns.call not in TOOLS:
            print(dumps({"ok": False, "error": f"unknown tool: {ns.call}"}), file=sys.stderr)
            return 2
        try:
            args = json.loads(ns.args)
            print(dumps(TOOLS[ns.call](**args)))
            return 0
        except Exception as exc:
            print(dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 1
    run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
