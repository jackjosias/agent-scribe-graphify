#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import six_host_rendezvous as rendezvous  # noqa: E402


EXPECTED_HOSTS = 6
EXPECTED_ACTIVITY_CALLS = 8
PROXY_SERVER = "agent-scribe-graphify-replay"
NORMAL_SERVER = "agent-scribe-graphify"
ALLOWED_EVENT_TYPES = {
    "thread.started",
    "turn.started",
    "turn.completed",
    "item.started",
    "item.completed",
}
ALLOWED_ITEM_TYPES = {
    "mcp_tool_call",
    "agent_message",
    "reasoning",
    "error",
}
HOOK_TRUST_NOTICE = (
    "`--dangerously-bypass-hook-trust` is enabled. Enabled hooks may run "
    "without review for this invocation."
)
EXPECTED_HOOK_TRUST_NOTICES = 2
SESSION_RE = re.compile(r"^TENOR_INIT_AGENT_SESSION=(cli-[A-Za-z0-9_-]+)$", re.M)
VERSION_RE = re.compile(r"^codex-cli ([0-9]+\.[0-9]+\.[0-9]+)$")


class ReplayFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class GitIdentity:
    commit_sha: str
    tree_sha: str
    branch: str
    origin_url: str


@dataclass(frozen=True)
class CodexAttestation:
    path: Path
    sha256: str
    version: str
    size: int
    elf_class: int
    machine: int


@dataclass
class HostResult:
    participant_id: int
    thread_id: str
    host_pid: int
    returncode: int
    calls: list[dict[str, Any]]
    event_summary: list[dict[str, Any]]
    stderr_sha256: str
    stderr_line_count: int
    started_at_ns: int
    completed_at_ns: int
    home: Path
    hook_log: Path
    proxy_log: Path
    agent_session_id: str


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _git(root: Path, *arguments: str) -> str:
    process = _run(["git", *arguments], cwd=root, timeout=20)
    if process.returncode != 0:
        raise ReplayFailure(
            f"GIT_COMMAND_FAILED:{arguments!r}:{process.stderr.strip()}"
        )
    return process.stdout.strip()


def git_identity(root: Path, *, require_clean: bool = True) -> GitIdentity:
    resolved = root.resolve()
    top = Path(_git(resolved, "rev-parse", "--show-toplevel")).resolve()
    if top != resolved:
        raise ReplayFailure(f"GIT_ROOT_MISMATCH expected={resolved} actual={top}")
    status = _git(
        resolved,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if require_clean and status:
        raise ReplayFailure("GIT_WORKTREE_NOT_CLEAN")
    commit_sha = _git(resolved, "rev-parse", "HEAD")
    tree_sha = _git(resolved, "rev-parse", "HEAD^{tree}")
    branch = _git(resolved, "branch", "--show-current") or "DETACHED"
    origin = _git(resolved, "remote", "get-url", "origin")
    if len(commit_sha) != 40 or len(tree_sha) != 40:
        raise ReplayFailure("GIT_IDENTITY_INVALID")
    return GitIdentity(commit_sha, tree_sha, branch, origin)


def inspect_elf(path: Path) -> tuple[int, int]:
    if not path.is_absolute():
        raise ReplayFailure("CODEX_BINARY_MUST_BE_ABSOLUTE")
    if path.is_symlink():
        raise ReplayFailure("CODEX_BINARY_SYMLINK_FORBIDDEN")
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode) or not os.access(path, os.X_OK):
        raise ReplayFailure("CODEX_BINARY_NOT_EXECUTABLE_REGULAR_FILE")
    header = path.read_bytes()[:64]
    if len(header) < 20 or header[:4] != b"\x7fELF":
        raise ReplayFailure("CODEX_BINARY_NOT_NATIVE_ELF")
    elf_class = int(header[4])
    byteorder = "little" if header[5] == 1 else "big"
    machine = int.from_bytes(header[18:20], byteorder=byteorder)
    if elf_class not in {1, 2} or machine == 0:
        raise ReplayFailure("CODEX_ELF_HEADER_INVALID")
    return elf_class, machine


def attest_codex_binary(
    path: Path,
    *,
    expected_sha256: str = "",
) -> CodexAttestation:
    resolved = path.resolve(strict=True)
    if resolved != path:
        raise ReplayFailure("CODEX_BINARY_INDIRECT_IDENTITY_FORBIDDEN")
    elf_class, machine = inspect_elf(resolved)
    digest = _sha256_file(resolved)
    if expected_sha256 and digest != expected_sha256.lower():
        raise ReplayFailure(
            f"CODEX_BINARY_SHA256_MISMATCH expected={expected_sha256} actual={digest}"
        )
    process = _run([str(resolved), "--version"], cwd=ROOT, timeout=15)
    match = VERSION_RE.fullmatch(process.stdout.strip())
    if process.returncode != 0 or match is None:
        raise ReplayFailure("CODEX_BINARY_VERSION_ATTESTATION_FAILED")
    return CodexAttestation(
        path=resolved,
        sha256=digest,
        version=match.group(1),
        size=resolved.stat().st_size,
        elf_class=elf_class,
        machine=machine,
    )


