#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import queue
import shutil
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any

MCP_DIR_FOR_LOCK = Path(__file__).resolve().parents[1] / "mcp"
if str(MCP_DIR_FOR_LOCK) not in sys.path:
    sys.path.insert(0, str(MCP_DIR_FOR_LOCK))
from runtime.validation_lock import (
    ValidationRuntimeBusy,
    reset_validation_runtime_database,
    validation_runtime_busy_message,
    validation_runtime_lock,
)

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / ".agent" / "mcp" / "server_entry.py"
REDTEAM_DIR = ROOT / ".agent" / "state" / "redteam"
TRIPWIRE_TARGET = ROOT / "tenor-redteam-tripwire-target.txt"


def fail(message: str) -> None:
    raise SystemExit(f"ENFORCEMENT_REDTEAM_FAIL: {message}")


class PersistentMcpClient:
    """One bounded stdio MCP session matching real host process lifetime."""

    def __init__(self, root: Path, entry: Path, timeout_seconds: float = 30.0) -> None:
        self.root = root
        self.entry = entry
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.process: subprocess.Popen[str] | None = None
        self.stdout_queue: queue.Queue[str | None] = queue.Queue(maxsize=64)
        self.stderr_lines: deque[str] = deque(maxlen=200)
        self.request_lock = threading.Lock()
        self.stdout_thread: threading.Thread | None = None
        self.stderr_thread: threading.Thread | None = None
        self.request_id = 0

    def __enter__(self) -> "PersistentMcpClient":
        try:
            self.process = subprocess.Popen(
                [sys.executable, str(self.entry)],
                cwd=str(self.root),
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
            )
            self.stdout_thread = threading.Thread(
                target=self._drain_stdout,
                name="tenor-redteam-mcp-stdout",
                daemon=True,
            )
            self.stderr_thread = threading.Thread(
                target=self._drain_stderr,
                name="tenor-redteam-mcp-stderr",
                daemon=True,
            )
            self.stdout_thread.start()
            self.stderr_thread.start()
            initialized = self._rpc("initialize", {})
            if initialized.get("protocolVersion") != "2024-11-05":
                fail(f"MCP initialize returned an unexpected payload: {initialized}")
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def _drain_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            self.stdout_queue.put(None)
            return
        try:
            for line in process.stdout:
                self.stdout_queue.put(line)
        finally:
            self.stdout_queue.put(None)

    def _drain_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self.stderr_lines.append(line.rstrip("\n"))

    def _stderr_tail(self) -> str:
        return "\n".join(list(self.stderr_lines)[-50:])

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self.request_lock:
            process = self.process
            if process is None or process.stdin is None:
                fail("persistent MCP process is not started")
            if process.poll() is not None:
                fail(f"persistent MCP process exited rc={process.returncode}\nSTDERR={self._stderr_tail()}")
            self.request_id += 1
            request_id = self.request_id
            request = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            try:
                process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                fail(f"persistent MCP write failed: {exc}\nSTDERR={self._stderr_tail()}")
            try:
                raw = self.stdout_queue.get(timeout=self.timeout_seconds)
            except queue.Empty:
                fail(f"persistent MCP response timeout method={method}\nSTDERR={self._stderr_tail()}")
            if raw is None:
                fail(f"persistent MCP stdout closed method={method}\nSTDERR={self._stderr_tail()}")
            try:
                response = json.loads(raw)
            except json.JSONDecodeError:
                fail(f"persistent MCP returned non-json output method={method}: {raw!r}\nSTDERR={self._stderr_tail()}")
            if response.get("id") != request_id:
                fail(f"persistent MCP response id mismatch expected={request_id} got={response.get('id')}")
            if "error" in response:
                fail(f"persistent MCP protocol error method={method}: {response['error']}")
            result = response.get("result")
            if not isinstance(result, dict):
                fail(f"persistent MCP result must be an object method={method}: {result!r}")
            return result

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return self._rpc("tools/call", {"name": name, "arguments": args})

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError as exc:
                print(
                    f"MCP_PERSISTENT_STDIN_CLOSE_WARNING error={type(exc).__name__}:{exc}",
                    file=sys.stderr,
                )
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        for thread in (self.stdout_thread, self.stderr_thread):
            if thread is not None:
                thread.join(timeout=2)
        self.process = None


