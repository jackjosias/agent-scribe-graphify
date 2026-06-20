#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

MCP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MCP_DIR.parents[1]
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

try:
    from runtime.db import (
        CoordinationError,
        before_edit as db_before_edit,
        claim_resource as db_claim_resource,
        finish_task as db_finish_task,
        heartbeat as db_heartbeat,
        init_db,
        register_agent as db_register_agent,
        release_claim as db_release_claim,
        session_status as db_session_status,
    )
    from runtime.state_paths import graphify_report_candidates
    from runtime import patch_queue
except Exception:
    from .runtime.db import (  # type: ignore
        CoordinationError,
        before_edit as db_before_edit,
        claim_resource as db_claim_resource,
        finish_task as db_finish_task,
        heartbeat as db_heartbeat,
        init_db,
        register_agent as db_register_agent,
        release_claim as db_release_claim,
        session_status as db_session_status,
    )
    from .runtime.state_paths import graphify_report_candidates  # type: ignore
    from .runtime import patch_queue  # type: ignore

SERVER_NAME = "agent-scribe-graphify"
SERVER_VERSION = "0.2.2"
ROOT = PROJECT_ROOT.resolve()
AGENT_DIR = ROOT / ".agent"
WRITE_MODES = {"write", "exclusive", "patch_queue"}
WRITE_INTENTS = {"write", "edit", "patch", "modify", "code", "fix", "refactor", "test", "create", "delete"}
FINISH_INTENTS = {"finish", "done", "complete", "end", "finalize"}


class ToolError(RuntimeError):
    pass


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


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
        "agent_id": agent_id,
    })