def should_retry_fresh_init(returncode: int, output: str) -> bool:
    return (
        returncode == 76
        and "HOST_RECONNECT_REQUIRED" in output
        and "HOST_CONFIG_UPDATED_RESTART_REQUIRED" in output
    )


def tenor_init_session(root: Path) -> str:
    argv = [
        str(root / ".agent" / "workflow" / "scribe" / "scribe"),
        "tenor-init",
        "--type",
        "cli",
        "--host",
        "codex-cli",
    ]
    first = _run(argv, cwd=root, timeout=240)
    output = first.stdout + first.stderr
    process = first
    if should_retry_fresh_init(first.returncode, output):
        process = _run(argv, cwd=root, timeout=240)
        output = process.stdout + process.stderr
        if should_retry_fresh_init(process.returncode, output):
            raise ReplayFailure("TENOR_INIT_RECONNECT_REPEATED")
    if process.returncode != 0:
        raise ReplayFailure(
            f"TENOR_INIT_FAILED rc={process.returncode} "
            f"output_sha256={_sha256_bytes(output.encode('utf-8'))}"
        )
    match = SESSION_RE.search(output)
    if match is None or "LOCAL_INIT_READY_HOST_MCP_UNBOUND" not in output:
        raise ReplayFailure("TENOR_INIT_MACHINE_PROOF_MISSING")
    return match.group(1)


def _toml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _binding(root: Path) -> dict[str, Any]:
    path = root / ".agent" / "state" / "install" / "host-binding.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReplayFailure("HOST_BINDING_RECEIPT_UNREADABLE") from exc
    if not isinstance(value, dict) or not value.get("binding_id"):
        raise ReplayFailure("HOST_BINDING_RECEIPT_INVALID")
    return value


def _home_config(
    *,
    root: Path,
    participant_id: int,
    run_id: str,
    model: str,
    session_id: str,
    rendezvous_db: Path,
    hook_log: Path,
    proxy_log: Path,
) -> str:
    python = Path(sys.executable).resolve()
    proxy = root / ".agent" / "scripts" / "six_host_mcp_proxy.py"
    hook = root / ".agent" / "scripts" / "six_host_pre_tool_hook.py"
    environment = {
        "AGENT_MCP_BINDING_ID": str(_binding(root)["binding_id"]),
        "AGENT_MCP_HOST": "codex-cli",
        "AGENT_SCRIBE_GRAPHIFY_ROOT": str(root),
        "TENOR_REPLAY_AGENT_SESSION_ID": session_id,
        "TENOR_REPLAY_HOOK_LOG": str(hook_log),
        "TENOR_REPLAY_MODEL": model,
        "TENOR_REPLAY_PARTICIPANT_ID": str(participant_id),
        "TENOR_REPLAY_PROXY_LOG": str(proxy_log),
        "TENOR_REPLAY_RENDEZVOUS_DB": str(rendezvous_db),
        "TENOR_REPLAY_RENDEZVOUS_TIMEOUT": "120",
        "TENOR_REPLAY_RUN_ID": run_id,
        "TENOR_REPLAY_SERVER_NAME": PROXY_SERVER,
    }
    lines = [
        "[features]",
        "hooks = true",
        "",
        f'[mcp_servers."{PROXY_SERVER}"]',
        f"command = {_toml(str(python))}",
        f"args = [{_toml(str(proxy))}]",
        f"cwd = {_toml(str(root))}",
        "enabled = true",
        "required = true",
        "startup_timeout_sec = 30",
        "tool_timeout_sec = 180",
        'default_tools_approval_mode = "approve"',
        f'[mcp_servers."{PROXY_SERVER}".env]',
        *[
            f"{key} = {_toml(value)}"
            for key, value in sorted(environment.items())
        ],
        "",
        "[[hooks.PreToolUse]]",
        'matcher = ".*"',
        "",
        "[[hooks.PreToolUse.hooks]]",
        'type = "command"',
        f"command = {_toml(shlex.quote(str(python)) + ' ' + shlex.quote(str(hook)))}",
        "timeout = 15",
        'statusMessage = "TENOR release-grade tool gate"',
        "",
    ]
    return "\n".join(lines)