ACTIVE_CLIENT: PersistentMcpClient | None = None


def clean_runtime(root: Path = ROOT) -> None:
    reset_validation_runtime_database(root)


def clean_redteam() -> None:
    shutil.rmtree(REDTEAM_DIR, ignore_errors=True)
    TRIPWIRE_TARGET.unlink(missing_ok=True)


def prepare_redteam() -> None:
    REDTEAM_DIR.mkdir(parents=True, exist_ok=True)


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if ACTIVE_CLIENT is None:
        fail("persistent MCP client is not active")
    outer = ACTIVE_CLIENT.call_tool(name, args)
    if "content" in outer:
        return json.loads(outer["content"][0]["text"])
    return outer


def error_text(result: dict[str, Any]) -> str:
    return " ".join(str(result.get(key, "")) for key in ("code", "error", "status", "verdict", "reason"))


def assert_refused(result: dict[str, Any], expected: str, label: str) -> None:
    if result.get("ok") is not False and result.get("verdict") not in {"CLAIM_CONTEXT_NOT_READY", "DELETE_CONFIRMATION_REQUIRED"}:
        fail(f"{label} expected refusal, got {result}")
    if expected not in error_text(result):
        fail(f"{label} expected refusal containing {expected!r}, got {result}")
    if result.get("forbidden") and "direct_file_edit" not in result.get("forbidden", []):
        fail(f"{label} must forbid direct_file_edit: {result}")


def bootstrap(label: str) -> str:
    boot = call_tool("bootstrap", {"host_tool": label, "model_name": "redteam", "run_legacy_bootstrap": False})
    if boot.get("verdict") != "BOOT_OK_MCP":
        fail(f"bootstrap failed for {label}: {boot}")
    return boot["agent"]["agent_id"]


def register(label: str) -> str:
    registered = call_tool("register_agent", {"host_tool": label, "model_name": "redteam"})
    if registered.get("verdict") != "AGENT_REGISTERED":
        fail(f"register_agent failed for {label}: {registered}")
    return registered["agent"]["agent_id"]


def write_target(name: str, text: str = "redteam-original\n") -> str:
    prepare_redteam()
    target = REDTEAM_DIR / name
    target.write_text(text, encoding="utf-8")
    return rel(target)


def file_hash(target: str) -> str:
    result = call_tool("file_hash", {"resource": target})
    if result.get("verdict") != "FILE_HASH":
        fail(f"file_hash failed: {result}")
    return result["hash"]


def start_context(agent_id: str, target: str, request: str = "redteam write", intent: str = "write") -> dict[str, str]:
    before = call_tool("before_task", {"agent_id": agent_id, "request": request, "intent": intent, "resource": target})
    if before.get("verdict") != "BEFORE_TASK_OK":
        fail(f"before_task failed: {before}")
    return {"task_id": before["task_id"], "context_token": before["context_token"]}


def mark_scribe(agent_id: str, ctx: dict[str, str], query: str = "redteam write") -> None:
    result = call_tool("scribe_query", {"agent_id": agent_id, **ctx, "query": query, "limit": 5})
    if result.get("verdict") not in {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}:
        fail(f"scribe_query failed: {result}")


def mark_graphify(agent_id: str, target: str, ctx: dict[str, str], query: str = "redteam write") -> None:
    result = call_tool("graphify_query", {"agent_id": agent_id, **ctx, "query": query, "resource": target})
    if result.get("verdict") not in {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}:
        fail(f"graphify_query failed: {result}")


def ready_context(agent_id: str, target: str, request: str = "redteam write", intent: str = "write") -> dict[str, str]:
    ctx = start_context(agent_id, target, request, intent=intent)
    scoped_query = f"{request} resource:{target}" if target else request
    mark_scribe(agent_id, ctx, scoped_query)
    if intent != "read":
        mark_graphify(agent_id, target, ctx, scoped_query)
    return ctx


