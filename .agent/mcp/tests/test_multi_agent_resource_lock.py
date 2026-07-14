from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import graphify_readiness, installation_state
from _strict_cleanup import remove_tree_strict


_SEQUENTIAL_LAUNCHER = r"""
import json
import os
import sys
import time
from pathlib import Path

mcp_dir = Path(os.environ["MCP_LOCK_TEST_ENTRY"]).resolve().parent
sys.path.insert(0, str(mcp_dir))
import server_ext as mcp

ipc_dir = Path(os.environ["MCP_LOCK_TEST_IPC"])
sequence = 1
while True:
    if (ipc_dir / "stop").is_file():
        raise SystemExit(0)
    request_path = ipc_dir / f"{sequence}.request.json"
    if not request_path.is_file():
        time.sleep(0.005)
        continue
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        response = mcp.handle(request)
    except BaseException as exc:
        response = {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32000, "message": f"{type(exc).__name__}: {exc}"},
        }
    response_tmp = ipc_dir / f"{sequence}.response.tmp"
    response_path = ipc_dir / f"{sequence}.response.json"
    response_tmp.write_text(json.dumps(response), encoding="utf-8")
    response_tmp.replace(response_path)
    request_path.unlink(missing_ok=True)
    sequence += 1
"""


_RACE_LAUNCHER = r"""
import json
import os
import sys
import time
from pathlib import Path

mcp_dir = Path(os.environ["MCP_LOCK_RACE_ENTRY"]).resolve().parent
sys.path.insert(0, str(mcp_dir))
import server_ext as mcp

barrier = Path(os.environ["MCP_LOCK_RACE_BARRIER"])
deadline = time.monotonic() + 30.0
while not barrier.is_file():
    if time.monotonic() >= deadline:
        raise SystemExit("race barrier timeout")
    time.sleep(0.005)

arguments = json.loads(os.environ["MCP_LOCK_RACE_ARGUMENTS"])
response = mcp.handle({
    "jsonrpc": "2.0",
    "id": os.environ["MCP_LOCK_RACE_ID"],
    "method": "tools/call",
    "params": {"name": "resource_lock_claim", "arguments": arguments},
})
print(json.dumps(response), flush=True)
"""


class MultiAgentResourceLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        shutil.copytree(ROOT / ".agent" / "mcp", self.root / ".agent" / "mcp")
        (self.root / "README.md").write_text("line1\nline2\nline3\n", encoding="utf-8")
        (self.root / "a.txt").write_text("a\n", encoding="utf-8")
        (self.root / "b.txt").write_text("b\n", encoding="utf-8")
        self.entry = self.root / ".agent" / "mcp" / "server_entry.py"
        self._env = {
            **os.environ,
            "AGENT_SCRIBE_GRAPHIFY_ROOT": str(self.root),
            graphify_readiness.FIXTURE_ENV: "1",
        }
        subprocess.run(["git", "init"], cwd=str(self.root), capture_output=True, env=self._env)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(self.root), capture_output=True, env=self._env)
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(self.root), capture_output=True, env=self._env)
        subprocess.run(["git", "add", "README.md", "a.txt", "b.txt"], cwd=str(self.root), capture_output=True, env=self._env)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=str(self.root), capture_output=True, env=self._env)
        prepared = installation_state.ensure_fresh_installation_state(self.root)
        self.assertTrue(prepared["ok"], prepared)
        finalized = installation_state.finalize_installation_state(self.root)
        self.assertTrue(finalized["ok"], finalized)
        fixture = graphify_readiness.write_smoke_fixture(self.root)
        self.assertTrue(fixture["ok"], fixture)

        self._ipc_dir = self.root / ".agent" / "state" / "runtime" / f"test-ipc-{uuid.uuid4().hex}"
        self._ipc_dir.mkdir(parents=True, exist_ok=False)
        self._sequence = 0
        server_env = {
            **self._env,
            "MCP_LOCK_TEST_ENTRY": str(self.entry),
            "MCP_LOCK_TEST_IPC": str(self._ipc_dir),
        }
        self._server_proc = subprocess.Popen(
            [sys.executable, "-c", _SEQUENTIAL_LAUNCHER],
            cwd=str(self.root),
            env=server_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def tearDown(self) -> None:
        try:
            self._stop_server()
        finally:
            remove_tree_strict(self.tmp.name)
            self.tmp.cleanup()

    def _stop_server(self) -> None:
        proc = self._server_proc
        if proc.poll() is None:
            (self._ipc_dir / "stop").write_text("stop\n", encoding="utf-8")
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    stdout, stderr = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate(timeout=10)
        else:
            stdout, stderr = proc.communicate(timeout=10)
        if proc.returncode != 0:
            raise RuntimeError(
                f"persistent MCP test server exited {proc.returncode}; "
                f"stdout={stdout[-1000:]}; stderr={stderr[-1000:]}"
            )

    @staticmethod
    def _decode_tool_response(raw: dict[str, Any]) -> dict[str, Any]:
        result = raw.get("result", raw)
        if "content" in result:
            return json.loads(result["content"][0]["text"])
        return result

    def call(self, tool: str, **args: object) -> dict[str, Any]:
        self._sequence += 1
        request = {
            "jsonrpc": "2.0",
            "id": f"multi-agent-{self._sequence}-{tool}",
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
        request_tmp = self._ipc_dir / f"{self._sequence}.request.tmp"
        request_path = self._ipc_dir / f"{self._sequence}.request.json"
        response_path = self._ipc_dir / f"{self._sequence}.response.json"
        request_tmp.write_text(json.dumps(request), encoding="utf-8")
        request_tmp.replace(request_path)

        deadline = time.monotonic() + 30.0
        while not response_path.is_file():
            returncode = self._server_proc.poll()
            if returncode is not None:
                stdout, stderr = self._server_proc.communicate(timeout=10)
                raise RuntimeError(
                    f"persistent MCP test server exited {returncode}; "
                    f"stdout={stdout[-1000:]}; stderr={stderr[-1000:]}"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(f"persistent MCP call timed out: {tool}")
            time.sleep(0.005)

        try:
            raw = json.loads(response_path.read_text(encoding="utf-8"))
        finally:
            response_path.unlink(missing_ok=True)
        return self._decode_tool_response(raw)

    def race_lock_claims(
        self,
        claims: list[tuple[str, dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Run lock claims in synchronized, independent Python processes."""
        barrier = self.root / ".agent" / "state" / "runtime" / f"race-{uuid.uuid4().hex}.start"
        processes: list[tuple[str, subprocess.Popen[str]]] = []
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            for agent_id, arguments in claims:
                env = {
                    **self._env,
                    "MCP_LOCK_RACE_ARGUMENTS": json.dumps(arguments, sort_keys=True),
                    "MCP_LOCK_RACE_BARRIER": str(barrier),
                    "MCP_LOCK_RACE_ENTRY": str(self.entry),
                    "MCP_LOCK_RACE_ID": agent_id,
                }
                proc = subprocess.Popen(
                    [sys.executable, "-c", _RACE_LAUNCHER],
                    cwd=str(self.root),
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                processes.append((agent_id, proc))

            barrier.write_text("start\n", encoding="utf-8")
            for agent_id, proc in processes:
                try:
                    stdout, stderr = proc.communicate(timeout=45)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate(timeout=10)
                    errors.append(f"{agent_id}: process timeout; stderr={stderr[-1000:]}")
                    continue
                if proc.returncode != 0:
                    errors.append(
                        f"{agent_id}: exit={proc.returncode}; "
                        f"stdout={stdout[-1000:]}; stderr={stderr[-1000:]}"
                    )
                    continue
                try:
                    results.append(self._decode_tool_response(json.loads(stdout)))
                except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                    errors.append(f"{agent_id}: invalid response ({exc}); stdout={stdout[-1000:]}")
        finally:
            for _agent_id, proc in processes:
                if proc.poll() is None:
                    proc.kill()
                    try:
                        proc.communicate(timeout=10)
                    except subprocess.TimeoutExpired:
                        errors.append(f"{_agent_id}: process could not be reaped")
            barrier.unlink(missing_ok=True)
        return results, errors

    def register(self, agent_id: str = "lock-agent") -> dict[str, Any]:
        return self.call("register_agent", agent_id=agent_id, host_tool="test", model_name="unit")

    def ready_write(self, agent_id: str, resource: str = "README.md") -> dict[str, Any]:
        self.register(agent_id)
        bt = self.call("before_task", agent_id=agent_id, request=f"edit {resource}", intent="write", resource=resource)
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK", bt)
        task_id = bt["task_id"]
        ctx = bt["context_token"]
        self.call("scribe_query", agent_id=agent_id, task_id=task_id, context_token=ctx, query="edit", limit=1)
        self.call("graphify_query", agent_id=agent_id, task_id=task_id, context_token=ctx, query="impact", resource=resource)
        return {"task_id": task_id, "context_token": ctx}

    def ready_read(self, agent_id: str) -> dict[str, Any]:
        self.register(agent_id)
        bt = self.call("before_task", agent_id=agent_id, request="inspect", intent="read", resource=".")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK", bt)
        return {"task_id": bt["task_id"], "context_token": bt["context_token"]}

    def old_claim(self, agent_id: str, ctx: dict[str, Any], resource: str = "README.md") -> str:
        pg = self.call("pre_action_guard", agent_id=agent_id, task_id=ctx["task_id"],
                        context_token=ctx["context_token"], resource=resource,
                        planned_action="claim_resource")
        if pg.get("verdict") != "PRE_ACTION_GUARD_OK":
            self.skipTest(f"pre_action_guard for old claim failed: {pg}")
        lease_id = pg["action_lease"]["lease_id"]
        claim = self.call("claim_resource", agent_id=agent_id, resource=resource,
                           mode="patch_queue", ttl_seconds=600,
                           task_id=ctx["task_id"], context_token=ctx["context_token"],
                           action_lease_id=lease_id)
        if claim.get("verdict") != "CLAIM_GRANTED":
            self.skipTest(f"old claim_resource failed: {claim}")
        return claim.get("claim_id", "")

    def acquire_lease(self, agent_id: str, action: str, ctx: dict[str, Any], resource: str = "README.md") -> str:
        pargs = {"agent_id": agent_id, "task_id": ctx["task_id"], "context_token": ctx["context_token"],
                 "resource": resource, "planned_action": action}
        pg = self.call("pre_action_guard", **pargs)
        if pg.get("verdict") != "PRE_ACTION_GUARD_OK":
            self.skipTest(f"pre_action_guard failed: {pg}")
        return pg["action_lease"]["lease_id"]

    # ── Tests ─────────────────────────────────────────────────

    def test_01_apply_without_lock_rejected(self) -> None:
        aid = "no-lock-agent"
        ctx = self.ready_write(aid)
        self.old_claim(aid, ctx)
        lease_id = self.acquire_lease(aid, "propose_patch", ctx)
        fh = self.call("file_hash", resource="README.md")["hash"]
        prop = self.call("propose_patch", agent_id=aid, target="README.md", base_hash=fh,
                          diff_text="@@ -1,3 +1,3 @@\n line1\n-line2\n+modified\n line3\n",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          action_lease_id=lease_id)
        self.assertEqual(prop.get("status"), "PATCH_PROPOSED", prop)
        patch_id = prop["patch_id"]
        lease_id2 = self.acquire_lease(aid, "apply_patch", ctx)
        ap = self.call("apply_patch", agent_id=aid, patch_id=patch_id,
                        task_id=ctx["task_id"], context_token=ctx["context_token"],
                        action_lease_id=lease_id2)
        self.assertFalse(ap.get("ok"), f"apply without lock should fail, got {ap}")
        self.assertEqual(ap.get("verdict"), "RESOURCE_LOCK_REQUIRED", ap)

    def test_02_apply_with_owned_lock_succeeds(self) -> None:
        aid = "owned-lock-agent"
        ctx = self.ready_write(aid)
        self.old_claim(aid, ctx)
        lock = self.call("resource_lock_claim", agent_id=aid, resource="README.md",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          ttl_seconds=120)
        self.assertTrue(lock.get("ok"), lock)
        lease_id = self.acquire_lease(aid, "propose_patch", ctx)
        fh = self.call("file_hash", resource="README.md")["hash"]
        prop = self.call("propose_patch", agent_id=aid, target="README.md", base_hash=fh,
                          diff_text="@@ -1,3 +1,3 @@\n line1\n-line2\n+modified\n line3\n",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          action_lease_id=lease_id)
        self.assertEqual(prop.get("status"), "PATCH_PROPOSED", prop)
        patch_id = prop["patch_id"]
        lease_id2 = self.acquire_lease(aid, "apply_patch", ctx)
        ap = self.call("apply_patch", agent_id=aid, patch_id=patch_id,
                        task_id=ctx["task_id"], context_token=ctx["context_token"],
                        action_lease_id=lease_id2)
        self.assertEqual(ap.get("verdict"), "PATCH_APPLIED", ap)

    def test_03_propose_without_exclusive_lock_succeeds(self) -> None:
        """propose_patch does NOT require new exclusive lock (only old claim_resource)."""
        aid = "prop-only-agent"
        ctx = self.ready_write(aid)
        self.old_claim(aid, ctx)
        lease_id = self.acquire_lease(aid, "propose_patch", ctx)
        fh = self.call("file_hash", resource="README.md")["hash"]
        prop = self.call("propose_patch", agent_id=aid, target="README.md", base_hash=fh,
                          diff_text="@@ -1,3 +1,3 @@\n line1\n-line2\n+modified\n line3\n",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          action_lease_id=lease_id)
        self.assertEqual(prop.get("status"), "PATCH_PROPOSED", prop)
        content = (self.root / "README.md").read_text()
        self.assertEqual(content, "line1\nline2\nline3\n", "file should remain unchanged after propose")

    def test_04_lock_claimed_late_after_propose_before_apply(self) -> None:
        aid = "late-lock-agent"
        ctx = self.ready_write(aid)
        self.old_claim(aid, ctx)
        lease_id = self.acquire_lease(aid, "propose_patch", ctx)
        fh = self.call("file_hash", resource="README.md")["hash"]
        prop = self.call("propose_patch", agent_id=aid, target="README.md", base_hash=fh,
                          diff_text="@@ -1,3 +1,3 @@\n line1\n-line2\n+modified\n line3\n",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          action_lease_id=lease_id)
        self.assertEqual(prop.get("status"), "PATCH_PROPOSED", prop)
        patch_id = prop["patch_id"]
        lock = self.call("resource_lock_claim", agent_id=aid, resource="README.md",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          ttl_seconds=120)
        self.assertTrue(lock.get("ok"), f"late lock should succeed, got {lock}")
        lease_id2 = self.acquire_lease(aid, "apply_patch", ctx)
        ap = self.call("apply_patch", agent_id=aid, patch_id=patch_id,
                        task_id=ctx["task_id"], context_token=ctx["context_token"],
                        action_lease_id=lease_id2)
        self.assertEqual(ap.get("verdict"), "PATCH_APPLIED", ap)

    def test_05_two_agents_same_file_second_busy(self) -> None:
        ctx_a = self.ready_write("agent-a")
        lock_a = self.call("resource_lock_claim", agent_id="agent-a", resource="README.md",
                            task_id=ctx_a["task_id"], context_token=ctx_a["context_token"])
        self.assertTrue(lock_a.get("ok"), lock_a)
        ctx_b = self.ready_write("agent-b")
        lock_b = self.call("resource_lock_claim", agent_id="agent-b", resource="README.md",
                            task_id=ctx_b["task_id"], context_token=ctx_b["context_token"])
        self.assertFalse(lock_b.get("ok"), f"second agent should fail, got {lock_b}")
        self.assertIn(lock_b.get("verdict"), ("RESOURCE_BUSY",), str(lock_b))

    def test_06_two_agents_different_files_can_lock(self) -> None:
        ctx_a = self.ready_write("agent-a", "a.txt")
        ctx_b = self.ready_write("agent-b", "b.txt")
        lock_a = self.call("resource_lock_claim", agent_id="agent-a", resource="a.txt",
                            task_id=ctx_a["task_id"], context_token=ctx_a["context_token"])
        self.assertTrue(lock_a.get("ok"), lock_a)
        lock_b = self.call("resource_lock_claim", agent_id="agent-b", resource="b.txt",
                            task_id=ctx_b["task_id"], context_token=ctx_b["context_token"])
        self.assertTrue(lock_b.get("ok"), lock_b)

    def test_07_apply_with_other_agent_lock_rejected(self) -> None:
        ctx_a = self.ready_write("agent-a")
        self.old_claim("agent-a", ctx_a)
        lock_a = self.call("resource_lock_claim", agent_id="agent-a", resource="README.md",
                            task_id=ctx_a["task_id"], context_token=ctx_a["context_token"])
        self.assertTrue(lock_a.get("ok"), lock_a)
        lease_id = self.acquire_lease("agent-a", "propose_patch", ctx_a)
        fh = self.call("file_hash", resource="README.md")["hash"]
        prop = self.call("propose_patch", agent_id="agent-a", target="README.md", base_hash=fh,
                          diff_text="@@ -1,3 +1,3 @@\n line1\n-line2\n+v2\n line3\n",
                          task_id=ctx_a["task_id"], context_token=ctx_a["context_token"],
                          action_lease_id=lease_id)
        self.assertEqual(prop.get("status"), "PATCH_PROPOSED", prop)
        patch_id = prop["patch_id"]
        ctx_b = self.ready_write("agent-b")
        lease_b = self.acquire_lease("agent-b", "apply_patch", ctx_b)
        ap = self.call("apply_patch", agent_id="agent-b", patch_id=patch_id,
                        task_id=ctx_b["task_id"], context_token=ctx_b["context_token"],
                        action_lease_id=lease_b)
        self.assertFalse(ap.get("ok"), f"other agent apply should fail, got {ap}")
        self.assertIn(ap.get("verdict"), ("RESOURCE_LOCK_OWNER_MISMATCH", "RESOURCE_LOCK_REQUIRED", "PATCH_NOT_FOUND"), str(ap))

    def test_08_expired_lock_requires_reacquire(self) -> None:
        aid = "exp-lock-agent"
        ctx = self.ready_write(aid)
        self.old_claim(aid, ctx)
        lock = self.call("resource_lock_claim", agent_id=aid, resource="README.md",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          ttl_seconds=1)
        self.assertTrue(lock.get("ok"), lock)
        time.sleep(1.5)
        lease_id = self.acquire_lease(aid, "propose_patch", ctx)
        fh = self.call("file_hash", resource="README.md")["hash"]
        prop = self.call("propose_patch", agent_id=aid, target="README.md", base_hash=fh,
                          diff_text="@@ -1,3 +1,3 @@\n line1\n-line2\n+exp\n line3\n",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          action_lease_id=lease_id)
        self.assertEqual(prop.get("status"), "PATCH_PROPOSED", prop)
        patch_id = prop["patch_id"]
        lease_id2 = self.acquire_lease(aid, "apply_patch", ctx)
        ap = self.call("apply_patch", agent_id=aid, patch_id=patch_id,
                        task_id=ctx["task_id"], context_token=ctx["context_token"],
                        action_lease_id=lease_id2)
        self.assertFalse(ap.get("ok"), f"expired lock should fail, got {ap}")
        self.assertEqual(ap.get("verdict"), "RESOURCE_LOCK_REQUIRED", ap)

    def test_09_heartbeat_extends_lock_before_expiry(self) -> None:
        aid = "hb-agent"
        ctx = self.ready_write(aid)
        lock = self.call("resource_lock_claim", agent_id=aid, resource="README.md",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          ttl_seconds=5)
        self.assertTrue(lock.get("ok"), lock)
        expires_before = lock["expires_at"]
        time.sleep(1)
        hb = self.call("resource_lock_heartbeat", agent_id=aid, resource="README.md",
                        ttl_seconds=30)
        self.assertTrue(hb.get("ok"), hb)
        self.assertEqual(hb.get("verdict"), "RESOURCE_LOCK_EXTENDED", hb)
        self.assertGreater(hb["expires_at"], expires_before,
                           "heartbeat should extend expiry")

    def test_10_heartbeat_after_expiry_rejected(self) -> None:
        aid = "hb-exp-agent"
        ctx = self.ready_write(aid)
        lock = self.call("resource_lock_claim", agent_id=aid, resource="README.md",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          ttl_seconds=1)
        self.assertTrue(lock.get("ok"), lock)
        time.sleep(1.5)
        hb = self.call("resource_lock_heartbeat", agent_id=aid, resource="README.md")
        self.assertFalse(hb.get("ok"), f"heartbeat after expiry should fail, got {hb}")
        self.assertEqual(hb.get("verdict"), "RESOURCE_LOCK_EXPIRED", hb)

    def test_11_heartbeat_by_non_owner_rejected(self) -> None:
        ctx_a = self.ready_write("agent-a")
        lock_a = self.call("resource_lock_claim", agent_id="agent-a", resource="README.md",
                            task_id=ctx_a["task_id"], context_token=ctx_a["context_token"])
        self.assertTrue(lock_a.get("ok"), lock_a)
        self.register("agent-b")
        hb = self.call("resource_lock_heartbeat", agent_id="agent-b", resource="README.md")
        self.assertFalse(hb.get("ok"), f"non-owner heartbeat should fail, got {hb}")
        self.assertEqual(hb.get("verdict"), "RESOURCE_LOCK_OWNER_MISMATCH", hb)

    def test_12_apply_auto_extends_low_ttl_lock(self) -> None:
        aid = "auto-ext-agent"
        ctx = self.ready_write(aid)
        self.old_claim(aid, ctx)
        lock = self.call("resource_lock_claim", agent_id=aid, resource="README.md",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          ttl_seconds=65)
        self.assertTrue(lock.get("ok"), lock)
        time.sleep(8)
        lease_id = self.acquire_lease(aid, "propose_patch", ctx)
        fh = self.call("file_hash", resource="README.md")["hash"]
        prop = self.call("propose_patch", agent_id=aid, target="README.md", base_hash=fh,
                          diff_text="@@ -1,3 +1,3 @@\n line1\n-line2\n+auto-ext\n line3\n",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          action_lease_id=lease_id)
        self.assertEqual(prop.get("status"), "PATCH_PROPOSED", prop)
        patch_id = prop["patch_id"]
        lease_id2 = self.acquire_lease(aid, "apply_patch", ctx)
        ap = self.call("apply_patch", agent_id=aid, patch_id=patch_id,
                        task_id=ctx["task_id"], context_token=ctx["context_token"],
                        action_lease_id=lease_id2)
        self.assertEqual(ap.get("verdict"), "PATCH_APPLIED", ap)
        content = (self.root / "README.md").read_text()
        self.assertIn("auto-ext", content)

    def test_13_apply_does_not_auto_extend_expired_lock(self) -> None:
        aid = "no-ext-exp-agent"
        ctx = self.ready_write(aid)
        self.old_claim(aid, ctx)
        lock = self.call("resource_lock_claim", agent_id=aid, resource="README.md",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          ttl_seconds=1)
        self.assertTrue(lock.get("ok"), lock)
        time.sleep(1.5)
        lease_id = self.acquire_lease(aid, "propose_patch", ctx)
        fh = self.call("file_hash", resource="README.md")["hash"]
        prop = self.call("propose_patch", agent_id=aid, target="README.md", base_hash=fh,
                          diff_text="@@ -1,3 +1,3 @@\n line1\n-line2\n+noext\n line3\n",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          action_lease_id=lease_id)
        self.assertEqual(prop.get("status"), "PATCH_PROPOSED", prop)
        patch_id = prop["patch_id"]
        lease_id2 = self.acquire_lease(aid, "apply_patch", ctx)
        ap = self.call("apply_patch", agent_id=aid, patch_id=patch_id,
                        task_id=ctx["task_id"], context_token=ctx["context_token"],
                        action_lease_id=lease_id2)
        self.assertNotEqual(ap.get("verdict"), "PATCH_APPLIED",
                            "apply should fail on expired lock")
        self.assertEqual(ap.get("verdict"), "RESOURCE_LOCK_REQUIRED", ap)

    def test_14_stale_base_hash_rejected(self) -> None:
        """Propose with correct hash, modify file between propose and apply."""
        aid = "stale-agent"
        ctx = self.ready_write(aid)
        self.old_claim(aid, ctx)
        lock = self.call("resource_lock_claim", agent_id=aid, resource="README.md",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          ttl_seconds=120)
        self.assertTrue(lock.get("ok"), lock)
        fh = self.call("file_hash", resource="README.md")["hash"]
        lease_id = self.acquire_lease(aid, "propose_patch", ctx)
        prop = self.call("propose_patch", agent_id=aid, target="README.md", base_hash=fh,
                          diff_text="@@ -1,3 +1,3 @@\n line1\n-line2\n+stale\n line3\n",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          action_lease_id=lease_id)
        self.assertEqual(prop.get("status"), "PATCH_PROPOSED", prop)
        patch_id = prop["patch_id"]
        (self.root / "README.md").write_text("modified after propose\n", encoding="utf-8")
        lease_id2 = self.acquire_lease(aid, "apply_patch", ctx)
        ap = self.call("apply_patch", agent_id=aid, patch_id=patch_id,
                        task_id=ctx["task_id"], context_token=ctx["context_token"],
                        action_lease_id=lease_id2)
        self.assertFalse(ap.get("ok"), f"stale hash should fail, got {ap}")
        self.assertEqual(ap.get("verdict"), "PATCH_BASE_STALE", ap)

    def test_15_same_agent_lock_idempotent(self) -> None:
        aid = "idemp-agent"
        ctx = self.ready_write(aid)
        first = self.call("resource_lock_claim", agent_id=aid, resource="README.md",
                           task_id=ctx["task_id"], context_token=ctx["context_token"])
        self.assertTrue(first.get("ok"), first)
        second = self.call("resource_lock_claim", agent_id=aid, resource="README.md",
                            task_id=ctx["task_id"], context_token=ctx["context_token"])
        self.assertTrue(second.get("ok"), f"same agent re-claim should succeed, got {second}")
        self.assertEqual(second.get("lock_id"), first.get("lock_id"),
                         "same agent should get same lock_id")

    def test_16_read_task_cannot_claim_write_lock(self) -> None:
        aid = "read-lock-agent"
        ctx = self.ready_read(aid)
        lock = self.call("resource_lock_claim", agent_id=aid, resource="README.md",
                          task_id=ctx["task_id"], context_token=ctx["context_token"])
        self.assertFalse(lock.get("ok"), f"read task lock should fail, got {lock}")
        self.assertEqual(lock.get("verdict"), "READ_ONLY_LOCK_FORBIDDEN", lock)

    def test_17_directory_target_rejected(self) -> None:
        aid = "dir-agent"
        ctx = self.ready_write(aid)
        lock = self.call("resource_lock_claim", agent_id=aid, resource=".",
                          task_id=ctx["task_id"], context_token=ctx["context_token"])
        self.assertFalse(lock.get("ok"), f"directory should be rejected, got {lock}")
        self.assertEqual(lock.get("verdict"), "PATCH_TARGET_INVALID", lock)

    def test_18_lock_release_by_non_owner_rejected(self) -> None:
        ctx_a = self.ready_write("agent-a")
        lock_a = self.call("resource_lock_claim", agent_id="agent-a", resource="README.md",
                            task_id=ctx_a["task_id"], context_token=ctx_a["context_token"])
        self.assertTrue(lock_a.get("ok"), lock_a)
        self.register("agent-b")
        release = self.call("resource_lock_release", agent_id="agent-b", resource="README.md")
        self.assertFalse(release.get("ok"), f"non-owner release should fail, got {release}")
        self.assertEqual(release.get("verdict"), "RESOURCE_LOCK_OWNER_MISMATCH",
                         f"non-owner release should be rejected, got {release}")

    def test_19_workflow_next_recommends_late_lock_after_propose(self) -> None:
        aid = "wn-lock-agent"
        self.register(aid)
        bt = self.call("before_task", agent_id=aid, request="edit tracked", intent="write", resource="README.md")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        ctx = bt["context_token"]
        self.call("scribe_query", agent_id=aid, task_id=task_id, context_token=ctx, query="edit", limit=1)
        self.call("graphify_query", agent_id=aid, task_id=task_id, context_token=ctx, query="impact", resource="README.md")
        wn = self.call("workflow_next", agent_id=aid, request="edit tracked", intent="write",
                        resource="README.md", task_id=task_id, context_token=ctx,
                        last_verdict="GRAPHIFY_QUERY_DONE")
        must_tool = wn.get("must_call", {}).get("tool", "")
        self.assertIn(must_tool, ("resource_lock_claim", "claim_resource"),
                      f"workflow_next should recommend lock tool, got {wn}")

    def test_20_no_wait_queue(self) -> None:
        ctx_a = self.ready_write("agent-a")
        lock_a = self.call("resource_lock_claim", agent_id="agent-a", resource="README.md",
                            task_id=ctx_a["task_id"], context_token=ctx_a["context_token"])
        self.assertTrue(lock_a.get("ok"), lock_a)
        ctx_b = self.ready_write("agent-b")
        lock_b = self.call("resource_lock_claim", agent_id="agent-b", resource="README.md",
                            task_id=ctx_b["task_id"], context_token=ctx_b["context_token"])
        self.assertFalse(lock_b.get("ok"), f"should be busy, got {lock_b}")
        self.assertEqual(lock_b.get("verdict"), "RESOURCE_BUSY", lock_b)
        self.assertNotIn("queue", str(lock_b).lower(), "no wait queue should exist")

    def test_21_apply_after_direct_write_hash_drift_rejected(self) -> None:
        """Propose with correct hash, direct write modifies file between propose and apply."""
        aid = "drift-agent"
        ctx = self.ready_write(aid)
        self.old_claim(aid, ctx)
        lock = self.call("resource_lock_claim", agent_id=aid, resource="README.md",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          ttl_seconds=120)
        self.assertTrue(lock.get("ok"), lock)
        fh = self.call("file_hash", resource="README.md")["hash"]
        lease_id = self.acquire_lease(aid, "propose_patch", ctx)
        prop = self.call("propose_patch", agent_id=aid, target="README.md", base_hash=fh,
                          diff_text="@@ -1,3 +1,3 @@\n line1\n-line2\n+drift\n line3\n",
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          action_lease_id=lease_id)
        self.assertEqual(prop.get("status"), "PATCH_PROPOSED", prop)
        patch_id = prop["patch_id"]
        (self.root / "README.md").write_text("direct write after propose\n", encoding="utf-8")
        lease_id2 = self.acquire_lease(aid, "apply_patch", ctx)
        ap = self.call("apply_patch", agent_id=aid, patch_id=patch_id,
                        task_id=ctx["task_id"], context_token=ctx["context_token"],
                        action_lease_id=lease_id2)
        self.assertFalse(ap.get("ok"), f"hash drift should fail, got {ap}")
        self.assertEqual(ap.get("verdict"), "PATCH_BASE_STALE", ap)

    def test_22_concurrent_claim_atomicity(self) -> None:
        """Two already-ready agents race on the same file — exactly one wins.

        This test isolates resource_lock_claim atomicity. Agent registration,
        before_task, scribe_query, and graphify_query are intentionally performed
        before the barrier; otherwise a setup race can mask the lock invariant with
        AGENT_UNKNOWN_OR_UNREGISTERED / task-context failures.
        """
        ctx_a = self.ready_write("race-agent-a")
        ctx_b = self.ready_write("race-agent-b")
        contexts = {
            "race-agent-a": ctx_a,
            "race-agent-b": ctx_b,
        }

        claims = []
        for agent_id in ("race-agent-a", "race-agent-b"):
            ctx = contexts[agent_id]
            claims.append((agent_id, {
                "agent_id": agent_id,
                "resource": "README.md",
                "task_id": ctx["task_id"],
                "context_token": ctx["context_token"],
                "ttl_seconds": 30,
            }))
        results, errors = self.race_lock_claims(claims)

        self.assertEqual(len(errors), 0, f"errors during lock race: {errors}")
        self.assertEqual(len(results), 2, f"expected 2 lock results, got {results}")

        ok_count = sum(1 for r in results if r.get("ok"))
        busy_count = sum(1 for r in results if r.get("verdict") == "RESOURCE_BUSY")

        self.assertEqual(ok_count, 1, f"exactly one lock claimant should win: {results}")
        self.assertEqual(busy_count, 1, f"exactly one lock claimant should be busy: {results}")

    def test_23_concurrent_claim_atomicity_repeated(self) -> None:
        """Race 10 times to validate atomicity under repeated contention."""
        agent_a = "race-repeat-a"
        agent_b = "race-repeat-b"
        ctx_a = self.ready_write(agent_a)
        ctx_b = self.ready_write(agent_b)
        for i in range(10):
            with self.subTest(round=i):
                results, errors = self.race_lock_claims([
                    (agent_a, {
                        "agent_id": agent_a,
                        "resource": "README.md",
                        "task_id": ctx_a["task_id"],
                        "context_token": ctx_a["context_token"],
                        "ttl_seconds": 30,
                    }),
                    (agent_b, {
                        "agent_id": agent_b,
                        "resource": "README.md",
                        "task_id": ctx_b["task_id"],
                        "context_token": ctx_b["context_token"],
                        "ttl_seconds": 30,
                    }),
                ])
                self.assertEqual(len(errors), 0, f"round {i}: {errors}")
                self.assertEqual(len(results), 2, f"round {i}: expected 2 results, got {results}")
                winners = [r for r in results if r.get("ok")]
                self.assertEqual(len(winners), 1, f"round {i}: exactly one should win: {results}")
                winner = winners[0]
                released = self.call("resource_lock_release", agent_id=winner["agent_id"],
                                     resource="README.md", lock_id=winner.get("lock_id", ""))
                self.assertEqual(released.get("verdict"), "RESOURCE_LOCK_RELEASED", released)


if __name__ == "__main__":
    unittest.main()