def _disabled_normal_server_overrides(root: Path) -> list[str]:
    binding_id = str(_binding(root)["binding_id"])
    values = {
        f'mcp_servers."{NORMAL_SERVER}".command': "python3",
        f'mcp_servers."{NORMAL_SERVER}".args': [
            ".agent/mcp/server_entry.py"
        ],
        f'mcp_servers."{NORMAL_SERVER}".cwd': str(root),
        f'mcp_servers."{NORMAL_SERVER}".enabled': False,
        f'mcp_servers."{NORMAL_SERVER}".required': False,
        f'mcp_servers."{NORMAL_SERVER}".startup_timeout_sec': 20,
        f'mcp_servers."{NORMAL_SERVER}".tool_timeout_sec': 60,
        f'mcp_servers."{NORMAL_SERVER}".default_tools_approval_mode': "approve",
        f'mcp_servers."{NORMAL_SERVER}".env.AGENT_MCP_BINDING_ID': binding_id,
        f'mcp_servers."{NORMAL_SERVER}".env.AGENT_MCP_HOST': "codex-cli",
        f'mcp_servers."{NORMAL_SERVER}".env.AGENT_SCRIBE_GRAPHIFY_ROOT': str(
            root
        ),
    }
    result: list[str] = []
    for key, value in values.items():
        result.extend(
            [
                "-c",
                f"{key}={json.dumps(value, ensure_ascii=False, separators=(',', ':'))}",
            ]
        )
    return result


