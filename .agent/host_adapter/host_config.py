from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Mapping


SERVER_NAME = "agent-scribe-graphify"
BINDING_RELATIVE = Path(".agent") / "state" / "install" / "host-binding.json"
HOST_CONFIG_READY = "HOST_CONFIG_READY"
HOST_CONFIG_UPDATED_RESTART_REQUIRED = "HOST_CONFIG_UPDATED_RESTART_REQUIRED"
HOST_DETECTION_REQUIRED = "HOST_DETECTION_REQUIRED"
HOST_DETECTION_AMBIGUOUS = "HOST_DETECTION_AMBIGUOUS"
HOST_CONFIGURATION_GUIDE_REQUIRED = "HOST_CONFIGURATION_GUIDE_REQUIRED"
HOST_CONFIG_INVALID = "HOST_CONFIG_INVALID"
HOST_PROCESS_BOUND = "HOST_PROCESS_BOUND"
HOST_MCP_UNBOUND = "HOST_MCP_UNBOUND"

_ALIASES = {
    "auto": "auto",
    "": "auto",
    "opencode": "opencode",
    "open-code": "opencode",
    "codex": "codex-cli",
    "codex-cli": "codex-cli",
    "openai-codex": "codex-cli",
    "claude": "claude-code",
    "claude-code": "claude-code",
    "cursor": "cursor",
    "vscode": "vscode-copilot",
    "vs-code": "vscode-copilot",
    "copilot": "vscode-copilot",
    "vscode-copilot": "vscode-copilot",
    "cline": "cline",
    "roo": "roo-code",
    "roo-code": "roo-code",
    "kilo": "kilo-code",
    "kilo-code": "kilo-code",
    "gemini": "gemini-cli",
    "gemini-cli": "gemini-cli",
    "windsurf": "windsurf",
}

_GUIDES = {
    "opencode": ".agent/docs/hosts/OPENCODE_MCP.md",
    "codex-cli": ".agent/docs/hosts/OPENAI_CODEX_MCP.md",
    "claude-code": ".agent/docs/hosts/CLAUDE_CODE_MCP.md",
    "cursor": ".agent/docs/hosts/CURSOR_MCP.md",
    "vscode-copilot": ".agent/docs/hosts/VSCODE_COPILOT_MCP.md",
    "cline": ".agent/docs/hosts/CLINE_MCP.md",
    "roo-code": ".agent/docs/hosts/ROO_CODE_MCP.md",
    "kilo-code": ".agent/docs/hosts/KCODE_MCP.md",
    "gemini-cli": ".agent/docs/hosts/GEMINI_CLI_MCP.md",
    "windsurf": ".agent/docs/hosts/WINDSURF_MCP.md",
    "unknown": ".agent/docs/hosts/README.md",
}

_VERIFIED_AUTOCONFIG_HOSTS = frozenset({"opencode", "codex-cli", "claude-code"})
_MANAGED_TOML_START = "# agent-scribe-graphify:host-config:start"
_MANAGED_TOML_END = "# agent-scribe-graphify:host-config:end"


def normalize_host_id(value: object) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    return _ALIASES.get(raw, raw if raw in _GUIDES else "unknown")


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _binding_path(project_root: Path) -> Path:
    return project_root.resolve() / BINDING_RELATIVE


def _python_command() -> str:
    return "python" if os.name == "nt" else "python3"


def _replace_with_retry(source: Path, target: Path) -> None:
    delay = 0.01
    for attempt in range(10):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.25)


