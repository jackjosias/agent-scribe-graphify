#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(
    os.environ.get("AGENT_SCRIBE_GRAPHIFY_ROOT", Path(__file__).resolve().parents[2])
).resolve()
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import server_ext as upstream  # noqa: E402
from runtime import six_host_rendezvous as rendezvous  # noqa: E402


SERVER_NAME = "agent-scribe-graphify-replay"
SERVER_VERSION = "3.0.0"
ALLOWED_TOOLS = ("tenor_init_bridge", "tenor_activity")
_BRIDGED_AGENT = ""


class ProxyError(RuntimeError):
    pass


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ProxyError(f"PROXY_ENV_REQUIRED:{name}")
    return value


def _strict_object(
    value: Any,
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProxyError("PROXY_ARGUMENTS_MUST_BE_OBJECT")
    allowed = required | (optional or set())
    if set(value) != required and not (
        required <= set(value) and set(value) <= allowed
    ):
        raise ProxyError(
            f"PROXY_ARGUMENT_SHAPE_INVALID required={sorted(required)!r} "
            f"actual={sorted(value)!r}"
        )
    return value


def _safe_log(event: dict[str, Any]) -> None:
    path = Path(_required_env("TENOR_REPLAY_PROXY_LOG"))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "tenor_replay_proxy_event_v1",
        "run_id": _required_env("TENOR_REPLAY_RUN_ID"),
        "participant_id": int(_required_env("TENOR_REPLAY_PARTICIPANT_ID")),
        "mcp_pid": os.getpid(),
        "host_pid": os.getppid(),
        "timestamp_ns": time.time_ns(),
        **event,
    }
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        path,
        os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        0o600,
    )
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _tool_schema(name: str) -> dict[str, Any]:
    if name == "tenor_init_bridge":
        return {
            "type": "object",
            "properties": {
                "agent_session_id": {"type": "string"},
                "host_tool": {"type": "string", "enum": ["codex"]},
                "model_name": {"type": "string"},
            },
            "required": ["agent_session_id", "host_tool", "model_name"],
            "additionalProperties": False,
        }
    if name == "tenor_activity":
        return {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "participant_id": {"type": "integer", "minimum": 1, "maximum": 6},
                "agent_session_id": {"type": "string"},
                "phase": {"type": "string", "enum": ["ready", "observed"]},
                "sequence": {"type": "integer", "minimum": 1, "maximum": 8},
            },
            "required": [
                "run_id",
                "participant_id",
                "agent_session_id",
                "phase",
                "sequence",
            ],
            "additionalProperties": False,
        }
    raise ProxyError("PROXY_TOOL_NOT_ALLOWED")


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": f"{SERVER_NAME}.{name}",
            "inputSchema": _tool_schema(name),
        }
        for name in ALLOWED_TOOLS
    ]


def _decode_upstream(response: dict[str, Any]) -> dict[str, Any]:
    try:
        text = response["result"]["content"][0]["text"]
        value = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ProxyError("PROXY_UPSTREAM_RESPONSE_INVALID") from exc
    if not isinstance(value, dict):
        raise ProxyError("PROXY_UPSTREAM_PAYLOAD_INVALID")
    return value


def _bridge(arguments: Any) -> dict[str, Any]:
    global _BRIDGED_AGENT
    args = _strict_object(
        arguments,
        required={"agent_session_id", "host_tool", "model_name"},
    )
    expected_session = _required_env("TENOR_REPLAY_AGENT_SESSION_ID")
    expected_model = _required_env("TENOR_REPLAY_MODEL")
    if args["agent_session_id"] != expected_session:
        raise ProxyError("PROXY_AGENT_SESSION_MISMATCH")
    if args["host_tool"] != "codex":
        raise ProxyError("PROXY_HOST_TOOL_MISMATCH")
    if args["model_name"] != expected_model:
        raise ProxyError("PROXY_MODEL_MISMATCH")
    if _BRIDGED_AGENT:
        raise ProxyError("PROXY_BRIDGE_ALREADY_CONSUMED")
    request = {
        "jsonrpc": "2.0",
        "id": "proxy-bridge",
        "method": "tools/call",
        "params": {"name": "tenor_init_bridge", "arguments": args},
    }
    response = upstream.handle(request)
    payload = _decode_upstream(response)
    if (
        payload.get("verdict") != "TENOR_INIT_READY"
        or payload.get("state") != "TENOR_INIT_READY"
        or payload.get("agent_session_id") != expected_session
        or payload.get("model_name") != expected_model
        or payload.get("ready_scope") != "HOST_PROCESS_ROOT_AND_SESSION"
    ):
        raise ProxyError(
            "PROXY_BRIDGE_VERDICT_INVALID:"
            + hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
        )
    _BRIDGED_AGENT = expected_session
    database = Path(_required_env("TENOR_REPLAY_RENDEZVOUS_DB"))
    registered = rendezvous.register_bridge(
        database,
        run_id=_required_env("TENOR_REPLAY_RUN_ID"),
        participant_id=int(_required_env("TENOR_REPLAY_PARTICIPANT_ID")),
        agent_session_id=expected_session,
        mcp_pid=os.getpid(),
        host_pid=os.getppid(),
        model=expected_model,
    )
    _safe_log(
        {
            "event": "bridge",
            "tool": "tenor_init_bridge",
            "agent_session_id": expected_session,
            "verdict": "TENOR_INIT_READY",
        }
    )
    payload["replay_registration"] = {
        "participant_count": registered["participant_count"],
        "expected_hosts": registered["expected_hosts"],
    }
    return payload


