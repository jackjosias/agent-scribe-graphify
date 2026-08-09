#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


TOOLS = ("tenor_init_bridge", "tenor_activity")


class HookPolicyError(RuntimeError):
    pass


def accepted_tool_names(server_name: str) -> dict[str, str]:
    normalized = server_name.replace("-", "_")
    result: dict[str, str] = {}
    for tool in TOOLS:
        for name in (
            f"mcp__{server_name}__{tool}",
            f"mcp__{normalized}__{tool}",
            f"{server_name}.{tool}",
        ):
            result[name] = tool
    return result


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise HookPolicyError(f"HOOK_ENV_REQUIRED:{name}")
    return value


def _strict_keys(value: Any, expected: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise HookPolicyError(
            f"HOOK_ARGUMENT_SHAPE_INVALID expected={sorted(expected)!r} "
            f"actual={actual!r}"
        )
    return value


def _validate_bridge(arguments: Any) -> None:
    value = _strict_keys(
        arguments,
        {"agent_session_id", "host_tool", "model_name"},
    )
    if value["agent_session_id"] != _required("TENOR_REPLAY_AGENT_SESSION_ID"):
        raise HookPolicyError("HOOK_AGENT_SESSION_MISMATCH")
    if value["host_tool"] != "codex":
        raise HookPolicyError("HOOK_HOST_TOOL_MISMATCH")
    if value["model_name"] != _required("TENOR_REPLAY_MODEL"):
        raise HookPolicyError("HOOK_MODEL_MISMATCH")


def _validate_activity(arguments: Any) -> None:
    value = _strict_keys(
        arguments,
        {
            "run_id",
            "participant_id",
            "agent_session_id",
            "phase",
            "sequence",
        },
    )
    if value["run_id"] != _required("TENOR_REPLAY_RUN_ID"):
        raise HookPolicyError("HOOK_RUN_ID_MISMATCH")
    if int(value["participant_id"]) != int(
        _required("TENOR_REPLAY_PARTICIPANT_ID")
    ):
        raise HookPolicyError("HOOK_PARTICIPANT_MISMATCH")
    if value["agent_session_id"] != _required("TENOR_REPLAY_AGENT_SESSION_ID"):
        raise HookPolicyError("HOOK_AGENT_SESSION_MISMATCH")
    sequence = int(value["sequence"])
    if not 1 <= sequence <= 8:
        raise HookPolicyError("HOOK_SEQUENCE_INVALID")
    expected_phase = "ready" if sequence <= 4 else "observed"
    if value["phase"] != expected_phase:
        raise HookPolicyError("HOOK_PHASE_SEQUENCE_MISMATCH")


def evaluate(payload: Any) -> tuple[bool, str, str]:
    if not isinstance(payload, dict):
        return False, "HOOK_INPUT_MUST_BE_OBJECT", ""
    if payload.get("hook_event_name") != "PreToolUse":
        return False, "HOOK_EVENT_MISMATCH", ""
    tool_name = str(payload.get("tool_name") or "")
    allowed = accepted_tool_names(_required("TENOR_REPLAY_SERVER_NAME"))
    logical_tool = allowed.get(tool_name)
    if logical_tool is None:
        return False, f"HOOK_TOOL_NOT_ALLOWED:{tool_name}", tool_name
    try:
        if logical_tool == "tenor_init_bridge":
            _validate_bridge(payload.get("tool_input"))
        else:
            _validate_activity(payload.get("tool_input"))
    except (HookPolicyError, TypeError, ValueError) as exc:
        return False, str(exc), tool_name
    return True, "HOOK_ALLOWLIST_OK", tool_name


def _append_decision(
    *,
    allowed: bool,
    reason: str,
    tool_name: str,
    payload: Any,
) -> None:
    path = Path(_required("TENOR_REPLAY_HOOK_LOG"))
    path.parent.mkdir(parents=True, exist_ok=True)
    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    digest = hashlib.sha256(
        json.dumps(
            tool_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    record = {
        "schema": "tenor_replay_hook_decision_v1",
        "run_id": _required("TENOR_REPLAY_RUN_ID"),
        "participant_id": int(_required("TENOR_REPLAY_PARTICIPANT_ID")),
        "timestamp_ns": time.time_ns(),
        "tool_name": tool_name,
        "tool_input_sha256": digest,
        "allowed": bool(allowed),
        "reason": reason,
    }
    encoded = (
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
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


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        allowed, reason, tool_name = evaluate(payload)
        _append_decision(
            allowed=allowed,
            reason=reason,
            tool_name=tool_name,
            payload=payload,
        )
        if allowed:
            return 0
        print(reason, file=sys.stderr)
        return 2
    except Exception as exc:
        try:
            _append_decision(
                allowed=False,
                reason=f"HOOK_INTERNAL_FAIL_CLOSED:{exc}",
                tool_name="",
                payload={},
            )
        except Exception:
            pass
        print(f"HOOK_INTERNAL_FAIL_CLOSED:{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