def _atomic_text_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp", text=True
    )
    temporary = Path(temporary_name)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, mode)
        except OSError:
            pass
        _replace_with_retry(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_json_write(path: Path, value: Mapping[str, Any]) -> None:
    _atomic_text_write(path, json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _skip_trivia(text: str, index: int) -> int:
    length = len(text)
    while index < length:
        if text[index].isspace():
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise ValueError("unterminated JSONC block comment")
            index = end + 2
            continue
        return index
    return index


def _scan_string(text: str, index: int) -> int:
    if index >= len(text) or text[index] not in {'"', "'"}:
        raise ValueError("expected JSONC string")
    quote = text[index]
    index += 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1
        index += 1
    raise ValueError("unterminated JSONC string")


def _scan_composite(text: str, index: int) -> int:
    opening = text[index]
    stack = ["}" if opening == "{" else "]"]
    index += 1
    while index < len(text):
        if text.startswith("//", index):
            index = _skip_trivia(text, index)
            continue
        if text.startswith("/*", index):
            index = _skip_trivia(text, index)
            continue
        char = text[index]
        if char in {'"', "'"}:
            index = _scan_string(text, index)
            continue
        if char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]":
            if not stack or char != stack[-1]:
                raise ValueError("mismatched JSONC delimiters")
            stack.pop()
            if not stack:
                return index + 1
        index += 1
    raise ValueError("unterminated JSONC object or array")


def _scan_value(text: str, index: int) -> int:
    index = _skip_trivia(text, index)
    if index >= len(text):
        raise ValueError("missing JSONC value")
    if text[index] in {'"', "'"}:
        return _scan_string(text, index)
    if text[index] in "{[":
        return _scan_composite(text, index)
    end = index
    while end < len(text) and text[end] not in ",}]\r\n":
        if text.startswith("//", end) or text.startswith("/*", end):
            break
        end += 1
    if not text[index:end].strip():
        raise ValueError("empty JSONC primitive")
    return end


def _decode_key(raw: str) -> str:
    if raw.startswith("'"):
        return raw[1:-1]
    return str(json.loads(raw))


def _object_members(text: str, object_start: int) -> tuple[list[dict[str, Any]], int]:
    if object_start >= len(text) or text[object_start] != "{":
        raise ValueError("expected JSONC object")
    members: list[dict[str, Any]] = []
    index = object_start + 1
    while True:
        index = _skip_trivia(text, index)
        if index >= len(text):
            raise ValueError("unterminated JSONC object")
        if text[index] == "}":
            return members, index
        key_start = index
        key_end = _scan_string(text, key_start)
        key = _decode_key(text[key_start:key_end])
        colon = _skip_trivia(text, key_end)
        if colon >= len(text) or text[colon] != ":":
            raise ValueError(f"missing colon after JSONC key {key!r}")
        value_start = _skip_trivia(text, colon + 1)
        value_end = _scan_value(text, value_start)
        after = _skip_trivia(text, value_end)
        has_comma = after < len(text) and text[after] == ","
        members.append(
            {
                "key": key,
                "value_start": value_start,
                "value_end": value_end,
                "has_comma": has_comma,
            }
        )
        if has_comma:
            index = after + 1
            continue
        index = after
        if index < len(text) and text[index] == "}":
            continue
        raise ValueError(f"missing comma after JSONC key {key!r}")


def _line_indent(text: str, index: int) -> str:
    line_start = text.rfind("\n", 0, index) + 1
    prefix = text[line_start:index]
    match = re.match(r"[ \t]*", prefix)
    return match.group(0) if match else ""


def _json_value(value: Any, indent: str) -> str:
    rendered = json.dumps(value, ensure_ascii=False, indent=2)
    lines = rendered.splitlines()
    if len(lines) == 1:
        return rendered
    return lines[0] + "\n" + "\n".join(indent + line for line in lines[1:])


def _upsert_object_member(text: str, object_start: int, key: str, value: Any) -> tuple[str, bool]:
    members, close = _object_members(text, object_start)
    member = next((item for item in members if item["key"] == key), None)
    child_indent = _line_indent(text, members[0]["value_start"]) if members else _line_indent(text, object_start) + "  "
    rendered = _json_value(value, child_indent)
    if member is not None:
        previous = text[member["value_start"] : member["value_end"]]
        if previous == rendered:
            return text, False
        return text[: member["value_start"]] + rendered + text[member["value_end"] :], True

    separator = ""
    if members and not members[-1]["has_comma"]:
        separator = ","
    parent_indent = _line_indent(text, object_start)
    insertion = f'{separator}\n{parent_indent}  {json.dumps(key)}: {_json_value(value, parent_indent + "  ")}\n{parent_indent}'
    return text[:close] + insertion + text[close:], True


def _strip_jsonc(text: str) -> str:
    chars = list(text)
    index = 0
    while index < len(chars):
        if chars[index] == '"':
            index = _scan_string(text, index)
            continue
        if chars[index] == "'":
            raise ValueError("single-quoted JSONC strings are not supported for automatic configuration")
        if text.startswith("//", index):
            end = text.find("\n", index + 2)
            end = len(text) if end < 0 else end
            for offset in range(index, end):
                chars[offset] = " "
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise ValueError("unterminated JSONC block comment")
            for offset in range(index, end + 2):
                if chars[offset] not in "\r\n":
                    chars[offset] = " "
            index = end + 2
            continue
        index += 1
    cleaned = "".join(chars)
    chars = list(cleaned)
    index = 0
    while index < len(chars):
        if chars[index] == '"':
            index = _scan_string(cleaned, index)
            continue
        if chars[index] == "'":
            raise ValueError("single-quoted JSONC strings are not supported for automatic configuration")
        if chars[index] == ",":
            next_index = index + 1
            while next_index < len(chars) and chars[next_index].isspace():
                next_index += 1
            if next_index < len(chars) and chars[next_index] in "}]":
                chars[index] = " "
        index += 1
    return "".join(chars)


def _load_jsonc(text: str) -> dict[str, Any]:
    value = json.loads(_strip_jsonc(text))
    if not isinstance(value, dict):
        raise ValueError("host configuration root must be an object")
    return value


def _upsert_nested_jsonc(text: str, parent_key: str, child_key: str, value: Any) -> tuple[str, bool]:
    root_start = _skip_trivia(text, 0)
    root_members, _ = _object_members(text, root_start)
    parent = next((item for item in root_members if item["key"] == parent_key), None)
    if parent is None:
        return _upsert_object_member(text, root_start, parent_key, {child_key: value})
    if text[parent["value_start"]] != "{":
        raise ValueError(f"{parent_key} must be an object")
    return _upsert_object_member(text, parent["value_start"], child_key, value)


def _load_binding(project_root: Path) -> dict[str, Any]:
    try:
        value = json.loads(_binding_path(project_root).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _candidate_binding_id(project_root: Path, host_id: str, config_path: Path) -> str:
    existing = _load_binding(project_root)
    if (
        existing.get("project_root") == str(project_root.resolve())
        and existing.get("host_id") == host_id
        and existing.get("config_path") == str(config_path.relative_to(project_root))
        and isinstance(existing.get("binding_id"), str)
        and existing.get("binding_id")
    ):
        return str(existing["binding_id"])
    return uuid.uuid4().hex


def _host_environment(host_id: str, binding_id: str) -> dict[str, str]:
    return {
        "AGENT_MCP_HOST": host_id,
        "AGENT_MCP_BINDING_ID": binding_id,
        "AGENT_SCRIBE_GRAPHIFY_ROOT": ".",
    }


def _opencode_entry(host_id: str, binding_id: str) -> dict[str, Any]:
    return {
        "type": "local",
        "command": [_python_command(), ".agent/mcp/server_entry.py"],
        "cwd": ".",
        "environment": _host_environment(host_id, binding_id),
        "enabled": True,
        "timeout": 20000,
    }


def _stdio_entry(host_id: str, binding_id: str) -> dict[str, Any]:
    return {
        "command": _python_command(),
        "args": [".agent/mcp/server_entry.py"],
        "env": _host_environment(host_id, binding_id),
    }


def _configure_opencode(path: Path, binding_id: str) -> bool:
    if path.exists():
        text = path.read_text(encoding="utf-8")
        current = _load_jsonc(text)
    else:
        text = '{\n  "$schema": "https://opencode.ai/config.json"\n}\n'
        current = {}
    expected_server = _opencode_entry("opencode", binding_id)
    current_mcp = current.get("mcp") if isinstance(current.get("mcp"), dict) else {}
    current_permissions = current.get("permission") if isinstance(current.get("permission"), dict) else {}
    updated = text
    changed_mcp = False
    changed_edit = False
    changed_bash = False
    if current_mcp.get(SERVER_NAME) != expected_server:
        updated, changed_mcp = _upsert_nested_jsonc(updated, "mcp", SERVER_NAME, expected_server)
    if current_permissions.get("edit") != "deny":
        updated, changed_edit = _upsert_nested_jsonc(updated, "permission", "edit", "deny")
    safe_bash = {
        "*": "deny",
        ".agent/workflow/scribe/scribe tenor-init --type cli --host opencode": "allow",
        "python .agent/workflow/scribe/scribe tenor-init --type cli --host opencode": "allow",
        "python3 .agent/workflow/scribe/scribe tenor-init --type cli --host opencode": "allow",
        "py -3 .agent/workflow/scribe/scribe tenor-init --type cli --host opencode": "allow",
    }
    if current_permissions.get("bash") != safe_bash:
        updated, changed_bash = _upsert_nested_jsonc(updated, "permission", "bash", safe_bash)
    _load_jsonc(updated)
    changed = changed_mcp or changed_edit or changed_bash or not path.exists()
    if changed:
        _atomic_text_write(path, updated if updated.endswith("\n") else updated + "\n")
    return changed


def _configure_claude(path: Path, binding_id: str) -> bool:
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(".mcp.json root must be an object")
    else:
        value = {}
    servers = value.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(".mcp.json mcpServers must be an object")
    expected = _stdio_entry("claude-code", binding_id)
    changed = servers.get(SERVER_NAME) != expected
    servers[SERVER_NAME] = expected
    if changed or not path.exists():
        _atomic_json_write(path, value)
        return True
    return False


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _codex_block(binding_id: str) -> str:
    environment = _host_environment("codex-cli", binding_id)
    return "\n".join(
        [
            _MANAGED_TOML_START,
            f'[mcp_servers."{SERVER_NAME}"]',
            f"command = {_toml_string(_python_command())}",
            'args = [".agent/mcp/server_entry.py"]',
            'cwd = "."',
            "enabled = true",
            "startup_timeout_sec = 20",
            "tool_timeout_sec = 60",
            f'[mcp_servers."{SERVER_NAME}".env]',
            *[f"{key} = {_toml_string(value)}" for key, value in sorted(environment.items())],
            _MANAGED_TOML_END,
        ]
    )


def _configure_codex(path: Path, binding_id: str) -> bool:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    block = _codex_block(binding_id)
    pattern = re.compile(re.escape(_MANAGED_TOML_START) + r".*?" + re.escape(_MANAGED_TOML_END), re.DOTALL)
    if pattern.search(text):
        updated = pattern.sub(block, text)
    else:
        unmanaged = re.search(rf'^\s*\[mcp_servers\.(?:"{re.escape(SERVER_NAME)}"|{re.escape(SERVER_NAME)})\]\s*$', text, re.MULTILINE)
        if unmanaged:
            raise ValueError("existing unmanaged Codex MCP table requires explicit review")
        updated = (text.rstrip() + "\n\n" if text.strip() else "") + block + "\n"
    if updated == text:
        return False
    _atomic_text_write(path, updated)
    return True


def detect_host(
    project_root: Path | str,
    *,
    explicit: str = "auto",
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    requested = normalize_host_id(explicit)
    if requested not in {"auto", "unknown"}:
        return {"ok": True, "verdict": "HOST_DETECTED_EXPLICIT", "host_id": requested, "candidates": [requested]}

    env = dict(os.environ if environ is None else environ)
    for key in ("AGENT_MCP_HOST", "AGENT_HOST"):
        candidate = normalize_host_id(env.get(key, ""))
        if candidate not in {"auto", "unknown"}:
            return {"ok": True, "verdict": "HOST_DETECTED_ENVIRONMENT", "host_id": candidate, "candidates": [candidate], "source": key}
    env_markers = (
        ("OPENCODE", "opencode"),
        ("CLAUDECODE", "claude-code"),
        ("CURSOR_SESSION_ID", "cursor"),
    )
    for key, host_id in env_markers:
        if env.get(key):
            return {"ok": True, "verdict": "HOST_DETECTED_ENVIRONMENT", "host_id": host_id, "candidates": [host_id], "source": key}

    strong_markers = (
        ("opencode", ("opencode.jsonc", "opencode.json")),
        ("codex-cli", (".codex/config.toml",)),
        ("claude-code", (".mcp.json",)),
        ("cursor", (".cursor/mcp.json",)),
        ("vscode-copilot", (".vscode/mcp.json",)),
        ("roo-code", (".roo/mcp.json",)),
        ("gemini-cli", (".gemini/settings.json",)),
    )
    candidates = [host_id for host_id, markers in strong_markers if any((root / marker).is_file() for marker in markers)]
    if len(candidates) == 1:
        return {"ok": True, "verdict": "HOST_DETECTED_PROJECT_MARKER", "host_id": candidates[0], "candidates": candidates}
    if len(candidates) > 1:
        return {"ok": False, "verdict": HOST_DETECTION_AMBIGUOUS, "host_id": "unknown", "candidates": candidates}
    return {"ok": False, "verdict": HOST_DETECTION_REQUIRED, "host_id": "unknown", "candidates": []}


def _config_path(project_root: Path, host_id: str) -> Path:
    if host_id == "opencode":
        for candidate in (project_root / "opencode.jsonc", project_root / "opencode.json"):
            if candidate.exists():
                return candidate
        return project_root / "opencode.jsonc"
    if host_id == "claude-code":
        return project_root / ".mcp.json"
    if host_id == "codex-cli":
        return project_root / ".codex" / "config.toml"
    raise ValueError(f"automatic host configuration is not verified for {host_id}")


def configure_host(
    project_root: Path | str,
    *,
    explicit: str = "auto",
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    detection = detect_host(root, explicit=explicit, environ=environ)
    host_id = str(detection.get("host_id") or "unknown")
    guide = _GUIDES.get(host_id, _GUIDES["unknown"])
    if not detection.get("ok"):
        return {**detection, "guide": guide, "restart_required": False, "project_writes_allowed": False}
    if host_id not in _VERIFIED_AUTOCONFIG_HOSTS:
        return {
            "ok": False,
            "verdict": HOST_CONFIGURATION_GUIDE_REQUIRED,
            "host_id": host_id,
            "guide": guide,
            "restart_required": False,
            "project_writes_allowed": False,
        }

    config_path = _config_path(root, host_id)
    binding_id = _candidate_binding_id(root, host_id, config_path)
    try:
        if host_id == "opencode":
            changed = _configure_opencode(config_path, binding_id)
        elif host_id == "claude-code":
            changed = _configure_claude(config_path, binding_id)
        else:
            changed = _configure_codex(config_path, binding_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "verdict": HOST_CONFIG_INVALID,
            "host_id": host_id,
            "guide": guide,
            "config_path": str(config_path),
            "reason": str(exc),
            "restart_required": False,
            "project_writes_allowed": False,
        }

    try:
        manifest = {
            "schema": "host_binding_v1",
            "project_root": str(root),
            "host_id": host_id,
            "binding_id": binding_id,
            "config_path": str(config_path.relative_to(root)),
            "config_sha256": _sha256(config_path),
            "python_command": _python_command(),
            "server_entry": ".agent/mcp/server_entry.py",
            "guide": guide,
        }
        _atomic_json_write(_binding_path(root), manifest)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "verdict": HOST_CONFIG_INVALID,
            "host_id": host_id,
            "guide": guide,
            "config_path": str(config_path),
            "reason": f"host binding receipt could not be committed: {exc}",
            "restart_required": changed,
            "project_writes_allowed": False,
        }
    verdict = HOST_CONFIG_UPDATED_RESTART_REQUIRED if changed else HOST_CONFIG_READY
    return {
        "ok": True,
        "verdict": verdict,
        "host_id": host_id,
        "guide": guide,
        "config_path": str(config_path),
        "binding_id": binding_id,
        "config_sha256": manifest["config_sha256"],
        "restart_required": changed,
        "project_writes_allowed": False,
        "detection": detection,
    }


def verify_host_process_binding(
    project_root: Path | str,
    *,
    environ: Mapping[str, str] | None = None,
    claimed_host: str = "",
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    env = dict(os.environ if environ is None else environ)
    host_id = normalize_host_id(env.get("AGENT_MCP_HOST", ""))
    binding_id = str(env.get("AGENT_MCP_BINDING_ID") or "").strip()
    if host_id in {"auto", "unknown"} or not binding_id:
        return {"ok": False, "verdict": HOST_MCP_UNBOUND, "reason": "host process environment binding is absent"}
    requested = normalize_host_id(claimed_host)
    if requested not in {"auto", "unknown", host_id}:
        return {"ok": False, "verdict": "HOST_PROCESS_IDENTITY_MISMATCH", "host_id": host_id, "claimed_host": requested}
    manifest = _load_binding(root)
    required = {
        "project_root": str(root),
        "host_id": host_id,
        "binding_id": binding_id,
    }
    for key, expected in required.items():
        if manifest.get(key) != expected:
            return {"ok": False, "verdict": "HOST_BINDING_MANIFEST_MISMATCH", "field": key, "expected": expected}
    try:
        config_path = root / str(manifest["config_path"])
        current_hash = _sha256(config_path)
    except (KeyError, OSError, ValueError) as exc:
        return {"ok": False, "verdict": "HOST_BINDING_CONFIG_UNREADABLE", "reason": str(exc)}
    if current_hash != manifest.get("config_sha256"):
        return {"ok": False, "verdict": "HOST_BINDING_CONFIG_STALE", "config_sha256": current_hash}
    return {
        "ok": True,
        "verdict": HOST_PROCESS_BOUND,
        "host_id": host_id,
        "binding_id": binding_id,
        "project_root": str(root),
        "config_path": str(config_path),
        "config_sha256": current_hash,
    }


def portable_tenor_argv() -> list[str]:
    return [sys.executable or _python_command(), ".agent/workflow/scribe/scribe", "tenor-init", "--type", "cli"]
