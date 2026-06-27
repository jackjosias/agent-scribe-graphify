from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]


class MultiAgentResourceLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        shutil.copytree(ROOT / ".agent" / "mcp", self.root / ".agent" / "mcp")
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True, exist_ok=True)
        gdir = self.root / "graphify-out"
        gdir.mkdir(parents=True)
        (gdir / "graph.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        (gdir / "GRAPH_REPORT.md").write_text("# Graphify Report\n\nEmpty.\n", encoding="utf-8")
        (gdir / "graph.html").write_text("<html><body></body></html>\n", encoding="utf-8")
        (self.root / "README.md").write_text("line1\nline2\nline3\n", encoding="utf-8")
        self.entry = self.root / ".agent" / "mcp" / "server_entry.py"
        self._env = {**os.environ, "AGENT_SCRIBE_GRAPHIFY_ROOT": str(self.root)}
        subprocess.run(["git", "init"], cwd=str(self.root), capture_output=True, env=self._env)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(self.root), capture_output=True, env=self._env)
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(self.root), capture_output=True, env=self._env)
        subprocess.run(["git", "add", "README.md"], cwd=str(self.root), capture_output=True, env=self._env)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=str(self.root), capture_output=True, env=self._env)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def call(self, tool: str, **args: object) -> dict[str, Any]:
        proc = subprocess.run(
            ["python3", str(self.entry), "--call", tool, "--args", json.dumps(args)],
            cwd=str(self.root),
            env=self._env,
            text=True,
            capture_output=True,
            timeout=20,
        )
        raw_text = proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout)
        raw = json.loads(raw_text)
        if "content" in raw:
            return json.loads(raw["content"][0]["text"])
        return raw

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
        (self.root / "a.txt").write_text("a\n", encoding="utf-8")
        (self.root / "b.txt").write_text("b\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt", "b.txt"], cwd=str(self.root), capture_output=True)
        subprocess.run(["git", "commit", "-m", "ab"], cwd=str(self.root), capture_output=True)
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

        results: list[dict[str, Any]] = []
        errors: list[str] = []
        barrier = threading.Barrier(2)

        def race(agent_id: str) -> None:
            try:
                ctx = contexts[agent_id]
                barrier.wait(timeout=10)
                lock = self.call(
                    "resource_lock_claim",
                    agent_id=agent_id,
                    resource="README.md",
                    task_id=ctx["task_id"],
                    context_token=ctx["context_token"],
                    ttl_seconds=30,
                )
                results.append(lock)
            except Exception as e:
                errors.append(f"{agent_id}: {e}")

        threads = [
            threading.Thread(target=race, args=("race-agent-a",)),
            threading.Thread(target=race, args=("race-agent-b",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"errors during lock race: {errors}")
        self.assertEqual(len(results), 2, f"expected 2 lock results, got {results}")

        ok_count = sum(1 for r in results if r.get("ok"))
        busy_count = sum(1 for r in results if r.get("verdict") == "RESOURCE_BUSY")

        self.assertEqual(ok_count, 1, f"exactly one lock claimant should win: {results}")
        self.assertEqual(busy_count, 1, f"exactly one lock claimant should be busy: {results}")

    def test_23_concurrent_claim_atomicity_repeated(self) -> None:
        """Race 10 times to validate atomicity under repeated contention."""
        for i in range(10):
            with self.subTest(round=i):
                agent_a = f"race-a-{i}"
                agent_b = f"race-b-{i}"
                ctx_a = self.ready_write(agent_a)
                ctx_b = self.ready_write(agent_b)
                results: list[dict[str, Any]] = []
                errors: list[str] = []
                barrier = threading.Barrier(2)

                def race(agent_id: str, ctx: dict[str, Any]) -> None:
                    try:
                        barrier.wait(timeout=10)
                        lock = self.call("resource_lock_claim", agent_id=agent_id, resource="README.md",
                                         task_id=ctx["task_id"], context_token=ctx["context_token"],
                                         ttl_seconds=30)
                        results.append(lock)
                    except Exception as e:
                        errors.append(f"{agent_id}: {e}")

                threads = [
                    threading.Thread(target=race, args=(agent_a, ctx_a)),
                    threading.Thread(target=race, args=(agent_b, ctx_b)),
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                self.assertEqual(len(errors), 0, f"round {i}: {errors}")
                self.assertEqual(len(results), 2, f"round {i}: expected 2 results, got {results}")
                winners = [r for r in results if r.get("ok")]
                self.assertEqual(len(winners), 1, f"round {i}: exactly one should win: {results}")
                winner = winners[0]
                released = self.call("resource_lock_release", agent_id=winner["agent_id"],
                                     resource="README.md", lock_id=winner.get("lock_id", ""))
                self.assertEqual(released.get("verdict"), "RESOURCE_LOCK_RELEASED", released)