def _activity(arguments: Any) -> dict[str, Any]:
    args = _strict_object(
        arguments,
        required={
            "run_id",
            "participant_id",
            "agent_session_id",
            "phase",
            "sequence",
        },
    )
    expected_session = _required_env("TENOR_REPLAY_AGENT_SESSION_ID")
    if not _BRIDGED_AGENT or _BRIDGED_AGENT != expected_session:
        raise ProxyError("PROXY_BRIDGE_REQUIRED")
    if args["run_id"] != _required_env("TENOR_REPLAY_RUN_ID"):
        raise ProxyError("PROXY_RUN_ID_MISMATCH")
    if int(args["participant_id"]) != int(
        _required_env("TENOR_REPLAY_PARTICIPANT_ID")
    ):
        raise ProxyError("PROXY_PARTICIPANT_MISMATCH")
    if args["agent_session_id"] != expected_session:
        raise ProxyError("PROXY_AGENT_SESSION_MISMATCH")
    result = rendezvous.record_activity(
        Path(_required_env("TENOR_REPLAY_RENDEZVOUS_DB")),
        run_id=str(args["run_id"]),
        participant_id=int(args["participant_id"]),
        agent_session_id=str(args["agent_session_id"]),
        phase=str(args["phase"]),
        sequence=int(args["sequence"]),
        timeout_seconds=float(
            os.environ.get("TENOR_REPLAY_RENDEZVOUS_TIMEOUT", "120")
        ),
    )
    _safe_log(
        {
            "event": "activity",
            "tool": "tenor_activity",
            "agent_session_id": expected_session,
            "phase": args["phase"],
            "sequence": int(args["sequence"]),
            "verdict": result["verdict"],
        }
    )
    return result


def _content(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                ),
            }
        ]
    }


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        return None
    try:
        if method == "initialize":
            result: Any = {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {}},
            }
        elif method == "tools/list":
            result = {"tools": _tools()}
        elif method == "tools/call":
            params = _strict_object(
                request.get("params"),
                required={"name"},
                optional={"arguments", "_meta"},
            )
            if "_meta" in params and not isinstance(params["_meta"], dict):
                raise ProxyError("PROXY_PROTOCOL_META_INVALID")
            name = params["name"]
            if name not in ALLOWED_TOOLS:
                raise ProxyError(f"PROXY_TOOL_NOT_ALLOWED:{name}")
            payload = (
                _bridge(params.get("arguments") or {})
                if name == "tenor_init_bridge"
                else _activity(params.get("arguments") or {})
            )
            result = _content(payload)
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except (ProxyError, rendezvous.RendezvousError, TypeError, ValueError) as exc:
        _safe_log(
            {
                "event": "rejected",
                "method": str(method),
                "reason": str(exc),
            }
        )
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "isError": True,
                **_content(
                    {
                        "ok": False,
                        "verdict": "TENOR_REPLAY_PROXY_FAIL_CLOSED",
                        "reason": str(exc),
                    }
                ),
            },
        }


def main() -> int:
    upstream.server.ROOT = ROOT
    upstream.server.AGENT_DIR = ROOT / ".agent"
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            request = json.loads(stripped)
            if not isinstance(request, dict):
                raise ProxyError("PROXY_REQUEST_MUST_BE_OBJECT")
            response = handle(request)
            if response is not None:
                print(
                    json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                    flush=True,
                )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": str(exc)},
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