def acquire_lease(agent_id: str, action: str, ctx: dict[str, str] | None = None, resource: str = "") -> str:
    args: dict[str, Any] = {"agent_id": agent_id, "planned_action": action, "intent": "write"}
    if ctx:
        args["task_id"] = ctx.get("task_id", "")
        args["context_token"] = ctx.get("context_token", "")
    if resource:
        args["resource"] = resource
    result = call_tool("pre_action_guard", args)
    if result.get("verdict") == "PRE_ACTION_GUARD_OK" and "action_lease" in result:
        return result["action_lease"]["lease_id"]
    if ctx is not None:
        fail(f"pre_action_guard failed to issue lease for {action}: {result}")
    return ""


def claim(agent_id: str, target: str, ctx: dict[str, str]) -> str:
    lease_id = acquire_lease(agent_id, "claim_resource", ctx, resource=target)
    result = call_tool("claim_resource", {
        "agent_id": agent_id, "resource": target, "mode": "patch_queue",
        "ttl_seconds": 600, **ctx, "action_lease_id": lease_id,
    })
    if result.get("verdict") != "CLAIM_GRANTED":
        fail(f"claim_resource failed: {result}")
    return result["claim_id"]


def hard_lock(agent_id: str, target: str, ctx: dict[str, str]) -> str:
    result = call_tool("resource_lock_claim", {
        "agent_id": agent_id, "resource": target, "ttl_seconds": 600, **ctx,
    })
    if result.get("verdict") != "RESOURCE_LOCK_ACQUIRED":
        fail(f"resource_lock_claim failed: {result}")
    return result["lock_id"]


def propose(agent_id: str, target: str, base_hash: str, ctx: dict[str, str], replacement: str = "redteam-updated\n") -> dict[str, Any]:
    lease_id = acquire_lease(agent_id, "propose_patch", ctx, resource=target)
    return call_tool("propose_patch", {
        "agent_id": agent_id,
        "target": target,
        "base_hash": base_hash,
        "diff_text": f"@@ -1,1 +1,1 @@\n-redteam-original\n+{replacement.rstrip()}\n",
        **ctx, "action_lease_id": lease_id,
    })


def expect_patch(agent_id: str, target: str, base_hash: str, ctx: dict[str, str], replacement: str = "redteam-updated\n") -> str:
    result = propose(agent_id, target, base_hash, ctx, replacement)
    if result.get("status") != "PATCH_PROPOSED":
        fail(f"propose_patch failed: {result}")
    return result["patch_id"]


def test_positive_context_path() -> None:
    clean_redteam()
    target = write_target("positive-context.txt")
    agent = bootstrap("redteam-positive")
    ctx = ready_context(agent, target, "redteam positive context path")
    claim(agent, target, ctx)
    patch_id = expect_patch(agent, target, file_hash(target), ctx, replacement="positive-context-applied\n")
    hard_lock(agent, target, ctx)
    lease_id = acquire_lease(agent, "apply_patch", ctx)
    applied = call_tool("apply_patch", {"agent_id": agent, "patch_id": patch_id, **ctx, "action_lease_id": lease_id})
    if applied.get("verdict") != "PATCH_APPLIED":
        fail(f"positive context apply failed: {applied}")


def test_claim_requires_scribe_and_graphify() -> None:
    clean_redteam()
    target = write_target("missing-context.txt")
    agent = bootstrap("redteam-missing-context")
    ctx = start_context(agent, target, "redteam missing scribe")
    result = call_tool("claim_resource", {"agent_id": agent, "resource": target, "mode": "patch_queue", "ttl_seconds": 600, **ctx})
    assert_refused(result, "scribe_query", "claim_resource without scribe_query")
    mark_scribe(agent, ctx, "redteam missing graphify")
    result = call_tool("claim_resource", {"agent_id": agent, "resource": target, "mode": "patch_queue", "ttl_seconds": 600, **ctx})
    assert_refused(result, "graphify_query", "claim_resource without graphify_query")


