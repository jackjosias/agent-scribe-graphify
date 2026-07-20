from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from runtime import (
    canonical_memory_gate,
    db,
    direct_fs_tripwire,
    presence,
    task_context,
    tenor_changeset,
    tenor_decision,
    tenor_memory_admission,
)


PUBLIC_TASK_TOOLS = (
    "tenor_task_start",
    "tenor_apply_changeset",
    "tenor_activity",
    "tenor_task_control",
)
PUBLIC_BOOTSTRAP_TOOLS = (
    "file_hash",
    "tenor_init_bridge",
    "portability_check",
    "graphify_required_check",
    "graphify_project_build",
)
PUBLIC_TOOL_NAMES = PUBLIC_BOOTSTRAP_TOOLS + PUBLIC_TASK_TOOLS
ACTIVITY_TABLE = "tenor_task_activity_v1"
MAX_ACTIVITY_HISTORY = 100

_SERVER: Any = None
_BASE_SCHEMA: Any = None
_BASE_BRIDGE: Any = None
_TASK_TOKENS: dict[str, str] = {}


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(result["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {}


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    if _SERVER is None:
        return payload
    return _SERVER.ok(payload)


def _root() -> Path:
    if _SERVER is None:
        raise RuntimeError("TENOR_PUBLIC_API_NOT_INSTALLED")
    return Path(_SERVER.ROOT).resolve()


def ensure_schema(project_root: Path | None = None) -> None:
    root = (project_root or _root()).resolve()
    db.init_db(root)
    with db.connect(root) as con:
        con.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {ACTIVITY_TABLE}(
              task_id TEXT PRIMARY KEY,
              agent_id TEXT NOT NULL,
              objective TEXT NOT NULL,
              intent TEXT NOT NULL,
              scope TEXT NOT NULL,
              resources_json TEXT NOT NULL,
              status TEXT NOT NULL,
              current_action TEXT NOT NULL,
              last_action TEXT NOT NULL,
              next_action TEXT NOT NULL,
              last_changeset_id TEXT NOT NULL DEFAULT '',
              decision_capsule_hash TEXT NOT NULL DEFAULT '',
              memory_decision TEXT NOT NULL DEFAULT '',
              memory_reason TEXT NOT NULL DEFAULT '',
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              finished_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_{ACTIVITY_TABLE}_agent_status
              ON {ACTIVITY_TABLE}(agent_id,status,updated_at);
            """
        )
        columns = {str(row["name"]) for row in con.execute(f"PRAGMA table_info({ACTIVITY_TABLE})").fetchall()}
        for column, declaration in (
            ("decision_capsule_hash", "TEXT NOT NULL DEFAULT ''"),
            ("memory_decision", "TEXT NOT NULL DEFAULT ''"),
            ("memory_reason", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in columns:
                con.execute(f"ALTER TABLE {ACTIVITY_TABLE} ADD COLUMN {column} {declaration}")


def _bound_agent() -> tuple[str, dict[str, Any] | None]:
    if _SERVER is None:
        return "", {"ok": False, "verdict": "TENOR_PUBLIC_API_NOT_INSTALLED"}
    agent_id = str(getattr(_SERVER, "_MCP_BOUND_AGENT_ID", "") or "").strip()
    if not agent_id:
        return "", {
            "ok": False,
            "verdict": "TENOR_SESSION_NOT_BOUND",
            "state": "TENOR_INIT_REQUIRED",
            "reason": "tenor_init_bridge must succeed in this MCP process before task tools are used.",
        }
    try:
        agent = db.get_agent(agent_id, _root())
        if not agent:
            return "", {"ok": False, "verdict": "AGENT_UNKNOWN_OR_UNREGISTERED"}
        if agent.get("status") == "idle":
            db.resume_agent(agent_id, _root())
        elif agent.get("status") != "active":
            return "", {"ok": False, "verdict": f"AGENT_{str(agent.get('status') or 'NOT_ACTIVE').upper()}"}
        db.require_agent_active(agent_id, _root())
    except db.CoordinationError as exc:
        return "", {"ok": False, "verdict": str(exc)}
    return agent_id, None


def _normalize_intent(intent: str) -> str:
    value = (intent or "write").strip().lower()
    aliases = {
        "read": "read",
        "inspect": "read",
        "query": "read",
        "write": "write",
        "edit": "write",
        "patch": "write",
        "fix": "write",
        "create": "write",
        "delete": "delete",
        "remove": "delete",
    }
    if value not in aliases:
        raise ValueError("TENOR_TASK_INTENT_INVALID")
    return aliases[value]


def _normalize_resource_path(raw: str, *, allow_project_root: bool) -> str:
    value = str(raw or "").strip().replace("\\", "/").rstrip("/")
    if value == "." and allow_project_root:
        return value
    path = Path(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("TENOR_TASK_RESOURCE_PATH_INVALID")
    return path.as_posix()


def _normalize_resources(resources: list[str] | None, scope: str, intent: str) -> tuple[list[str], str]:
    values: list[str] = []
    for resource in resources or []:
        value = _normalize_resource_path(resource, allow_project_root=True)
        if value and value not in values:
            values.append(value)
    if intent in {"write", "delete"} and not values:
        raise ValueError("TENOR_TASK_RESOURCES_REQUIRED")
    raw_scope = (scope or "").strip()
    clean_scope = _normalize_resource_path(raw_scope, allow_project_root=True) if raw_scope else ""
    if not clean_scope:
        if len(values) == 1:
            clean_scope = values[0]
        elif values:
            try:
                clean_scope = os.path.commonpath(values).replace("\\", "/")
            except ValueError:
                clean_scope = "."
        elif intent == "read":
            clean_scope = "."
    if not values and clean_scope:
        values = [clean_scope]
    return values, clean_scope


def _write_activity(
    task_id: str,
    agent_id: str,
    objective: str,
    intent: str,
    scope: str,
    resources: list[str],
    *,
    status: str,
    current_action: str,
    last_action: str,
    next_action: str,
) -> None:
    ensure_schema()
    now = int(time.time())
    with db.connect(_root()) as con:
        con.execute(
            f"""
            INSERT INTO {ACTIVITY_TABLE}(
              task_id,agent_id,objective,intent,scope,resources_json,status,
              current_action,last_action,next_action,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(task_id) DO UPDATE SET
              objective=excluded.objective,
              intent=excluded.intent,
              scope=excluded.scope,
              resources_json=excluded.resources_json,
              status=excluded.status,
              current_action=excluded.current_action,
              last_action=excluded.last_action,
              next_action=excluded.next_action,
              updated_at=excluded.updated_at
            """,
            (
                task_id,
                agent_id,
                objective,
                intent,
                scope,
                json.dumps(resources, ensure_ascii=False),
                status,
                current_action,
                last_action,
                next_action,
                now,
                now,
            ),
        )
        db.add_event(
            con,
            "tenor.task_activity",
            {
                "task_id": task_id,
                "status": status,
                "current_action": current_action,
                "last_action": last_action,
                "next_action": next_action,
                "resources": resources,
            },
            agent_id,
        )


def _advance(
    task_id: str,
    *,
    status: str | None = None,
    current_action: str | None = None,
    last_action: str | None = None,
    next_action: str | None = None,
    last_changeset_id: str | None = None,
    decision_capsule_hash: str | None = None,
    memory_decision: str | None = None,
    memory_reason: str | None = None,
    finished: bool = False,
) -> None:
    ensure_schema()
    assignments = ["updated_at=?"]
    params: list[Any] = [int(time.time())]
    for column, value in (
        ("status", status),
        ("current_action", current_action),
        ("last_action", last_action),
        ("next_action", next_action),
        ("last_changeset_id", last_changeset_id),
        ("decision_capsule_hash", decision_capsule_hash),
        ("memory_decision", memory_decision),
        ("memory_reason", memory_reason),
    ):
        if value is not None:
            assignments.append(f"{column}=?")
            params.append(value)
    if finished:
        assignments.append("finished_at=?")
        params.append(int(time.time()))
    params.append(task_id)
    with db.connect(_root()) as con:
        con.execute(
            f"UPDATE {ACTIVITY_TABLE} SET {','.join(assignments)} WHERE task_id=?",
            tuple(params),
        )


def _activity_row(task_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with db.connect(_root()) as con:
        row = con.execute(f"SELECT * FROM {ACTIVITY_TABLE} WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["resources"] = json.loads(data.pop("resources_json") or "[]")
    return data


def _active_activity(agent_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with db.connect(_root()) as con:
        row = con.execute(
            f"SELECT * FROM {ACTIVITY_TABLE} WHERE agent_id=? AND status IN ('active','paused','blocked','awaiting_memory') ORDER BY updated_at DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["resources"] = json.loads(data.pop("resources_json") or "[]")
    return data


def _token(agent_id: str, task_id: str) -> str:
    token = _TASK_TOKENS.get(task_id, "")
    if token:
        try:
            task_context.verify_active_context(agent_id, task_id, token)
            return token
        except task_context.TaskContextError:
            token = ""
    resumed = task_context.resume_task_context(agent_id, task_id)
    token = str(resumed["context_token"])
    _TASK_TOKENS[task_id] = token
    return token


def _targeted_scribe_query(objective: str, intent: str, scope: str, resources: list[str]) -> str:
    joined = ", ".join(resources[:12])
    return (
        f"request:{objective} intent:{intent} scope:{scope} resources:{joined} "
        "scar regression decision ne_pas_reproposer root_cause"
    )


def _targeted_graphify_query(objective: str, scope: str, resources: list[str]) -> str:
    joined = ", ".join(resources[:12])
    return f"dependencies blast radius ownership callers for {scope} {joined}; objective: {objective}"


def _mark_context_after_scribe(
    agent_id: str,
    task_id: str,
    token: str,
    payload: dict[str, Any],
    scope: str,
    force: bool = False,
) -> None:
    try:
        state = task_context.get_task_context(agent_id, task_id)
    except task_context.TaskContextError:
        state = {}
    if state.get("scribe_done") and not force:
        return
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if int(result.get("returncode", 0) or 0) != 0:
        raise RuntimeError("SCRIBE_QUERY_FAILED")
    stdout = str(result.get("stdout") or "")
    task_context.mark_scribe_done(
        agent_id,
        task_id,
        token,
        result_count=1 if stdout.strip() else 0,
        result_resources=scope,
    )


def _hydrate_task_context(
    agent_id: str,
    task_id: str,
    token: str,
    objective: str,
    intent: str,
    scope: str,
    resources: list[str],
    *,
    force: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    state = task_context.get_task_context(agent_id, task_id)
    scribe_result: dict[str, Any] = {"verdict": "SCRIBE_CONTEXT_ALREADY_READY"}
    if force or not bool(state.get("scribe_done")):
        _advance(task_id, status="active", current_action="scribe_query", next_action="graphify_query")
        scribe_result = _payload(
            _SERVER.scribe_query(
                query=_targeted_scribe_query(objective, intent, scope, resources),
                limit=5,
                agent_id=agent_id,
                task_id=task_id,
                context_token=token,
            )
        )
        try:
            _mark_context_after_scribe(agent_id, task_id, token, scribe_result, scope, force=force)
        except (RuntimeError, task_context.TaskContextError) as exc:
            return scribe_result, {}, {
                "ok": False,
                "verdict": "TENOR_TASK_SCRIBE_FAILED",
                "task_id": task_id,
                "reason": str(exc),
                "scribe": scribe_result,
            }

    state = task_context.get_task_context(agent_id, task_id)
    graphify_result: dict[str, Any] = {"verdict": "GRAPHIFY_NOT_REQUIRED"}
    if bool(state.get("requires_graphify")) and (force or not bool(state.get("graphify_done"))):
        _advance(task_id, current_action="graphify_query", last_action="scribe_query", next_action="ready")
        graphify_result = _payload(
            _SERVER.graphify_query(
                query=_targeted_graphify_query(objective, scope, resources),
                resource=scope,
                agent_id=agent_id,
                task_id=task_id,
                context_token=token,
            )
        )
        refreshed = task_context.get_task_context(agent_id, task_id)
        if not refreshed.get("graphify_done"):
            return scribe_result, graphify_result, {
                "ok": False,
                "verdict": "TENOR_TASK_GRAPHIFY_FAILED",
                "task_id": task_id,
                "graphify": graphify_result,
            }
    elif bool(state.get("requires_graphify")):
        graphify_result = {"verdict": "GRAPHIFY_CONTEXT_ALREADY_READY"}
    return scribe_result, graphify_result, None


def _prepare_decision_capsule(
    agent_id: str,
    task_id: str,
    objective: str,
    intent: str,
    scope: str,
    resources: list[str],
    scribe_result: dict[str, Any],
    graphify_result: dict[str, Any],
    *,
    refresh_existing: bool = False,
) -> dict[str, Any]:
    existing = tenor_decision.load_capsule(_root(), task_id)
    if existing and not refresh_existing:
        return tenor_decision.verify_capsule(_root(), task_id, agent_id, resources)
    state = task_context.get_task_context(agent_id, task_id)
    return tenor_decision.build_capsule(
        project_root=_root(),
        task_id=task_id,
        agent_id=agent_id,
        objective=objective,
        intent=intent,
        scope=scope,
        resources=resources,
        scribe_result=scribe_result,
        graphify_result=graphify_result,
        graphify_required=bool(state.get("requires_graphify")),
        refresh_existing=refresh_existing,
    )


def tenor_task_start(
    objective: str = "",
    intent: str = "write",
    resources: list[str] | None = None,
    scope: str = "",
) -> dict[str, Any]:
    agent_id, blocked = _bound_agent()
    if blocked:
        return _ok(blocked)
    if not objective or not objective.strip():
        return _ok({"ok": False, "verdict": "TENOR_TASK_OBJECTIVE_REQUIRED"})
    try:
        canonical_intent = _normalize_intent(intent)
        normalized_resources, normalized_scope = _normalize_resources(resources, scope, canonical_intent)
    except ValueError as exc:
        return _ok({"ok": False, "verdict": str(exc)})
    active = _active_activity(agent_id)
    if active:
        if active["status"] == "awaiting_memory":
            admission = tenor_memory_admission.get_admission(_root(), active["task_id"], agent_id)
            return _ok({
                "ok": False,
                "verdict": "TENOR_MEMORY_ADMISSION_USER_DECISION_REQUIRED",
                "task_id": active["task_id"],
                "memory_admission": admission,
                "next_action": "tenor_task_control:memory_promote_or_memory_skip",
            })
        if (
            active["objective"] == objective.strip()
            and active["intent"] == canonical_intent
            and active["scope"] == normalized_scope
            and active["resources"] == normalized_resources
        ):
            try:
                token = _token(agent_id, active["task_id"])
            except task_context.TaskContextError as exc:
                return _ok({"ok": False, "verdict": exc.code, "task_id": active["task_id"]})
            _TASK_TOKENS[active["task_id"]] = token
            existing_capsule = tenor_decision.load_capsule(_root(), active["task_id"])
            force_refresh = False
            if existing_capsule:
                capsule_state = tenor_decision.verify_capsule(
                    _root(), active["task_id"], agent_id, normalized_resources
                )
                force_refresh = capsule_state.get("verdict") == "TENOR_DECISION_CAPSULE_STALE"
            scribe_result, graphify_result, context_error = _hydrate_task_context(
                agent_id,
                active["task_id"],
                token,
                objective.strip(),
                canonical_intent,
                normalized_scope,
                normalized_resources,
                force=force_refresh,
            )
            if context_error:
                _advance(active["task_id"], status="blocked", current_action="blocked", next_action="tenor_task_start")
                return _ok(context_error)
            capsule = _prepare_decision_capsule(
                agent_id,
                active["task_id"],
                objective.strip(),
                canonical_intent,
                normalized_scope,
                normalized_resources,
                scribe_result,
                graphify_result,
                refresh_existing=force_refresh,
            )
            if not capsule.get("ok"):
                _advance(active["task_id"], status="blocked", current_action="decision_capsule_stale", next_action="tenor_task_start:same_objective_refresh")
                return _ok(capsule)
            if force_refresh:
                direct_fs_tripwire.workspace_snapshot(
                    _root(),
                    active["task_id"],
                    agent_id,
                    normalized_scope,
                    refresh=True,
                )
            _advance(
                active["task_id"],
                status="active",
                current_action="ready",
                next_action="tenor_apply_changeset",
                decision_capsule_hash=str(capsule.get("capsule_hash") or ""),
            )
            return _ok({
                "ok": True,
                "verdict": "TENOR_TASK_RESUMED",
                "task_id": active["task_id"],
                "intent": canonical_intent,
                "scope": active["scope"],
                "resources": active["resources"],
                "scribe": {"verdict": scribe_result.get("verdict", "")},
                "graphify": {"verdict": graphify_result.get("verdict", "")},
                "decision_capsule": capsule,
                "next_action": "tenor_apply_changeset" if canonical_intent != "read" else "tenor_task_control",
            })
        return _ok({
            "ok": False,
            "verdict": "TENOR_TASK_ALREADY_ACTIVE",
            "active_task": active,
            "allowed_action": "tenor_task_control",
        })

    before = _payload(
        _SERVER.before_task(
            request=objective.strip(),
            agent_id=agent_id,
            intent=canonical_intent,
            resource=normalized_scope,
        )
    )
    if before.get("verdict") != "BEFORE_TASK_OK":
        return _ok({"ok": False, "verdict": before.get("verdict", "TENOR_TASK_START_FAILED"), "details": before})
    task_id = str(before.get("task_id") or "")
    token = str(before.get("context_token") or "")
    if not task_id or not token:
        return _ok({"ok": False, "verdict": "TENOR_TASK_CONTEXT_MISSING", "details": before})
    _TASK_TOKENS[task_id] = token
    _write_activity(
        task_id,
        agent_id,
        objective.strip(),
        canonical_intent,
        normalized_scope,
        normalized_resources,
        status="active",
        current_action="scribe_query",
        last_action="before_task",
        next_action="graphify_query" if before.get("requires_graphify") else "ready",
    )
    canonical_memory_gate.snapshot_before_task(
        _root(),
        task_id,
        agent_id,
        objective.strip(),
        canonical_intent,
        normalized_scope,
    )

    scribe_result, graphify_result, context_error = _hydrate_task_context(
        agent_id,
        task_id,
        token,
        objective.strip(),
        canonical_intent,
        normalized_scope,
        normalized_resources,
    )
    if context_error:
        _advance(task_id, status="blocked", current_action="blocked", next_action="tenor_task_start")
        return _ok(context_error)
    capsule = _prepare_decision_capsule(
        agent_id,
        task_id,
        objective.strip(),
        canonical_intent,
        normalized_scope,
        normalized_resources,
        scribe_result,
        graphify_result,
    )
    if not capsule.get("ok"):
        _advance(task_id, status="blocked", current_action="decision_capsule_failed", next_action="tenor_task_control:cancel")
        return _ok(capsule)
    task_state = task_context.get_task_context(agent_id, task_id)
    next_action = "tenor_apply_changeset" if canonical_intent != "read" else "tenor_task_control"
    _advance(
        task_id,
        current_action="ready",
        last_action="graphify_query" if bool(task_state.get("requires_graphify")) else "scribe_query",
        next_action=next_action,
        decision_capsule_hash=str(capsule.get("capsule_hash") or ""),
    )
    return _ok({
        "ok": True,
        "verdict": "TENOR_TASK_READY",
        "task_id": task_id,
        "agent_id": agent_id,
        "intent": canonical_intent,
        "scope": normalized_scope,
        "resources": normalized_resources,
        "scribe": {
            "verdict": scribe_result.get("verdict", ""),
            "historical_context_found": scribe_result.get("historical_scribe_context_found"),
        },
        "graphify": {"verdict": graphify_result.get("verdict", "")},
        "decision_capsule": capsule,
        "next_action": next_action,
    })


def _record_runtime_scribe(
    agent_id: str,
    task_id: str,
    token: str,
    objective: str,
    summary: str,
    resources: list[str],
    changeset_id: str,
    validators: list[dict[str, Any]],
    *,
    record_type: str = "task_summary",
    memory_policy: str = "local_only",
    capsule_hash: str = "",
) -> dict[str, Any]:
    result = _SERVER.scribe_record(
        agent_id=agent_id,
        request=objective,
        summary=summary,
        touched_resources=resources,
        resources=resources,
        verdict="CHANGESET_COMMITTED" if changeset_id != "read-only" else "READ_TASK_COMPLETED",
        record_type=record_type,
        severity="medium",
        evidence=json.dumps({
            "changeset_id": changeset_id,
            "decision_capsule_hash": capsule_hash,
            "validators": [
                {"argv": item.get("argv", []), "returncode": item.get("returncode"), "ok": item.get("ok")}
                for item in validators
            ],
        }, ensure_ascii=False, sort_keys=True),
        related_tests=[" ".join(item.get("argv", [])) for item in validators],
        tags=["tenor-changeset", changeset_id],
        task_id=task_id,
        context_token=token,
        memory_policy=memory_policy,
    )
    payload = _payload(result)
    if payload.get("verdict") not in {"SCRIBE_RECORD_STAGED_ONLY", "SCRIBE_RECORD_WRITTEN"}:
        raise RuntimeError(f"SCRIBE_RUNTIME_RECORD_FAILED: {payload}")
    return payload


def _close_task_after_admission(
    *,
    agent_id: str,
    task_id: str,
    activity: dict[str, Any],
    summary: str,
    changeset_id: str,
    admission: dict[str, Any],
) -> dict[str, Any]:
    token = _token(agent_id, task_id)
    capsule = tenor_decision.resolve_capsule(_root(), task_id, agent_id, changeset_id)
    if not capsule.get("ok"):
        raise RuntimeError(f"TENOR_DECISION_CAPSULE_RESOLUTION_FAILED: {capsule}")
    task_context.finish_task_context(agent_id, task_id, token)
    db.finish_task(agent_id, summary or f"changeset {changeset_id} committed")
    _advance(
        task_id,
        status="finished",
        current_action="finished",
        last_action="memory_admission_and_finish",
        next_action="ready_for_next_task",
        last_changeset_id=changeset_id,
        memory_decision=str(admission.get("decision") or ""),
        memory_reason=str(admission.get("reason") or ""),
        finished=True,
    )
    _TASK_TOKENS.pop(task_id, None)
    return {"capsule": capsule, "terminal": True, "next_action": "ready_for_next_task"}


def tenor_apply_changeset(
    task_id: str = "",
    changes: list[dict[str, Any]] | None = None,
    validators: list[dict[str, Any]] | None = None,
    summary: str = "",
    request_id: str = "",
    confirm_deletions: list[str] | None = None,
    confirm_full_replacements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    agent_id, blocked = _bound_agent()
    if blocked:
        return _ok(blocked)
    activity = _activity_row(task_id)
    if not activity:
        return _ok({"ok": False, "verdict": "TENOR_TASK_UNKNOWN", "task_id": task_id})
    if activity["agent_id"] != agent_id:
        return _ok({"ok": False, "verdict": "TENOR_TASK_OWNER_MISMATCH", "task_id": task_id})
    if activity["intent"] == "read":
        return _ok({"ok": False, "verdict": "TENOR_READ_TASK_CANNOT_APPLY_CHANGESET", "task_id": task_id})
    if activity["status"] not in {"active", "paused", "blocked"}:
        return _ok({"ok": False, "verdict": "TENOR_TASK_NOT_ACTIVE", "status": activity["status"]})
    try:
        token = _token(agent_id, task_id)
    except task_context.TaskContextError as exc:
        return _ok({"ok": False, "verdict": exc.code, "task_id": task_id, "details": exc.details})
    capsule = tenor_decision.verify_capsule(_root(), task_id, agent_id, activity["resources"])
    if not capsule.get("ok"):
        _advance(task_id, status="blocked", current_action="decision_capsule_stale", last_action="ready", next_action="tenor_task_start:same_objective_refresh")
        return _ok(capsule)
    _advance(task_id, status="active", current_action="apply_changeset", last_action="decision_capsule_verified", next_action="validate_and_record")
    result = tenor_changeset.apply_changeset(
        project_root=_root(),
        agent_id=agent_id,
        task_id=task_id,
        changes=changes or [],
        validators=validators or [],
        allowed_resources=activity["resources"],
        confirm_deletions=confirm_deletions or [],
        confirm_full_replacements=confirm_full_replacements or [],
        request_id=request_id,
    )
    if not result.get("ok"):
        _advance(task_id, status="active", current_action="ready", last_action="changeset_rolled_back", next_action="tenor_apply_changeset")
        return _ok(result)
    resources = [str(item.get("path") or "") for item in result.get("files", [])]
    for item in result.get("files", []):
        direct_fs_tripwire.record_authorized_mutation(
            task_id=task_id,
            agent_id=agent_id,
            resource=str(item.get("path") or ""),
            tool="tenor_apply_changeset",
            patch_id=str(result.get("changeset_id") or ""),
            before_hash=str(item.get("base_hash") or ""),
            after_hash=str(item.get("new_hash") or ""),
            project_root=_root(),
        )
    workspace_audit = direct_fs_tripwire.detect_unauthorized_mutations(_root(), task_id, agent_id)
    if workspace_audit.get("verdict") == direct_fs_tripwire.DIRECT_WRITE_BYPASS_DETECTED:
        _advance(
            task_id,
            status="blocked",
            current_action="unauthorized_mutation_detected",
            last_action="changeset_committed",
            next_action="inspect_and_restore_unauthorized_mutations",
            last_changeset_id=str(result.get("changeset_id") or ""),
        )
        return _ok({
            **result,
            "ok": False,
            "verdict": "TENOR_CHANGESET_COMMITTED_UNAUTHORIZED_MUTATION_DETECTED",
            "task_id": task_id,
            "workspace_audit": workspace_audit,
            "terminal": False,
            "next_action": "inspect_and_restore_unauthorized_mutations",
        })
    completion_summary = summary.strip() or f"Applied validated changeset {result['changeset_id']}"
    classification = tenor_memory_admission.classify_outcome(
        objective=activity["objective"],
        intent=activity["intent"],
        summary=completion_summary,
        files=list(result.get("files", [])),
        validators=list(result.get("validators", [])),
        canonical_memory_active=canonical_memory_gate.is_active(_root()),
    )
    memory_policy = "canonical_required" if classification["decision"] in {"promote", "ask_user"} else "local_only"
    record_type = "validation" if classification["decision"] in {"promote", "ask_user"} else "task_summary"
    try:
        token = _token(agent_id, task_id)
        record = _record_runtime_scribe(
            agent_id,
            task_id,
            token,
            activity["objective"],
            completion_summary,
            resources,
            str(result["changeset_id"]),
            list(result.get("validators", [])),
            record_type=record_type,
            memory_policy=memory_policy,
            capsule_hash=str(capsule.get("capsule_hash") or ""),
        )
        if classification["decision"] == "promote":
            promotion = _payload(
                _SERVER.scribe_promote_record(
                    agent_id=agent_id,
                    task_id=task_id,
                    context_token=token,
                    record_path=str(record.get("record_path") or ""),
                )
            )
            if promotion.get("verdict") not in {"CANONICAL_MEMORY_PROMOTED", "CANONICAL_MEMORY_ALREADY_PROMOTED"}:
                raise RuntimeError(f"SCRIBE_CANONICAL_PROMOTION_FAILED: {promotion}")
        record_path = _root() / str(record.get("record_path") or "")
        admission = tenor_memory_admission.admit_runtime_record(
            project_root=_root(),
            task_id=task_id,
            agent_id=agent_id,
            objective=activity["objective"],
            intent=activity["intent"],
            summary=completion_summary,
            files=list(result.get("files", [])),
            validators=list(result.get("validators", [])),
            record=dict(record.get("entry") or {}),
            record_path=record_path,
            scope=activity["scope"],
        )
        if admission.get("decision") == "ask_user":
            _advance(
                task_id,
                status="awaiting_memory",
                current_action="awaiting_memory_decision",
                last_action="changeset_committed_and_record_staged",
                next_action="tenor_task_control:memory_promote_or_memory_skip",
                last_changeset_id=str(result.get("changeset_id") or ""),
                memory_decision="ask_user",
                memory_reason=str(admission.get("reason") or ""),
            )
            return _ok({
                **result,
                "ok": False,
                "verdict": "TENOR_CHANGESET_COMMITTED_MEMORY_DECISION_REQUIRED",
                "task_id": task_id,
                "scribe_record": record,
                "memory_admission": admission,
                "terminal": False,
                "next_action": "tenor_task_control:memory_promote_or_memory_skip",
            })
        if not admission.get("ok"):
            raise RuntimeError(f"TENOR_MEMORY_ADMISSION_FAILED: {admission}")
        closure = _close_task_after_admission(
            agent_id=agent_id,
            task_id=task_id,
            activity=activity,
            summary=completion_summary,
            changeset_id=str(result["changeset_id"]),
            admission=admission,
        )
    except Exception as exc:
        _advance(
            task_id,
            status="blocked",
            current_action="record_or_finish_failed",
            last_action="changeset_committed",
            next_action="tenor_task_control",
            last_changeset_id=str(result.get("changeset_id") or ""),
        )
        return _ok({
            **result,
            "ok": False,
            "verdict": "TENOR_CHANGESET_COMMITTED_TASK_CLOSURE_FAILED",
            "reason": f"{type(exc).__name__}: {exc}",
            "task_id": task_id,
        })
    return _ok({
        **result,
        "verdict": "TENOR_CHANGESET_COMMITTED_TASK_FINISHED",
        "task_id": task_id,
        "scribe_record": {
            "verdict": record.get("verdict"),
            "record_path": record.get("record_path"),
        },
        "memory_admission": admission,
        "decision_capsule": closure["capsule"],
        "terminal": True,
        "next_action": "ready_for_next_task",
    })


def _activity_snapshot(include_history: int) -> dict[str, Any]:
    ensure_schema()
    limit = max(0, min(int(include_history), MAX_ACTIVITY_HISTORY))
    agents = db.list_agents().get("agents", [])
    with db.connect(_root()) as con:
        task_rows = con.execute(
            f"SELECT * FROM {ACTIVITY_TABLE} ORDER BY updated_at DESC"
        ).fetchall()
        event_rows = con.execute(
            "SELECT event_id,ts,agent_id,type,payload FROM events ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall() if limit else []
    tasks: list[dict[str, Any]] = []
    for row in task_rows:
        item = dict(row)
        item["resources"] = json.loads(item.pop("resources_json") or "[]")
        tasks.append(item)
    tasks_by_agent: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        tasks_by_agent.setdefault(task["agent_id"], []).append(task)
    consolidated: list[dict[str, Any]] = []
    for agent in agents:
        own = tasks_by_agent.get(agent["agent_id"], [])
        current = next((task for task in own if task["status"] in {"active", "paused", "blocked", "awaiting_memory"}), None)
        consolidated.append({
            **agent,
            "presence": presence.status(_root(), agent["agent_id"]),
            "current_task": current,
            "last_task": own[0] if own else None,
            "task_count": len(own),
        })
    events: list[dict[str, Any]] = []
    for row in event_rows:
        event = dict(row)
        try:
            event["payload"] = json.loads(event["payload"] or "{}")
        except json.JSONDecodeError:
            event["payload"] = {"raw": event["payload"]}
        events.append(event)
    return {
        "ok": True,
        "verdict": "TENOR_ACTIVITY_SNAPSHOT",
        "active_agents": sum(1 for agent in consolidated if agent.get("status") == "active"),
        "agents": consolidated,
        "tasks": tasks,
        "events": events,
        "captured_at": int(time.time()),
    }


def tenor_activity(include_history: int = 20) -> dict[str, Any]:
    _, blocked = _bound_agent()
    if blocked:
        return _ok(blocked)
    try:
        return _ok(_activity_snapshot(include_history))
    except (TypeError, ValueError):
        return _ok({"ok": False, "verdict": "TENOR_ACTIVITY_LIMIT_INVALID"})


def tenor_task_control(task_id: str = "", action: str = "", summary: str = "") -> dict[str, Any]:
    agent_id, blocked = _bound_agent()
    if blocked:
        return _ok(blocked)
    activity = _activity_row(task_id)
    if not activity:
        return _ok({"ok": False, "verdict": "TENOR_TASK_UNKNOWN", "task_id": task_id})
    if activity["agent_id"] != agent_id:
        return _ok({"ok": False, "verdict": "TENOR_TASK_OWNER_MISMATCH", "task_id": task_id})
    normalized = (action or "").strip().lower()
    if normalized not in {"pause", "resume", "cancel", "finish", "memory_promote", "memory_skip"}:
        return _ok({"ok": False, "verdict": "TENOR_TASK_CONTROL_ACTION_INVALID"})
    if normalized == "pause":
        if activity["status"] != "active":
            return _ok({"ok": False, "verdict": "TENOR_TASK_NOT_ACTIVE", "status": activity["status"]})
        _advance(task_id, status="paused", current_action="paused", last_action=activity["current_action"], next_action="tenor_task_control:resume")
        return _ok({"ok": True, "verdict": "TENOR_TASK_PAUSED", "task_id": task_id})
    if normalized == "resume":
        try:
            _token(agent_id, task_id)
        except task_context.TaskContextError as exc:
            return _ok({"ok": False, "verdict": exc.code, "details": exc.details})
        next_action = "tenor_apply_changeset" if activity["intent"] != "read" else "tenor_task_control:finish"
        _advance(task_id, status="active", current_action="ready", last_action="resumed", next_action=next_action)
        return _ok({"ok": True, "verdict": "TENOR_TASK_RESUMED", "task_id": task_id, "next_action": next_action})

    tenor_changeset.ensure_schema(_root())
    with db.connect(_root()) as con:
        active_transaction = con.execute(
            f"SELECT changeset_id,status FROM {tenor_changeset.TRANSACTION_TABLE} WHERE task_id=? AND status IN ('staging','applying','validating') LIMIT 1",
            (task_id,),
        ).fetchone()
    if active_transaction:
        return _ok({
            "ok": False,
            "verdict": "TENOR_TASK_CONTROL_TRANSACTION_ACTIVE",
            "changeset_id": active_transaction["changeset_id"],
            "status": active_transaction["status"],
        })
    try:
        token = _token(agent_id, task_id)
    except task_context.TaskContextError as exc:
        return _ok({"ok": False, "verdict": exc.code, "details": exc.details})
    if normalized in {"memory_promote", "memory_skip"}:
        if activity["status"] != "awaiting_memory":
            return _ok({"ok": False, "verdict": "TENOR_MEMORY_ADMISSION_NOT_PENDING", "task_id": task_id})
        admission = tenor_memory_admission.get_admission(_root(), task_id, agent_id)
        if not admission:
            return _ok({"ok": False, "verdict": "TENOR_MEMORY_ADMISSION_NOT_PENDING", "task_id": task_id})
        if normalized == "memory_promote":
            promoted = _payload(
                _SERVER.scribe_promote_record(
                    agent_id=agent_id,
                    task_id=task_id,
                    context_token=token,
                    record_path=str(admission.get("record_path") or ""),
                )
            )
            if promoted.get("verdict") not in {"CANONICAL_MEMORY_PROMOTED", "CANONICAL_MEMORY_ALREADY_PROMOTED"}:
                return _ok({"ok": False, "verdict": "TENOR_MEMORY_ADMISSION_PROMOTION_FAILED", "promotion": promoted})
            resolved = tenor_memory_admission.resolve_pending(_root(), task_id, agent_id, "promote", summary)
        else:
            resolved = tenor_memory_admission.resolve_pending(_root(), task_id, agent_id, "runtime_only", summary)
            if resolved.get("ok"):
                task_context.mark_scribe_record_skipped(agent_id, task_id, token, skip_reason=summary.strip())
        if not resolved.get("ok"):
            return _ok(resolved)
        try:
            closure = _close_task_after_admission(
                agent_id=agent_id,
                task_id=task_id,
                activity=activity,
                summary=summary.strip() or "Memory admission resolved",
                changeset_id=str(activity.get("last_changeset_id") or ""),
                admission=resolved,
            )
        except Exception as exc:
            return _ok({"ok": False, "verdict": "TENOR_TASK_FINISH_FAILED", "reason": f"{type(exc).__name__}: {exc}"})
        return _ok({
            "ok": True,
            "verdict": "TENOR_TASK_FINISHED_AFTER_MEMORY_ADMISSION",
            "task_id": task_id,
            "memory_admission": resolved,
            "decision_capsule": closure["capsule"],
            "terminal": True,
        })

    if normalized == "finish" and activity["intent"] != "read" and not activity.get("last_changeset_id"):
        return _ok({
            "ok": False,
            "verdict": "TENOR_WRITE_TASK_CHANGESET_REQUIRED",
            "next_action": "tenor_apply_changeset",
        })
    if normalized == "finish":
        if activity["intent"] != "read":
            admission = tenor_memory_admission.get_admission(_root(), task_id, agent_id)
            if not admission or admission.get("status") != "resolved":
                return _ok({
                    "ok": False,
                    "verdict": "TENOR_MEMORY_ADMISSION_REQUIRED",
                    "task_id": task_id,
                    "next_action": "tenor_task_control:memory_promote_or_memory_skip",
                })
            try:
                closure = _close_task_after_admission(
                    agent_id=agent_id,
                    task_id=task_id,
                    activity=activity,
                    summary=summary.strip() or "Committed task closure retried",
                    changeset_id=str(activity["last_changeset_id"]),
                    admission=admission,
                )
            except Exception as exc:
                return _ok({"ok": False, "verdict": "TENOR_TASK_FINISH_FAILED", "reason": f"{type(exc).__name__}: {exc}"})
            return _ok({
                "ok": True,
                "verdict": "TENOR_TASK_FINISHED",
                "task_id": task_id,
                "memory_admission": admission,
                "decision_capsule": closure["capsule"],
                "terminal": True,
            })
        try:
            record = _record_runtime_scribe(
                agent_id,
                task_id,
                token,
                activity["objective"],
                summary.strip() or "Read-only task completed",
                activity["resources"],
                activity.get("last_changeset_id") or "read-only",
                [],
                record_type="task_summary",
                memory_policy="local_only",
                capsule_hash=str(activity.get("decision_capsule_hash") or ""),
            )
            record_path = _root() / str(record.get("record_path") or "")
            admission = tenor_memory_admission.admit_runtime_record(
                project_root=_root(),
                task_id=task_id,
                agent_id=agent_id,
                objective=activity["objective"],
                intent="read",
                summary=summary.strip() or "Read-only task completed",
                files=[],
                validators=[],
                record=dict(record.get("entry") or {}),
                record_path=record_path,
                scope=activity["scope"],
            )
            closure = _close_task_after_admission(
                agent_id=agent_id,
                task_id=task_id,
                activity=activity,
                summary=summary.strip() or "task finished",
                changeset_id="read-only",
                admission=admission,
            )
        except Exception as exc:
            return _ok({"ok": False, "verdict": "TENOR_TASK_FINISH_FAILED", "reason": f"{type(exc).__name__}: {exc}"})
        return _ok({
            "ok": True,
            "verdict": "TENOR_TASK_FINISHED",
            "task_id": task_id,
            "scribe_record": record.get("record_path"),
            "memory_admission": admission,
            "decision_capsule": closure["capsule"],
            "terminal": True,
        })

    if activity.get("last_changeset_id"):
        return _ok({
            "ok": False,
            "verdict": "TENOR_TASK_COMMITTED_CANNOT_CANCEL",
            "task_id": task_id,
            "next_action": "tenor_task_control:finish_or_memory_decision",
        })
    capsule_state = tenor_decision.load_capsule(_root(), task_id)
    if capsule_state and capsule_state.get("status") == "active":
        tenor_decision.resolve_capsule(_root(), task_id, agent_id, "cancelled")
    with db.connect(_root()) as con:
        con.execute(
            "UPDATE task_context_v2 SET status='cancelled',finished_at=? WHERE task_id=? AND agent_id=? AND status='active'",
            (int(time.time()), task_id, agent_id),
        )
        con.execute(
            "DELETE FROM resource_exclusive_locks WHERE task_id=? AND agent_id=?",
            (task_id, agent_id),
        )
        db.add_event(con, "tenor.task_cancelled", {"task_id": task_id, "summary": summary}, agent_id)
    _advance(task_id, status="cancelled", current_action="cancelled", last_action="cancel", next_action="ready_for_next_task", finished=True)
    _TASK_TOKENS.pop(task_id, None)
    return _ok({"ok": True, "verdict": "TENOR_TASK_CANCELLED", "task_id": task_id, "terminal": True})


def tenor_init_bridge(*args: Any, **kwargs: Any) -> dict[str, Any]:
    result = _BASE_BRIDGE(*args, **kwargs)
    payload = _payload(result)
    if payload.get("verdict") == "TENOR_INIT_BRIDGE_OK" and payload.get("ok", True):
        agent_id = str(payload.get("agent_session_id") or "")
        if agent_id:
            _SERVER._MCP_BOUND_AGENT_ID = agent_id
            presence_result = presence.start(_root(), agent_id)
            payload["process_bound_identity"] = {
                "agent_id": agent_id,
                "pid": os.getpid(),
                "presence": presence_result,
                "caller_supplied_agent_id_required_for_task_tools": False,
            }
            payload["normal_task_tools"] = list(PUBLIC_TASK_TOOLS)
            payload["bridge_verdict"] = "TENOR_INIT_BRIDGE_OK"
            payload["verdict"] = "TENOR_INIT_READY"
            payload["state"] = "TENOR_INIT_READY"
            payload["ready_scope"] = "HOST_PROCESS_ROOT_AND_SESSION"
            payload["mcp_tools_visible_to_host_llm"] = True
            payload["terminal"] = True
            payload["next_action"] = "READY_FOR_NEXT_TASK"
            payload.pop("tenor_init_ready_must_be_reported_by_real_host_after_root_binding", None)
            return _ok(payload)
    return result


def tool_schema(name: str) -> dict[str, Any]:
    if name == "tenor_task_start":
        return {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "minLength": 1},
                "intent": {"type": "string", "enum": ["read", "write", "delete"]},
                "resources": {"type": "array", "items": {"type": "string"}, "maxItems": 64},
                "scope": {"type": "string"},
            },
            "required": ["objective"],
            "additionalProperties": False,
        }
    if name == "tenor_apply_changeset":
        structured_edit = {
            "type": "object",
            "properties": {
                "old_text": {"type": "string", "minLength": 1},
                "new_text": {"type": "string"},
                "expected_occurrences": {"type": "integer", "minimum": 1, "maximum": 1024},
            },
            "required": ["old_text", "new_text"],
            "additionalProperties": False,
        }
        change = {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "operation": {"type": "string", "enum": ["edit", "patch", "replace", "create", "delete"]},
                "base_hash": {"type": "string"},
                "diff_text": {"type": "string"},
                "content": {"type": "string"},
                "edits": {"type": "array", "items": structured_edit, "minItems": 1, "maxItems": 128},
            },
            "required": ["path", "operation"],
            "additionalProperties": False,
        }
        validator = {
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 64},
                "cwd": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
            },
            "required": ["argv"],
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "changes": {"type": "array", "items": change, "minItems": 1, "maxItems": 64},
                "validators": {"type": "array", "items": validator, "minItems": 1, "maxItems": 12},
                "summary": {"type": "string"},
                "request_id": {"type": "string", "maxLength": 200},
                "confirm_deletions": {"type": "array", "items": {"type": "string"}},
                "confirm_full_replacements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "base_hash": {"type": "string", "minLength": 64, "maxLength": 64},
                            "new_hash": {"type": "string", "minLength": 64, "maxLength": 64},
                        },
                        "required": ["path", "base_hash", "new_hash"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["task_id", "changes", "validators"],
            "additionalProperties": False,
        }
    if name == "tenor_activity":
        return {
            "type": "object",
            "properties": {"include_history": {"type": "integer", "minimum": 0, "maximum": MAX_ACTIVITY_HISTORY}},
            "additionalProperties": False,
        }
    if name == "tenor_task_control":
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "action": {"type": "string", "enum": ["pause", "resume", "cancel", "finish", "memory_promote", "memory_skip"]},
                "summary": {"type": "string"},
            },
            "required": ["task_id", "action"],
            "additionalProperties": False,
        }
    return _BASE_SCHEMA(name)


def install(server: Any) -> None:
    global _SERVER, _BASE_SCHEMA, _BASE_BRIDGE
    if getattr(server, "_TENOR_PUBLIC_API_INSTALLED", False):
        return
    _SERVER = server
    _BASE_SCHEMA = server.tool_schema
    _BASE_BRIDGE = server.TOOLS["tenor_init_bridge"]
    ensure_schema(Path(server.ROOT))
    tenor_changeset.recover_incomplete(Path(server.ROOT))
    server.tenor_task_start = tenor_task_start
    server.tenor_apply_changeset = tenor_apply_changeset
    server.tenor_activity = tenor_activity
    server.tenor_task_control = tenor_task_control
    server.tenor_init_bridge = tenor_init_bridge
    server.TOOLS["tenor_task_start"] = tenor_task_start
    server.TOOLS["tenor_apply_changeset"] = tenor_apply_changeset
    server.TOOLS["tenor_activity"] = tenor_activity
    server.TOOLS["tenor_task_control"] = tenor_task_control
    server.TOOLS["tenor_init_bridge"] = tenor_init_bridge
    server.tool_schema = tool_schema
    server.PUBLIC_TOOL_NAMES = PUBLIC_TOOL_NAMES
    server._TENOR_PUBLIC_API_INSTALLED = True