def _prompt(
    *,
    run_id: str,
    participant_id: int,
    session_id: str,
    model: str,
) -> str:
    bridge_arguments = json.dumps(
        {
            "agent_session_id": session_id,
            "host_tool": "codex",
            "model_name": model,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    calls = [
        "await tools.mcp__agent_scribe_graphify_replay__tenor_init_bridge"
        f"({bridge_arguments});"
    ]
    for sequence in range(1, EXPECTED_ACTIVITY_CALLS + 1):
        phase = "ready" if sequence <= 4 else "observed"
        arguments = json.dumps(
            {
                "run_id": run_id,
                "participant_id": participant_id,
                "agent_session_id": session_id,
                "phase": phase,
                "sequence": sequence,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        calls.append(
            "await tools.mcp__agent_scribe_graphify_replay__tenor_activity"
            f"({arguments});"
        )
    return "\n".join(
        [
            "Release-grade six-host replay. The two authorized MCP tools are "
            "deferred by this Codex build and must use Code Mode as transport.",
            "Invoke Code Mode exactly once. Execute only the literal JavaScript "
            "between BEGIN and END, with no inspection or modification.",
            "BEGIN",
            *calls,
            'text("SIX_REAL_HOST_PARTICIPANT_OK");',
            "END",
            "After the ninth successful result, answer only: "
            "SIX_REAL_HOST_PARTICIPANT_OK",
            "Never use Bash, shell commands, apply_patch, web, plans, subagents, "
            "the normal TENOR server, registry inspection, or any external tool "
            "other than the two MCP tools named in the literal program.",
        ]
    )


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            separators=(",", ": "),
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReplayFailure(f"JSONL_UNREADABLE:{path.name}") from exc
    for index, line in enumerate(lines, 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplayFailure(
                f"JSONL_INVALID:{path.name}:{index}"
            ) from exc
        if not isinstance(value, dict):
            raise ReplayFailure(f"JSONL_RECORD_INVALID:{path.name}:{index}")
        records.append(value)
    return records


def _logical_call(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("type") != "mcp_tool_call":
        raise ReplayFailure("TRACE_ITEM_NOT_MCP_TOOL_CALL")
    server = str(item.get("server") or "")
    tool = str(item.get("tool") or "")
    arguments = item.get("arguments")
    if server != PROXY_SERVER or tool not in {
        "tenor_init_bridge",
        "tenor_activity",
    }:
        raise ReplayFailure(
            f"TRACE_MCP_TOOL_NOT_ALLOWED server={server} tool={tool}"
        )
    if not isinstance(arguments, dict):
        raise ReplayFailure("TRACE_MCP_ARGUMENTS_INVALID")
    return {"server": server, "tool": tool, "arguments": arguments}


def validate_call_sequence(
    calls: list[dict[str, Any]],
    *,
    run_id: str,
    participant_id: int,
    session_id: str,
    model: str,
) -> None:
    if len(calls) != 9:
        raise ReplayFailure(f"TRACE_CALL_COUNT_INVALID:{len(calls)}")
    bridge = calls[0]
    if bridge != {
        "server": PROXY_SERVER,
        "tool": "tenor_init_bridge",
        "arguments": {
            "agent_session_id": session_id,
            "host_tool": "codex",
            "model_name": model,
        },
    }:
        raise ReplayFailure("TRACE_BRIDGE_CALL_INVALID")
    for sequence, call in enumerate(calls[1:], 1):
        expected = {
            "server": PROXY_SERVER,
            "tool": "tenor_activity",
            "arguments": {
                "run_id": run_id,
                "participant_id": participant_id,
                "agent_session_id": session_id,
                "phase": "ready" if sequence <= 4 else "observed",
                "sequence": sequence,
            },
        }
        if call != expected:
            raise ReplayFailure(
                f"TRACE_ACTIVITY_CALL_INVALID sequence={sequence}"
            )


def _summarize_event(
    event: Any,
    *,
    completed_calls: list[dict[str, Any]],
    thread_ids: list[str],
) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ReplayFailure("TRACE_EVENT_MUST_BE_OBJECT")
    event_type = str(event.get("type") or "")
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ReplayFailure(f"TRACE_EVENT_TYPE_NOT_ALLOWED:{event_type}")
    summary: dict[str, Any] = {"type": event_type}
    if event_type == "thread.started":
        thread_id = str(event.get("thread_id") or "")
        if not thread_id:
            raise ReplayFailure("TRACE_THREAD_ID_MISSING")
        thread_ids.append(thread_id)
        summary["thread_id"] = thread_id
    elif event_type.startswith("item."):
        item = event.get("item")
        if not isinstance(item, dict):
            raise ReplayFailure("TRACE_ITEM_INVALID")
        item_type = str(item.get("type") or "")
        if item_type not in ALLOWED_ITEM_TYPES:
            raise ReplayFailure(f"TRACE_ITEM_TYPE_NOT_ALLOWED:{item_type}")
        summary["item_type"] = item_type
        if item_type == "mcp_tool_call":
            call = _logical_call(item)
            summary["server"] = call["server"]
            summary["tool"] = call["tool"]
            summary["arguments_sha256"] = _sha256_bytes(
                json.dumps(
                    call["arguments"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            if event_type == "item.completed":
                if item.get("error") not in {None, ""}:
                    raise ReplayFailure("TRACE_MCP_CALL_ERROR")
                if item.get("status") != "completed":
                    raise ReplayFailure("TRACE_MCP_CALL_NOT_COMPLETED")
                completed_calls.append(call)
        elif item_type == "agent_message":
            text = str(item.get("text") or "")
            summary["text_sha256"] = _sha256_bytes(text.encode("utf-8"))
            summary["participant_ok"] = text.strip() == (
                "SIX_REAL_HOST_PARTICIPANT_OK"
            )
        elif item_type == "error":
            # Codex 0.145.0 emits its explicit hook-trust warning as two
            # completed `error` items. Admit only that exact deterministic
            # notice; every operational error remains fail-closed.
            message = str(item.get("message") or "")
            if event_type != "item.completed" or message != HOOK_TRUST_NOTICE:
                raise ReplayFailure("TRACE_UNEXPECTED_ERROR_ITEM")
            summary["expected_hook_trust_notice"] = True
            summary["message_sha256"] = _sha256_bytes(message.encode("utf-8"))
    return summary


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _reader(stream: Any, target: queue.Queue[str | None]) -> None:
    try:
        for line in iter(stream.readline, ""):
            target.put(line)
    finally:
        target.put(None)


def collect_host(
    *,
    codex: CodexAttestation,
    root: Path,
    run_id: str,
    participant_id: int,
    session_id: str,
    model: str,
    home: Path,
    hook_log: Path,
    proxy_log: Path,
    timeout_seconds: float,
    stop: threading.Event,
) -> HostResult:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
    env["CODEX_SQLITE_HOME"] = str(home)
    # MCP server env is scoped to the child server and is not inherited by
    # Codex PreToolUse hook processes. Bind the same public replay identity
    # into the host process so every hook decision is made against the exact
    # participant/session/run tuple used by the proxy.
    env.update(
        {
            "TENOR_REPLAY_AGENT_SESSION_ID": session_id,
            "TENOR_REPLAY_HOOK_LOG": str(hook_log),
            "TENOR_REPLAY_MODEL": model,
            "TENOR_REPLAY_PARTICIPANT_ID": str(participant_id),
            "TENOR_REPLAY_RUN_ID": run_id,
            "TENOR_REPLAY_SERVER_NAME": PROXY_SERVER,
        }
    )
    command = [
        str(codex.path),
        "exec",
        "--json",
        "--dangerously-bypass-hook-trust",
        "--sandbox",
        "read-only",
        "--cd",
        str(root),
        "--model",
        model,
        *_disabled_normal_server_overrides(root),
        _prompt(
            run_id=run_id,
            participant_id=participant_id,
            session_id=session_id,
            model=model,
        ),
    ]
    started_at_ns = time.time_ns()
    process = subprocess.Popen(
        command,
        cwd=str(root),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout_queue: queue.Queue[str | None] = queue.Queue()
    stderr_queue: queue.Queue[str | None] = queue.Queue()
    stdout_thread = threading.Thread(
        target=_reader,
        args=(process.stdout, stdout_queue),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_reader,
        args=(process.stderr, stderr_queue),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    deadline = time.monotonic() + timeout_seconds
    calls: list[dict[str, Any]] = []
    thread_ids: list[str] = []
    summaries: list[dict[str, Any]] = []
    stderr_lines: list[str] = []
    stdout_closed = False
    stderr_closed = False
    try:
        while not (stdout_closed and process.poll() is not None):
            if stop.is_set():
                raise ReplayFailure("HOST_CANCELLED_AFTER_PEER_FAILURE")
            if time.monotonic() >= deadline:
                raise ReplayFailure(f"HOST_TIMEOUT participant={participant_id}")
            try:
                line = stdout_queue.get(timeout=0.1)
            except queue.Empty:
                line = ""
            if line is None:
                stdout_closed = True
            elif line:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ReplayFailure(
                        f"TRACE_JSON_INVALID participant={participant_id}"
                    ) from exc
                summaries.append(
                    _summarize_event(
                        event,
                        completed_calls=calls,
                        thread_ids=thread_ids,
                    )
                )
            while True:
                try:
                    stderr_line = stderr_queue.get_nowait()
                except queue.Empty:
                    break
                if stderr_line is None:
                    stderr_closed = True
                    break
                stderr_lines.append(stderr_line)
                lowered = stderr_line.lower()
                if " panicked at " in lowered or " level=error" in lowered:
                    raise ReplayFailure(
                        f"HOST_STDERR_FATAL participant={participant_id}"
                    )
        returncode = process.wait(timeout=5)
        while not stderr_closed:
            try:
                value = stderr_queue.get(timeout=0.1)
            except queue.Empty:
                if not stderr_thread.is_alive():
                    break
                continue
            if value is None:
                stderr_closed = True
            else:
                stderr_lines.append(value)
        if returncode != 0:
            raise ReplayFailure(
                f"HOST_EXIT_NONZERO participant={participant_id} rc={returncode}"
            )
        if len(thread_ids) != 1:
            raise ReplayFailure(
                f"HOST_THREAD_CARDINALITY participant={participant_id} "
                f"count={len(thread_ids)}"
            )
        hook_notices = [
            item
            for item in summaries
            if item.get("expected_hook_trust_notice") is True
        ]
        if len(hook_notices) != EXPECTED_HOOK_TRUST_NOTICES:
            raise ReplayFailure(
                f"TRACE_HOOK_TRUST_NOTICE_CARDINALITY participant="
                f"{participant_id} count={len(hook_notices)}"
            )
        validate_call_sequence(
            calls,
            run_id=run_id,
            participant_id=participant_id,
            session_id=session_id,
            model=model,
        )
        return HostResult(
            participant_id=participant_id,
            thread_id=thread_ids[0],
            host_pid=process.pid,
            returncode=returncode,
            calls=calls,
            event_summary=summaries,
            stderr_sha256=_sha256_bytes("".join(stderr_lines).encode("utf-8")),
            stderr_line_count=len(stderr_lines),
            started_at_ns=started_at_ns,
            completed_at_ns=time.time_ns(),
            home=home,
            hook_log=hook_log,
            proxy_log=proxy_log,
            agent_session_id=session_id,
        )
    except Exception:
        stop.set()
        _terminate(process)
        raise


def _codex_thread_oracle(
    result: HostResult,
    *,
    root: Path,
    git: GitIdentity,
    codex: CodexAttestation,
    model: str,
) -> dict[str, Any]:
    database = result.home / "state_5.sqlite"
    if not database.is_file():
        raise ReplayFailure(
            f"CODEX_SQLITE_MISSING participant={result.participant_id}"
        )
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        integrity = str(
            connection.execute("PRAGMA integrity_check").fetchone()[0]
        )
        row = connection.execute(
            """
            SELECT id,cwd,git_sha,git_branch,git_origin_url,cli_version,
                   model,source,rollout_path
            FROM threads WHERE id=?
            """,
            (result.thread_id,),
        ).fetchone()
    finally:
        connection.close()
    if quick != "ok" or integrity != "ok" or row is None:
        raise ReplayFailure(
            f"CODEX_SQLITE_INTEGRITY_OR_THREAD_FAIL "
            f"participant={result.participant_id}"
        )
    expected = {
        "id": result.thread_id,
        "cwd": str(root),
        "git_sha": git.commit_sha,
        "git_branch": git.branch,
        "git_origin_url": git.origin_url,
        "cli_version": codex.version,
        "model": model,
        "source": "exec",
    }
    for key, value in expected.items():
        if row[key] != value:
            raise ReplayFailure(
                f"CODEX_SQLITE_FIELD_MISMATCH participant={result.participant_id} "
                f"field={key}"
            )
    rollout = Path(str(row["rollout_path"])).resolve()
    try:
        rollout.relative_to(result.home.resolve())
    except ValueError as exc:
        raise ReplayFailure("CODEX_SQLITE_ROLLOUT_OUTSIDE_HOME") from exc
    return {
        **expected,
        "quick_check": quick,
        "integrity_check": integrity,
        "rollout_bound_to_private_home": True,
    }


def _tenor_agent_oracle(
    root: Path,
    result: HostResult,
    *,
    expected_model: str,
    expected_mcp_pid: int,
) -> dict[str, Any]:
    database = root / ".agent" / "state" / "runtime" / "coordination.sqlite"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        integrity = str(
            connection.execute("PRAGMA integrity_check").fetchone()[0]
        )
        row = connection.execute(
            """
            SELECT agent_id,host_tool,model_name,pid,status
            FROM agents WHERE agent_id=?
            """,
            (result.agent_session_id,),
        ).fetchone()
    finally:
        connection.close()
    if quick != "ok" or integrity != "ok" or row is None:
        raise ReplayFailure("TENOR_SQLITE_INTEGRITY_OR_AGENT_FAIL")
    expected = {
        "agent_id": result.agent_session_id,
        "host_tool": "codex-cli",
        "model_name": expected_model,
        "pid": expected_mcp_pid,
    }
    for key, value in expected.items():
        if row[key] != value:
            raise ReplayFailure(
                f"TENOR_SQLITE_FIELD_MISMATCH participant={result.participant_id} "
                f"field={key}"
            )
    return {
        **expected,
        "status": row["status"],
        "quick_check": quick,
        "integrity_check": integrity,
    }


def _validate_hook_and_proxy(
    result: HostResult,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    hooks = _read_jsonl(result.hook_log)
    proxy = _read_jsonl(result.proxy_log)
    if len(hooks) != 9 or any(item.get("allowed") is not True for item in hooks):
        raise ReplayFailure(
            f"HOOK_DECISIONS_INVALID participant={result.participant_id}"
        )
    if len(proxy) != 9 or any(
        item.get("event") not in {"bridge", "activity"} for item in proxy
    ):
        raise ReplayFailure(
            f"PROXY_EVENTS_INVALID participant={result.participant_id}"
        )
    bridge_events = [item for item in proxy if item.get("event") == "bridge"]
    activity_events = [item for item in proxy if item.get("event") == "activity"]
    if len(bridge_events) != 1 or len(activity_events) != 8:
        raise ReplayFailure("PROXY_CARDINALITY_INVALID")
    mcp_pids = {int(item["mcp_pid"]) for item in proxy}
    host_pids = {int(item["host_pid"]) for item in proxy}
    if len(mcp_pids) != 1 or host_pids != {result.host_pid}:
        raise ReplayFailure("PROXY_PROCESS_IDENTITY_INVALID")
    return hooks, proxy, next(iter(mcp_pids))


def _artifact_safe(value: Any, forbidden: Iterable[str]) -> None:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    lowered = encoded.lower()
    static_forbidden = (
        "auth.json",
        "authorization:",
        "bearer ",
        "refresh_token",
        "access_token",
        "client_secret",
    )
    for item in (*static_forbidden, *(part.lower() for part in forbidden if part)):
        if item and item in lowered:
            raise ReplayFailure(
                f"ARTIFACT_REDACTION_FAILED token_sha256="
                f"{_sha256_bytes(item.encode('utf-8'))}"
            )


def _manifest(directory: Path) -> dict[str, Any]:
    files = []
    for path in sorted(directory.glob("*.json")):
        if path.name == "manifest.json":
            continue
        files.append(
            {
                "name": path.name,
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return {
        "schema": "tenor_replay_artifact_manifest_v1",
        "files": files,
        "file_count": len(files),
    }


def _prepare_home(
    home: Path,
    *,
    auth_source: Path,
    config: str,
) -> None:
    home.mkdir(parents=True, mode=0o700)
    auth_link = home / "auth.json"
    auth_link.symlink_to(auth_source.resolve())
    (home / "config.toml").write_text(config, encoding="utf-8")
    os.chmod(home / "config.toml", 0o600)


def execute_replay(
    *,
    root: Path,
    codex: CodexAttestation,
    model: str,
    auth_source: Path,
    output_root: Path,
    timeout_seconds: float,
) -> Path:
    root = root.resolve()
    initial_git = git_identity(root)
    run_id = (
        time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        + "-"
        + uuid.uuid4().hex[:8]
    )
    run_output = output_root.resolve() / run_id
    if run_output.exists():
        raise ReplayFailure("ARTIFACT_RUN_DIRECTORY_ALREADY_EXISTS")
    run_output.mkdir(parents=True, mode=0o700)
    sessions = [tenor_init_session(root) for _ in range(EXPECTED_HOSTS)]
    if len(set(sessions)) != EXPECTED_HOSTS:
        raise ReplayFailure("TENOR_INIT_SESSION_COLLISION")
    if git_identity(root) != initial_git:
        raise ReplayFailure("GIT_IDENTITY_CHANGED_DURING_INIT")

    with tempfile.TemporaryDirectory(prefix="tenor-six-real-hosts-v3-") as temporary:
        private = Path(temporary)
        rendezvous_db = private / "rendezvous.sqlite"
        rendezvous.initialize(
            rendezvous_db,
            run_id=run_id,
            root=str(root),
            commit_sha=initial_git.commit_sha,
            tree_sha=initial_git.tree_sha,
            model=model,
            cli_version=codex.version,
        )
        host_specs = []
        for participant_id, session_id in enumerate(sessions, 1):
            home = private / f"host-{participant_id}"
            hook_log = private / f"host-{participant_id}-hook.jsonl"
            proxy_log = private / f"host-{participant_id}-proxy.jsonl"
            config = _home_config(
                root=root,
                participant_id=participant_id,
                run_id=run_id,
                model=model,
                session_id=session_id,
                rendezvous_db=rendezvous_db,
                hook_log=hook_log,
                proxy_log=proxy_log,
            )
            _prepare_home(home, auth_source=auth_source, config=config)
            host_specs.append(
                {
                    "participant_id": participant_id,
                    "session_id": session_id,
                    "home": home,
                    "hook_log": hook_log,
                    "proxy_log": proxy_log,
                }
            )

        stop = threading.Event()
        results: list[HostResult] = []
        with ThreadPoolExecutor(max_workers=EXPECTED_HOSTS) as executor:
            futures = [
                executor.submit(
                    collect_host,
                    codex=codex,
                    root=root,
                    run_id=run_id,
                    participant_id=spec["participant_id"],
                    session_id=spec["session_id"],
                    model=model,
                    home=spec["home"],
                    hook_log=spec["hook_log"],
                    proxy_log=spec["proxy_log"],
                    timeout_seconds=timeout_seconds,
                    stop=stop,
                )
                for spec in host_specs
            ]
            try:
                for future in as_completed(futures):
                    results.append(future.result())
            except Exception:
                stop.set()
                for future in futures:
                    future.cancel()
                raise
        results.sort(key=lambda item: item.participant_id)
        if len(results) != EXPECTED_HOSTS:
            raise ReplayFailure("HOST_RESULT_CARDINALITY_INVALID")

        snapshot = rendezvous.snapshot(rendezvous_db, run_id=run_id)
        integrity = rendezvous.integrity_check(rendezvous_db)
        if (
            snapshot["participant_count"] != EXPECTED_HOSTS
            or snapshot["ready_count"] != EXPECTED_HOSTS
            or snapshot["observed_count"] != EXPECTED_HOSTS
            or snapshot["activity_call_count"]
            != EXPECTED_HOSTS * EXPECTED_ACTIVITY_CALLS
            or integrity != {"quick_check": "ok", "integrity_check": "ok"}
        ):
            raise ReplayFailure("RENDEZVOUS_TERMINAL_ORACLE_FAILED")

        thread_ids = {item.thread_id for item in results}
        host_pids = {item.host_pid for item in results}
        if len(thread_ids) != EXPECTED_HOSTS or len(host_pids) != EXPECTED_HOSTS:
            raise ReplayFailure("HOST_OR_THREAD_CARDINALITY_INVALID")
        launch_spread_ms = (
            max(item.started_at_ns for item in results)
            - min(item.started_at_ns for item in results)
        ) / 1_000_000
        if launch_spread_ms > 5_000:
            raise ReplayFailure("HOST_LAUNCH_SPREAD_TOO_WIDE")

        sidecars: list[dict[str, Any]] = []
        mcp_pids: set[int] = set()
        forbidden = [str(auth_source.resolve()), str(private.resolve())]
        for result in results:
            hooks, proxy, mcp_pid = _validate_hook_and_proxy(result)
            mcp_pids.add(mcp_pid)
            codex_row = _codex_thread_oracle(
                result,
                root=root,
                git=initial_git,
                codex=codex,
                model=model,
            )
            tenor_row = _tenor_agent_oracle(
                root,
                result,
                expected_model=model,
                expected_mcp_pid=mcp_pid,
            )
            public_hooks = [
                {
                    "tool_name": item["tool_name"],
                    "tool_input_sha256": item["tool_input_sha256"],
                    "allowed": item["allowed"],
                    "reason": item["reason"],
                }
                for item in hooks
            ]
            public_proxy = [
                {
                    key: item[key]
                    for key in (
                        "event",
                        "tool",
                        "phase",
                        "sequence",
                        "verdict",
                    )
                    if key in item
                }
                for item in proxy
            ]
            identity = {
                "participant_id": result.participant_id,
                "agent_session_id": result.agent_session_id,
                "thread_id": result.thread_id,
                "host_pid": result.host_pid,
                "mcp_pid": mcp_pid,
                "stderr_sha256": result.stderr_sha256,
                "stderr_line_count": result.stderr_line_count,
                "started_at_ns": result.started_at_ns,
                "completed_at_ns": result.completed_at_ns,
            }
            artifacts = {
                f"host-{result.participant_id}-events.json": {
                    "schema": "codex_event_oracle_v1",
                    "events": result.event_summary,
                },
                f"host-{result.participant_id}-hook.json": {
                    "schema": "codex_hook_oracle_v1",
                    "decisions": public_hooks,
                },
                f"host-{result.participant_id}-proxy.json": {
                    "schema": "tenor_proxy_oracle_v1",
                    "events": public_proxy,
                },
                f"host-{result.participant_id}-identity.json": {
                    "schema": "host_identity_oracle_v1",
                    **identity,
                    "codex_thread": codex_row,
                    "tenor_agent": tenor_row,
                },
            }
            for name, payload in artifacts.items():
                _artifact_safe(payload, forbidden)
                _atomic_json(run_output / name, payload)
                sidecars.append({"name": name, "participant_id": result.participant_id})

        if len(mcp_pids) != EXPECTED_HOSTS:
            raise ReplayFailure("MCP_PROCESS_CARDINALITY_INVALID")
        if len(sidecars) != 24:
            raise ReplayFailure("SIDECAR_CARDINALITY_INVALID")
        final_git = git_identity(root)
        if final_git != initial_git:
            raise ReplayFailure("GIT_IDENTITY_CHANGED_DURING_REPLAY")
        result_payload = {
            "schema": "six_real_host_llm_replay_result_v3",
            "verdict": "SIX_REAL_HOST_LLM_REPLAY_OK",
            "run_id": run_id,
            "root_binding": str(root),
            "git": {
                "commit_sha": final_git.commit_sha,
                "tree_sha": final_git.tree_sha,
                "branch": final_git.branch,
                "origin_url": final_git.origin_url,
                "dirty_diff_sha256": _sha256_bytes(b""),
            },
            "harness": {
                "path": ".agent/scripts/six_real_host_llm_replay.py",
                "sha256": _sha256_file(Path(__file__).resolve()),
            },
            "codex": {
                "version": codex.version,
                "sha256": codex.sha256,
                "size": codex.size,
                "elf_class": codex.elf_class,
                "machine": codex.machine,
                "direct_exec": True,
            },
            "model": model,
            "host_count": EXPECTED_HOSTS,
            "thread_count": len(thread_ids),
            "host_pid_count": len(host_pids),
            "mcp_pid_count": len(mcp_pids),
            "tenor_identity_count": len(set(sessions)),
            "activity_call_count": snapshot["activity_call_count"],
            "hook_decision_count": EXPECTED_HOSTS * 9,
            "ready": True,
            "observed": True,
            "launch_spread_ms": round(launch_spread_ms, 3),
            "sidecar_count": len(sidecars),
            "rendezvous_integrity": integrity,
            "private_state_retained": False,
        }
        _artifact_safe(result_payload, forbidden)
        _atomic_json(run_output / "result.json", result_payload)
        manifest = _manifest(run_output)
        if manifest["file_count"] != 25:
            raise ReplayFailure("ARTIFACT_PRE_MANIFEST_COUNT_INVALID")
        _atomic_json(run_output / "manifest.json", manifest)
    if any(path.exists() for path in private.glob("*")):
        raise ReplayFailure("PRIVATE_STATE_CLEANUP_FAILED")
    if git_identity(root) != initial_git:
        raise ReplayFailure("GIT_IDENTITY_CHANGED_AFTER_CLEANUP")
    return run_output


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Release-grade six real Codex host replay."
    )
    result.add_argument("--root", required=True, type=Path)
    result.add_argument("--codex-bin", required=True, type=Path)
    result.add_argument("--model", default="gpt-5.6-terra")
    result.add_argument("--output-dir", required=True, type=Path)
    result.add_argument("--timeout-seconds", type=float, default=300.0)
    result.add_argument("--expected-codex-sha256", default="")
    result.add_argument(
        "--auth-mode",
        choices=("auth-link",),
        required=True,
    )
    result.add_argument("--auth-source", required=True, type=Path)
    result.add_argument("--allow-auth-link", action="store_true")
    result.add_argument("--soak-runs", type=int, default=1)
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.auth_mode != "auth-link" or not arguments.allow_auth_link:
        raise ReplayFailure("AUTH_LINK_EXPLICIT_CONSENT_REQUIRED")
    auth_source = arguments.auth_source.resolve(strict=True)
    if not auth_source.is_file():
        raise ReplayFailure("AUTH_SOURCE_NOT_REGULAR_FILE")
    if not 1 <= arguments.soak_runs <= 24:
        raise ReplayFailure("SOAK_RUNS_OUT_OF_RANGE")
    if not 60 <= arguments.timeout_seconds <= 600:
        raise ReplayFailure("TIMEOUT_OUT_OF_RANGE")
    root = arguments.root.resolve(strict=True)
    codex = attest_codex_binary(
        arguments.codex_bin,
        expected_sha256=arguments.expected_codex_sha256,
    )
    output_root = arguments.output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    completed: list[str] = []
    started = time.time_ns()
    for _ in range(arguments.soak_runs):
        run = execute_replay(
            root=root,
            codex=codex,
            model=arguments.model,
            auth_source=auth_source,
            output_root=output_root,
            timeout_seconds=arguments.timeout_seconds,
        )
        completed.append(run.name)
        print(f"SIX_REAL_HOST_LLM_REPLAY_OK run_id={run.name}", flush=True)
    soak = {
        "schema": "six_real_host_llm_soak_v1",
        "verdict": (
            "SIX_REAL_HOST_LLM_SOAK_OK"
            if arguments.soak_runs > 1
            else "SIX_REAL_HOST_LLM_SINGLE_REPLAY_OK"
        ),
        "run_count": len(completed),
        "runs": completed,
        "codex_sha256": codex.sha256,
        "codex_version": codex.version,
        "started_at_ns": started,
        "completed_at_ns": time.time_ns(),
    }
    _atomic_json(output_root / "soak-summary.json", soak)
    print(soak["verdict"], flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReplayFailure as exc:
        print(f"SIX_REAL_HOST_LLM_REPLAY_FAIL_CLOSED:{exc}", file=sys.stderr)
        raise SystemExit(1)