def test_fake_token_and_read_intent_refused_at_claim() -> None:
    clean_redteam()
    target = write_target("fake-token.txt")
    agent = bootstrap("redteam-fake-token")
    ctx = ready_context(agent, target, "redteam fake token")
    bad_ctx = {**ctx, "context_token": "fake-token"}
    result = call_tool("claim_resource", {"agent_id": agent, "resource": target, "mode": "patch_queue", "ttl_seconds": 600, **bad_ctx})
    assert_refused(result, "TASK_CONTEXT_TOKEN_MISMATCH", "claim_resource with fake token")

    clean_redteam()
    target = write_target("read-intent.txt")
    agent = bootstrap("redteam-read-intent")
    read_ctx = ready_context(agent, target, "redteam read intent", intent="read")
    result = call_tool("claim_resource", {"agent_id": agent, "resource": target, "mode": "patch_queue", "ttl_seconds": 600, **read_ctx})
    assert_refused(result, "READ_ONLY_CLAIM_FORBIDDEN", "claim_resource with read intent")


def test_propose_apply_and_delete_guards() -> None:
    clean_redteam()
    target = write_target("guards.txt")
    agent = bootstrap("redteam-guards")
    ctx = ready_context(agent, target, "redteam guards")
    result = propose(agent, target, file_hash(target), ctx)
    assert_refused(result, "claim required", "propose_patch without claim")
    claim_id = claim(agent, target, ctx)
    patch_id = expect_patch(agent, target, file_hash(target), ctx)
    intruder = bootstrap("redteam-intruder")
    intruder_ctx = ready_context(intruder, target, "redteam intruder apply")
    intruder_lease = acquire_lease(intruder, "apply_patch", intruder_ctx)
    result = call_tool("apply_patch", {"agent_id": intruder, "patch_id": patch_id, **intruder_ctx, "action_lease_id": intruder_lease})
    assert_refused(result, "PATCH_NOT_FOUND", "apply_patch wrong agent")
    released = call_tool("release_claim", {"agent_id": agent, "claim_id": claim_id, "summary": "release before apply"})
    if released.get("verdict") != "CLAIM_RELEASED":
        fail(f"release_claim failed: {released}")
    hard_lock(agent, target, ctx)
    apply_lease = acquire_lease(agent, "apply_patch", ctx)
    result = call_tool("apply_patch", {"agent_id": agent, "patch_id": patch_id, **ctx, "action_lease_id": apply_lease})
    assert_refused(result, "claim required", "apply_patch without active claim")

    clean_redteam()
    target = write_target("delete-confirmation.txt")
    agent = bootstrap("redteam-delete")
    ctx = ready_context(agent, target, "redteam delete")
    claim(agent, target, ctx)
    lease_id = acquire_lease(agent, "delete_resource", ctx, resource=target)
    result = call_tool("delete_resource", {"agent_id": agent, "resource": target, "base_hash": file_hash(target), "confirm_phrase": "DELETE wrong-file", **ctx, "action_lease_id": lease_id})
    if result.get("verdict") != "DELETE_CONFIRMATION_REQUIRED" or not (ROOT / target).exists():
        fail(f"delete_resource should require exact confirmation: {result}")


def test_resource_mismatch_refused_at_claim() -> None:
    clean_redteam()
    file_a = write_target("resource-a.txt")
    file_b = write_target("resource-b.txt")
    agent = bootstrap("redteam-resource-mismatch")
    ctx = ready_context(agent, file_a, "redteam resource mismatch")
    result = call_tool("claim_resource", {"agent_id": agent, "resource": file_b, "mode": "patch_queue", "ttl_seconds": 600, **ctx})
    assert_refused(result, "TASK_CONTEXT_RESOURCE_MISMATCH", "claim_resource with mismatched context resource")