def _active_agent_ids() -> set[str]:
    init_db(ROOT)
    status = db_session_status()
    return {row.get("agent_id", "") for row in status.get("agents", []) if row.get("status") == "active"}


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
            "Do not finish with pending proposed/conflict patches.",
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
    normalized_mode = mode if mode in WRITE_MODES else "patch_queue"
    last = (last_verdict or "").strip()

    if not AGENT_DIR.is_dir():
        raise ToolError(".agent directory not found from server entrypoint project root")

    if not agent_id or agent_id not in _active_agent_ids():
        return _next_payload(
            state="NO_ACTIVE_AGENT",
            tool="bootstrap",
            args={"host_tool": host_tool or "unknown", "model_name": model_name or "", "run_legacy_bootstrap": False},
            reason="No active registered agent_id is available. Bootstrap is mandatory before any task action.",
            forbidden=["claim_resource", "before_edit", "propose_patch", "finish_task"],
        )

    finish_intent = normalized_intent in FINISH_INTENTS
    write_intent = normalized_intent in WRITE_INTENTS or bool(resource)
    pending = _agent_pending_patches(agent_id, resource)

    if pending and finish_intent:
        return _next_payload(
            state="PATCH_PENDING",
            tool="list_patches",
            args={"target": resource or "", "status": "proposed"},
            reason="This agent still owns proposed/conflict patches. finish_task is forbidden until each patch is confirmed or rejected.",
            forbidden=["finish_task", "direct_file_edit"],
            context={"pending_patches": pending, "acceptable_resolution_tools": ["confirm_patch_applied", "reject_patch"]},
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
                forbidden=["finish_task"],
                context={"active_claims": claims["owned"]},
            )
        return _next_payload(
            state="READY_TO_FINISH",
            tool="finish_task",
            args={"agent_id": agent_id, "summary": "task completed"},
            reason="No pending patches and no active claims remain for this agent.",
            forbidden=["direct_file_edit"],
        )

    if request and last not in {"BEFORE_TASK_OK", "SCRIBE_QUERY_DONE", "GRAPHIFY_QUERY_DONE", "CLAIM_GRANTED", "FILE_HASH", "PATCH_PROPOSED", "PATCH_CONFLICT"}:
        return _next_payload(
            state="TASK_NOT_ACKED",
            tool="before_task",
            args={"request": request, "agent_id": agent_id},
            reason="The task must be acknowledged mechanically before write planning.",
            forbidden=["claim_resource", "before_edit", "propose_patch", "finish_task"],
        )

    if write_intent:
        if not resource:
            return ok({
                "verdict": "INPUT_REQUIRED",
                "state": "RESOURCE_REQUIRED",
                "reason": "A write/patch/edit intent requires an explicit project-relative resource.",
                "required_inputs": ["resource"],
                "forbidden": ["claim_resource", "before_edit", "propose_patch", "finish_task"],
            })
        safe = patch_queue.safe_resource(resource)
        claims = _active_claims_for(agent_id, safe)
        owned_write = [row for row in claims["owned"] if row.get("mode") in WRITE_MODES]
        if not owned_write:
            return _next_payload(
                state="CLAIM_REQUIRED",
                tool="claim_resource",
                args={"agent_id": agent_id, "resource": safe, "mode": normalized_mode, "ttl_seconds": 600},
                reason="A compatible claim is mandatory before any write, edit, or patch proposal.",
                forbidden=["before_edit", "propose_patch", "direct_file_edit", "finish_task"],
                context={"foreign_claims": claims["foreign"]},
            )

        selected_claim = owned_write[0]
        if selected_claim.get("mode") == "patch_queue":
            if not base_hash:
                return _next_payload(
                    state="BASE_HASH_REQUIRED",
                    tool="file_hash",
                    args={"resource": safe},
                    reason="Patch queue requires a fresh base_hash from file_hash before propose_patch.",
                    forbidden=["propose_patch", "direct_file_edit", "finish_task"],
                    context={"claim": selected_claim},
                )
            return _next_payload(
                state="READY_TO_PROPOSE_PATCH",
                tool="propose_patch",
                args={"agent_id": agent_id, "target": safe, "base_hash": base_hash},
                reason="Patch queue claim and base_hash are present. The next MCP action is propose_patch with a real unified diff_text.",
                forbidden=["direct_file_edit", "finish_task"],
                missing_inputs=["diff_text"],
                context={"claim": selected_claim},
            )

        return _next_payload(
            state="DIRECT_EDIT_CHECK_REQUIRED",
            tool="before_edit",
            args={"agent_id": agent_id, "resource": safe},
            reason="A write/exclusive claim exists, but direct editing still requires before_edit immediately before the edit.",
            forbidden=["direct_file_edit_without_before_edit", "finish_task"],
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
    res = run_cmd([str(scribe_rag), "query", query, "--top", str(max(1, min(int(limit), 20)))], timeout=30)
    return ok({"verdict": "SCRIBE_QUERY_DONE", "query": query, "result": res})


def graphify_query(query: str = "", resource: str = "") -> Dict[str, Any]:
    found = []
    for p in graphify_report_candidates(ROOT):
        if p.exists() and p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            found.append({"path": str(p), "excerpt": text[:12000]})
    if not found:
        return ok({"verdict": "GRAPHIFY_UNAVAILABLE", "query": query, "resource": resource, "reason": "No graphify report found in .agent/state/graphify-out or legacy locations"})
    return ok({"verdict": "GRAPHIFY_QUERY_DONE", "query": query, "resource": resource, "results": found})


def claim_resource(agent_id: str, resource: str, mode: str = "write", ttl_seconds: int = 1800) -> Dict[str, Any]:
    return ok(db_claim_resource(agent_id=agent_id, resource=resource, mode=mode, ttl_seconds=ttl_seconds))


def release_claim(agent_id: str, claim_id: str, summary: str = "") -> Dict[str, Any]:
    return ok(db_release_claim(agent_id=agent_id, claim_id=claim_id, summary=summary))


def before_edit(agent_id: str, resource: str) -> Dict[str, Any]:
    safe = patch_queue.safe_resource(resource)
    current = patch_queue.file_hash(safe)
    with patch_queue.connect() as con:
        rows = [dict(row) for row in con.execute("SELECT * FROM claims WHERE resource=? AND status='active'", (safe,)).fetchall()]
    owned = [row for row in rows if row.get("agent_id") == agent_id]
    foreign = [row for row in rows if row.get("agent_id") != agent_id]
    if any(row.get("mode") == "patch_queue" for row in owned):
        return ok({"verdict": "DIRECT_EDIT_REFUSED", "policy": "PATCH_QUEUE_REQUIRED", "reason": "agent owns patch_queue claim", "owned_claims": owned, **current})
    if any(row.get("mode") in WRITE_MODES for row in foreign):
        return ok({"verdict": "DIRECT_EDIT_REFUSED", "policy": "PATCH_QUEUE_REQUIRED", "reason": "foreign write/patch claim active", "foreign_claims": foreign, **current})
    if not any(row.get("mode") in {"write", "exclusive"} for row in owned):
        return ok({"verdict": "DIRECT_EDIT_REFUSED_MISSING_CLAIM", "policy": "CLAIM_REQUIRED", "active_claims": rows, **current})
    return ok({"verdict": "DIRECT_EDIT_ALLOWED", "claims": owned, "legacy_check": db_before_edit(agent_id=agent_id, resource=safe), **current})


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


TOOLS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "bootstrap": bootstrap,
    "register_agent": register_agent,
    "heartbeat": heartbeat,
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
    "list_patches": list_patches,
    "confirm_patch_applied": confirm_patch_applied,
    "reject_patch": reject_patch,
    "installation_required": installation_required,
}


def tool_schema(name: str) -> Dict[str, Any]:
    schemas = {
        "bootstrap": {"host_tool": "string", "model_name": "string", "run_legacy_bootstrap": "boolean"},
        "register_agent": {"host_tool": "string", "model_name": "string", "agent_id": "string"},
        "heartbeat": {"agent_id": "string"},
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
        "list_patches": {"target": "string", "status": "string"},
        "confirm_patch_applied": {"agent_id": "string", "patch_id": "string", "new_hash": "string"},
        "reject_patch": {"agent_id": "string", "patch_id": "string", "reason": "string"},
        "installation_required": {"host_tool": "string"},
    }[name]
    return {"type": "object", "properties": {k: {"type": v} for k, v in schemas.items()}, "additionalProperties": False}


def list_tools() -> List[Dict[str, Any]]:
    return [{"name": name, "description": f"{SERVER_NAME}.{name}", "inputSchema": tool_schema(name)} for name in TOOLS]


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