def test_context_bypass() -> str:
    clean_redteam()
    target = write_target("context-bypass.txt")
    agent = bootstrap("redteam-context-bypass")
    lease_id = acquire_lease(agent, "claim_resource", resource=target)
    claim_result = call_tool("claim_resource", {"agent_id": agent, "resource": target, "mode": "patch_queue", "ttl_seconds": 600, "action_lease_id": lease_id})
    if claim_result.get("verdict") == "CLAIM_GRANTED":
        print("MCP_CONTEXT_BYPASS_OPEN")
        return "OPEN"
    lease_id = acquire_lease(agent, "propose_patch", resource=target)
    result = call_tool("propose_patch", {
        "agent_id": agent,
        "target": target,
        "base_hash": file_hash(target),
        "diff_text": "@@ -1,1 +1,1 @@\n-redteam-original\n+context-bypass-applied\n",
        "action_lease_id": lease_id,
    })
    if result.get("status") == "PATCH_PROPOSED":
        patch_id = result["patch_id"]
        lease_apply = acquire_lease(agent, "apply_patch")
        applied = call_tool("apply_patch", {"agent_id": agent, "patch_id": patch_id, "action_lease_id": lease_apply})
        if applied.get("verdict") == "PATCH_APPLIED":
            print("MCP_CONTEXT_BYPASS_OPEN")
            return "OPEN"
    print("MCP_CONTEXT_BYPASS_CLOSED")
    return "CLOSED"


def test_direct_fs_write() -> str:
    try:
        prepare_redteam()
        direct = REDTEAM_DIR / "direct-shell.txt"
        direct.write_text("direct fs write outside MCP\n", encoding="utf-8")
        if direct.exists():
            print("DIRECT_FS_WRITE_OUTSIDE_SANDBOX_OPEN")
            return "OPEN"
        return "UNKNOWN"
    except OSError:
        return "BLOCKED"


def test_direct_fs_bypass_detection() -> str:
    clean_redteam()
    TRIPWIRE_TARGET.write_text("redteam original\n", encoding="utf-8")
    target = rel(TRIPWIRE_TARGET)
    try:
        agent = bootstrap("redteam-tripwire-bypass")
        ctx = ready_context(agent, target, "redteam tripwire bypass")
        TRIPWIRE_TARGET.write_text("tamper after snapshot\n", encoding="utf-8")
        result = call_tool("workspace_audit", {"agent_id": agent, "task_id": ctx["task_id"], "resource": target})
        if result.get("verdict") == "DIRECT_WRITE_BYPASS_DETECTED":
            print("DIRECT_FS_BYPASS_DETECTED_BY_TRIPWIRE")
            return "DETECTED"
        print("DIRECT_FS_BYPASS_NOT_DETECTED")
        return "MISSED"
    finally:
        TRIPWIRE_TARGET.unlink(missing_ok=True)


def main() -> int:
    global ACTIVE_CLIENT
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-context", action="store_true", help="fail if direct MCP write path bypasses before_task/scribe_query/graphify_query")
    args = parser.parse_args()
    if not ENTRY.is_file():
        fail(f"missing entrypoint: {ENTRY}")
    clean_runtime()
    clean_redteam()
    try:
        with PersistentMcpClient(ROOT, ENTRY) as client:
            ACTIVE_CLIENT = client
            test_positive_context_path()
            test_claim_requires_scribe_and_graphify()
            test_fake_token_and_read_intent_refused_at_claim()
            test_propose_apply_and_delete_guards()
            test_resource_mismatch_refused_at_claim()
            context_bypass = test_context_bypass()
            direct_fs = test_direct_fs_write()
            tripwire = test_direct_fs_bypass_detection()
            print(f"MCP_ENFORCEMENT_REDTEAM_OK context_bypass={context_bypass} direct_fs_outside_sandbox={direct_fs} direct_fs_tripwire={tripwire}")
            if args.strict_context and context_bypass == "OPEN":
                return 2
            return 0
    finally:
        ACTIVE_CLIENT = None
        clean_redteam()
        clean_runtime()


if __name__ == "__main__":
    try:
        with validation_runtime_lock(ROOT, timeout_seconds=0, poll_interval=0.01):
            print("VALIDATION_RUNTIME_LOCK_ACQUIRED")
            exit_code = main()
            print("VALIDATION_RUNTIME_LOCK_RELEASED")
            raise SystemExit(exit_code)
    except ValidationRuntimeBusy as exc:
        raise SystemExit(validation_runtime_busy_message(exc.lock_path))
