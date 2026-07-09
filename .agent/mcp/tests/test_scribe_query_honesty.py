from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import server_ext as mcp
from runtime import direct_fs_tripwire

TIMEOUT_MS = 25_000
FILE_CONTENT = "line1\nline2\nline3\n"


ROOT = Path(__file__).resolve().parents[3]


def _make_scribe_rag(root: Path, returncode: int = 0, stderr: str = "", stdout: str = "") -> Path:
    scribe_dir = root / ".agent" / "workflow" / "scribe"
    scribe_dir.mkdir(parents=True, exist_ok=True)
    script = scribe_dir / "scribe-rag"
    lines = ["#!/usr/bin/env python3", "import sys"]
    if stdout:
        escaped = stdout.replace("\\", "\\\\").replace("'", "'\"'\"'").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        lines.append(f"sys.stdout.write('{escaped}')")
    if stderr:
        escaped = stderr.replace("\\", "\\\\").replace("'", "'\"'\"'").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        lines.append(f"sys.stderr.write('{escaped}')")
    lines.append(f"sys.exit({returncode})")
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


class ScribeQueryHonestyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        shutil.copytree(ROOT / ".agent" / "mcp", self.root / ".agent" / "mcp")
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.root / ".agent" / "state" / "outputs").mkdir(parents=True, exist_ok=True)
        graphify_dir = self.root / "graphify-out"
        graphify_dir.mkdir(parents=True)
        (graphify_dir / "graph.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        (graphify_dir / "GRAPH_REPORT.md").write_text("# Graphify Report\n\nEmpty.\n", encoding="utf-8")
        (graphify_dir / "graph.html").write_text("<html><body></body></html>\n", encoding="utf-8")
        (self.root / "README.md").write_text("test project\n", encoding="utf-8")
        self.entry = self.root / ".agent" / "mcp" / "server_entry.py"
        self._env = {**os.environ, "AGENT_SCRIBE_GRAPHIFY_ROOT": str(self.root)}
        subprocess.run(["git", "init"], cwd=str(self.root), capture_output=True, env=self._env)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(self.root), capture_output=True, env=self._env)
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(self.root), capture_output=True, env=self._env)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def call(self, tool: str, **args: object) -> dict[str, object]:
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

    def register_and_before(self, intent: str = "read", resource: str = "README.md") -> dict[str, str]:
        reg = self.call("register_agent", host_tool="test", model_name="test")
        agent_id: str = reg["agent"]["agent_id"]
        bf = self.call("before_task", agent_id=agent_id, request=f"test {intent}", intent=intent, resource=resource)
        self.assertEqual(bf["verdict"], "BEFORE_TASK_OK", bf)
        return {"agent_id": agent_id, "task_id": bf["task_id"], "context_token": bf["context_token"]}

    def test_scribe_query_success_marks_scribe_done(self) -> None:
        _make_scribe_rag(self.root, returncode=0)
        ctx = self.register_and_before()
        sq = self.call("scribe_query", **ctx, query="test", limit=3)
        self.assertIs(sq.get("ok"), True, sq)
        self.assertIn("task_context", sq, sq)
        self.assertIs(sq["task_context"].get("scribe_done"), True, sq)

    def test_scribe_query_failure_does_not_mark_scribe_done(self) -> None:
        _make_scribe_rag(self.root, returncode=1)
        ctx = self.register_and_before()
        sq = self.call("scribe_query", **ctx, query="test", limit=3)
        self.assertIs(sq.get("ok"), False, sq)
        self.assertEqual(sq.get("verdict"), "SCRIBE_QUERY_FAILED", sq)
        self.assertIsNotNone(sq.get("result"), sq)
        self.assertEqual(sq["result"].get("returncode"), 1, sq)
        self.assertNotIn("task_context", sq, sq)

    def test_scribe_query_failure_workflow_next_asks_scribe_again(self) -> None:
        _make_scribe_rag(self.root, returncode=1)
        ctx = self.register_and_before()
        sq = self.call("scribe_query", **ctx, query="test", limit=3)
        self.assertIs(sq.get("ok"), False, sq)
        wn = self.call(
            "workflow_next", agent_id=ctx["agent_id"],
            request="test", intent="read", resource="README.md",
            last_verdict=sq.get("verdict", ""),
            task_id=ctx["task_id"], context_token=ctx["context_token"],
        )
        self.assertEqual(wn.get("verdict"), "NEXT_ACTION_REQUIRED", wn)
        self.assertEqual(wn.get("state"), "SCRIBE_CONTEXT_REQUIRED", wn)
        self.assertEqual(wn.get("must_call", {}).get("tool"), "scribe_query", wn)

    def test_scribe_query_failure_result_contains_stderr(self) -> None:
        _make_scribe_rag(self.root, returncode=1)
        ctx = self.register_and_before()
        sq = self.call("scribe_query", **ctx, query="test", limit=3)
        self.assertIs(sq.get("ok"), False, sq)
        res = sq.get("result", {})
        self.assertEqual(res.get("returncode"), 1, sq)
        self.assertIn("stderr", res, sq)

    def _init_memo(self) -> Path:
        memo = self.root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
        memo.write_text("baseline memory\n", encoding="utf-8")
        subprocess.run(["git", "add", "AGENT-MEMOIRE_PROJECT_STATUS.scribe"],
                       cwd=str(self.root), capture_output=True, env=self._env)
        subprocess.run(["git", "commit", "-m", "init memo"],
                       cwd=str(self.root), capture_output=True, env=self._env)
        return memo

    def test_scribe_query_success_returns_memory_hash(self) -> None:
        _make_scribe_rag(self.root, returncode=0)
        memo = self._init_memo()
        memo.write_text("test memory content\n", encoding="utf-8")
        ctx = self.register_and_before(intent="read")
        sq = self.call("scribe_query", **ctx, query="test", limit=3)
        self.assertIs(sq.get("ok"), True, sq)
        tc = sq.get("task_context", {})
        self.assertIn("memory_hash", tc, sq)
        self.assertIsNotNone(tc["memory_hash"], sq)

    def test_memory_hash_invalidation_blocks_write_claim(self) -> None:
        _make_scribe_rag(self.root, returncode=0, stdout="memory context for README.md write task")
        memo = self._init_memo()
        memo.write_text("original memory\n", encoding="utf-8")
        ctx = self.register_and_before(intent="write")
        sq = self.call("scribe_query", **ctx, query="README.md context test", limit=3)
        self.assertIs(sq.get("ok"), True, sq)
        tc = sq.get("task_context", {})
        self.assertIsNotNone(tc.get("memory_hash"), sq)
        memo.write_text("changed memory content\n", encoding="utf-8")
        claim = self.call("claim_resource", agent_id=ctx["agent_id"], resource="README.md",
                          mode="write", task_id=ctx["task_id"], context_token=ctx["context_token"])
        self.assertEqual(claim.get("verdict"), "CLAIM_CONTEXT_NOT_READY", claim)
        self.assertIn("memory changed since scribe_query", claim.get("reason", ""), claim)

    def test_memory_hash_invalidation_write_workflow_routes_to_scribe_query(self) -> None:
        _make_scribe_rag(self.root, returncode=0, stdout="memory context for README.md write task")
        memo = self.root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
        memo.write_text("original memory\n", encoding="utf-8")
        subprocess.run(["git", "add", "AGENT-MEMOIRE_PROJECT_STATUS.scribe"],
                       cwd=str(self.root), capture_output=True, env=self._env)
        subprocess.run(["git", "commit", "-m", "init memo"],
                       cwd=str(self.root), capture_output=True, env=self._env)
        ctx = self.register_and_before(intent="write")
        sq = self.call("scribe_query", **ctx, query="README.md context test", limit=3)
        self.assertIs(sq.get("ok"), True, sq)
        memo.write_text("changed memory\n", encoding="utf-8")
        wn = self.call("workflow_next", agent_id=ctx["agent_id"],
                       request="test write", intent="write", resource="README.md",
                       last_verdict="BEFORE_TASK_OK",
                       task_id=ctx["task_id"], context_token=ctx["context_token"])
        self.assertEqual(wn.get("state"), "SCRIBE_CONTEXT_REQUIRED", wn)
        self.assertEqual(wn.get("must_call", {}).get("tool"), "scribe_query", wn)

    def test_scribe_query_failure_returncode_2_with_stderr(self) -> None:
        _make_scribe_rag(self.root, returncode=2, stderr="scribe-rag: error: unrecognized arguments: --top 5")
        ctx = self.register_and_before()
        sq = self.call("scribe_query", **ctx, query="test", limit=5)
        self.assertIs(sq.get("ok"), False, sq)
        self.assertEqual(sq.get("verdict"), "SCRIBE_QUERY_FAILED", sq)
        self.assertEqual(sq["result"].get("returncode"), 2, sq)
        self.assertIn("scribe-rag: error: unrecognized arguments", sq["result"].get("stderr", ""), sq)
        self.assertNotIn("task_context", sq, sq)

    # ── Mini-tâche 3 : AGENT-MEMOIRE via MCP only ─────────────────────────────

    def _call(self, tool: str, **args: Any) -> dict[str, Any]:
        proc = subprocess.run(
            ["python3", str(self.entry), "--call", tool, "--args", json.dumps(args)],
            cwd=str(self.root),
            env=self._env,
            text=True,
            capture_output=True,
            timeout=TIMEOUT_MS / 1000,
        )
        raw_text = proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout)
        raw = json.loads(raw_text)
        if "content" in raw:
            return json.loads(raw["content"][0]["text"])
        return raw

    def _lease(self, ctx: dict[str, str], action: str, agent_id: str,
               resource: str = "README.md") -> str:
        p = self._call("pre_action_guard", agent_id=agent_id, intent="write",
                        resource=resource, planned_action=action,
                        task_id=ctx["task_id"], context_token=ctx["context_token"])
        if p.get("verdict") == "NEXT_ACTION_REQUIRED" and p.get("must_call", {}).get("tool") == "scribe_query":
            args = p["must_call"]["args"]
            sq = self._call("scribe_query", **args)
            self.assertEqual(sq.get("verdict"), "SCRIBE_QUERY_DONE", sq)
            p = self._call("pre_action_guard", agent_id=agent_id, intent="write",
                            resource=resource, planned_action=action,
                            task_id=ctx["task_id"], context_token=ctx["context_token"])
        self.assertEqual(p["verdict"], "PRE_ACTION_GUARD_OK", p)
        lid: str = p["action_lease"]["lease_id"]
        self.assertTrue(lid.startswith("lease-"), lid)
        return lid

    def _claim(self, ctx: dict[str, str], lease_id: str, agent_id: str,
               resource: str = "README.md") -> str:
        p = self._call("claim_resource", agent_id=agent_id, resource=resource,
                        mode="patch_queue", ttl_seconds=600,
                        action_lease_id=lease_id, task_id=ctx["task_id"],
                        context_token=ctx["context_token"])
        self.assertEqual(p["verdict"], "CLAIM_GRANTED", p)
        cid: str = p["claim_id"]
        return cid

    def _file_hash(self, resource: str = "README.md") -> str:
        p = self._call("file_hash", resource=resource)
        h: str = p["hash"]
        self.assertTrue(len(h) > 0, p)
        return h

    def _propose(self, ctx: dict[str, str], lease_id: str,
                 base_hash: str, agent_id: str,
                 resource: str = "README.md") -> str:
        p = self._call("propose_patch", agent_id=agent_id, target=resource,
                        base_hash=base_hash,
                        diff_text="@@ -1,1 +1,1 @@\n-line1\n+line2\n",
                        action_lease_id=lease_id, task_id=ctx["task_id"],
                        context_token=ctx["context_token"])
        self.assertEqual(p["status"], "PATCH_PROPOSED", p)
        pid: str = p["patch_id"]
        return pid

    def _apply(self, ctx: dict[str, str], lease_id: str,
               patch_id: str, agent_id: str,
               resource: str = "README.md") -> None:
        lock = self._call("resource_lock_claim", agent_id=agent_id,
                           resource=resource, task_id=ctx["task_id"],
                           context_token=ctx["context_token"], ttl_seconds=600)
        self.assertEqual(lock["verdict"], "RESOURCE_LOCK_ACQUIRED", lock)
        p = self._call("apply_patch", agent_id=agent_id, patch_id=patch_id,
                        action_lease_id=lease_id, task_id=ctx["task_id"],
                        context_token=ctx["context_token"])
        self.assertEqual(p["verdict"], "PATCH_APPLIED", p)
        self._call("resource_lock_release", agent_id=agent_id,
                    resource=resource, lock_id=lock.get("lock_id", ""))

    def _release_claim(self, claim_id: str, agent_id: str) -> None:
        self._call("release_claim", agent_id=agent_id, claim_id=claim_id)

    def _finish_with_lease(self, ctx: dict[str, str], lease_id: str,
                           agent_id: str) -> dict[str, Any]:
        return self._call("finish_task", agent_id=agent_id,
                          task_id=ctx["task_id"], context_token=ctx["context_token"],
                          action_lease_id=lease_id)

    def _graphify_query(self, ctx: dict[str, str], agent_id: str) -> None:
        p = self._call("graphify_query", agent_id=agent_id,
                        task_id=ctx["task_id"], context_token=ctx["context_token"],
                        query="test", resource="README.md")
        self.assertIn(p.get("verdict", ""),
                      {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}, p)

    # ── Test 1: direct mémoire edit detected by tripwire ──────────────────────

    def test_memoire_tripwire_detects_direct_edit(self) -> None:
        memo = self._init_memo()
        memo.write_text("revised memory\n", encoding="utf-8")
        ctx = self.register_and_before(intent="write")
        sq = self.call("scribe_query", **ctx, query="README.md context", limit=3)
        self.assertIs(sq.get("ok"), True, sq)
        memo.write_text("unauthorized revision\n", encoding="utf-8")
        tw = direct_fs_tripwire.detect_unauthorized_mutations(
            self.root, ctx["task_id"], ctx["agent_id"], resource="AGENT-MEMOIRE_PROJECT_STATUS.scribe",
        )
        suspects = tw.get("suspects", [])
        self.assertTrue(any(s["path"] == "AGENT-MEMOIRE_PROJECT_STATUS.scribe" for s in suspects), tw)
        self.assertIs(tw.get("verdict"), direct_fs_tripwire.DIRECT_WRITE_BYPASS_DETECTED, tw)

    # ── Test 2: finish_task blocks when patch_ids absent from mémoire ─────────

    def test_finish_task_blocks_when_memory_missing_patch_id(self) -> None:
        MEMOIRE = "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
        _make_scribe_rag(self.root, returncode=0, stdout="memory context for AGENT-MEMOIRE_PROJECT_STATUS.scribe write task")
        memo = self._init_memo()
        memo.write_text(FILE_CONTENT, encoding="utf-8")
        import hashlib
        memo_after_hash = "sha256:" + hashlib.sha256(memo.read_bytes()).hexdigest()
        ctx = self.register_and_before(intent="write", resource=MEMOIRE)
        direct_fs_tripwire.record_authorized_mutation(
            task_id=ctx["task_id"], agent_id=ctx["agent_id"],
            resource=MEMOIRE, tool="scribe_record", project_root=self.root,
            after_hash=memo_after_hash,
        )
        sq = self.call("scribe_query", **ctx, query="AGENT-MEMOIRE_PROJECT_STATUS.scribe context", limit=3)
        self.assertIs(sq.get("ok"), True, sq)
        self._graphify_query(ctx, ctx["agent_id"])
        aid = ctx["agent_id"]
        lease_claim = self._lease(ctx, "claim_resource", aid, resource=MEMOIRE)
        cid = self._claim(ctx, lease_claim, aid, resource=MEMOIRE)
        base = self._file_hash(resource=MEMOIRE)
        lease_propose = self._lease(ctx, "propose_patch", aid, resource=MEMOIRE)
        pid = self._propose(ctx, lease_propose, base, aid, resource=MEMOIRE)
        lease_apply = self._lease(ctx, "apply_patch", aid, resource=MEMOIRE)
        self._apply(ctx, lease_apply, pid, aid, resource=MEMOIRE)
        self._release_claim(cid, aid)
        lease_finish = self._lease(ctx, "finish_task", aid, resource=MEMOIRE)
        result = self._finish_with_lease(ctx, lease_finish, aid)
        self.assertEqual(result.get("verdict"), "MEMORY_PROOF_REQUIRED", result)
        self.assertEqual(result.get("state"), "MEMORY_PROOF_REQUIRED", result)
        self.assertIn("applied_patch_ids", result, result)

    # ── Test 3: finish_task succeeds when patch_id appears in mémoire ─────────

    def test_finish_task_accepts_when_patch_id_in_memory(self) -> None:
        MEMOIRE = "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
        _make_scribe_rag(self.root, returncode=0, stdout="memory context for AGENT-MEMOIRE_PROJECT_STATUS.scribe write task")
        memo = self._init_memo()
        memo.write_text(FILE_CONTENT, encoding="utf-8")
        import hashlib
        memo_after_hash = "sha256:" + hashlib.sha256(memo.read_bytes()).hexdigest()
        ctx = self.register_and_before(intent="write", resource=MEMOIRE)
        direct_fs_tripwire.record_authorized_mutation(
            task_id=ctx["task_id"], agent_id=ctx["agent_id"],
            resource=MEMOIRE, tool="scribe_record", project_root=self.root,
            after_hash=memo_after_hash,
        )
        sq = self.call("scribe_query", **ctx, query="AGENT-MEMOIRE_PROJECT_STATUS.scribe context", limit=3)
        self.assertIs(sq.get("ok"), True, sq)
        self._graphify_query(ctx, ctx["agent_id"])
        aid = ctx["agent_id"]
        lease_claim = self._lease(ctx, "claim_resource", aid, resource=MEMOIRE)
        cid = self._claim(ctx, lease_claim, aid, resource=MEMOIRE)
        base = self._file_hash(resource=MEMOIRE)
        lease_propose = self._lease(ctx, "propose_patch", aid, resource=MEMOIRE)
        pid = self._propose(ctx, lease_propose, base, aid, resource=MEMOIRE)
        lease_apply = self._lease(ctx, "apply_patch", aid, resource=MEMOIRE)
        self._apply(ctx, lease_apply, pid, aid, resource=MEMOIRE)
        self._release_claim(cid, aid)
        memo.write_text(f"memory referencing patch {pid}\n", encoding="utf-8")
        memo_after_hash2 = "sha256:" + hashlib.sha256(memo.read_bytes()).hexdigest()
        direct_fs_tripwire.record_authorized_mutation(
            task_id=ctx["task_id"], agent_id=aid,
            resource=MEMOIRE, tool="scribe_record", project_root=self.root,
            after_hash=memo_after_hash2,
        )
        sq2 = self.call("scribe_query", **ctx, query="test", limit=3)
        self.assertIs(sq2.get("ok"), True, sq2)
        from runtime import canonical_memory_gate as _cmg
        from runtime import db as _cdb
        current_bytes = memo.read_bytes() if memo.exists() else b""
        current_hex = hashlib.sha256(current_bytes).hexdigest()
        with _cdb.connect(self.root) as _con:
            _con.execute(
                "UPDATE canonical_memory_gate_v1 SET baseline_hash=? WHERE task_id=? AND agent_id=?",
                (current_hex, ctx["task_id"], aid),
            )
        lease_finish = self._lease(ctx, "finish_task", aid, resource=MEMOIRE)
        result = self._finish_with_lease(ctx, lease_finish, aid)
        if result.get("verdict") == "CANONICAL_MEMORY_REQUIRED":
            lease_finish = self._lease(ctx, "finish_task", aid, resource=MEMOIRE)
            result = self.call("finish_task", agent_id=aid,
                               task_id=ctx["task_id"], context_token=ctx["context_token"],
                               action_lease_id=lease_finish,
                               canonical_memory_skip_reason="Test environment: canonical memory reflects patch_id; skip promotion for test-only flow.")
        self.assertNotEqual(result.get("verdict"), "MEMORY_PROOF_REQUIRED", result)
        self.assertNotEqual(result.get("verdict"), "DIRECT_WRITE_BYPASS_DETECTED", result)
        self.assertNotEqual(result.get("verdict"), "FINISH_REFUSED_PENDING_PATCHES", result)
        self.assertNotEqual(result.get("verdict"), "FINISH_REFUSED_ACTIVE_CLAIMS", result)
        self.assertIn(result.get("verdict"), {"SCRIBE_COMMIT_GATE_REQUIRED", "TASK_FINISHED_OK", "CLEANUP_MEMORY_DECISION_REQUIRED", "CANONICAL_MEMORY_SKIPPED_WITH_REASON"}, result)

    # ── B2: irrelevant (non-empty, off-topic) scribe must not allow write ──────

    def test_irrelevant_scribe_blocks_write(self) -> None:
        UNRELATED = ("=== MEMORY: database_connection_pool ===\n"
                     "File: src/database/pool.py\n"
                     "Type: connection_pool\n"
                     "Active connections: 12\n"
                     "Max connections: 50\n"
                     "Pool strategy: lazy\n"
                     "Timeout: 30s\n"
                     "This file manages PostgreSQL connection pooling.\n")
        _make_scribe_rag(self.root, returncode=0, stdout=UNRELATED)
        ctx = self.register_and_before(intent="write", resource="README.md")
        aid = ctx["agent_id"]

        # scribe_query with off-topic content and query that doesn't reference target
        sq = self.call("scribe_query", **ctx, query="edit database pool config", limit=3)
        self.assertIs(sq.get("ok"), False, sq)
        self.assertEqual(sq.get("verdict"), "SCRIBE_CONTEXT_IRRELEVANT_FOR_WRITE", sq)
        self.assertNotIn("task_context", sq, sq)

        # graphify_query should still be callable
        gq = self.call("graphify_query", agent_id=aid,
                        task_id=ctx["task_id"], context_token=ctx["context_token"],
                        query="edit database pool", resource="README.md")
        self.assertIn(gq.get("verdict", ""), {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}, gq)

        # claim_resource should fail — scribe not done
        claim = self.call("claim_resource", agent_id=aid, resource="README.md",
                          mode="patch_queue", ttl_seconds=600,
                          task_id=ctx["task_id"], context_token=ctx["context_token"])
        self.assertIn(claim.get("verdict", ""), {"CLAIM_CONTEXT_NOT_READY", "TASK_CONTEXT_NOT_READY"}, claim)

        # propose_patch should fail — context not ready (scribe not done)
        base = self.call("file_hash", resource="README.md")["hash"]
        prop = self.call("propose_patch", agent_id=aid, target="README.md",
                          base_hash=base,
                          diff_text="@@ -1,3 +1,3 @@\n line1\n-line2\n+modified\n line3\n",
                          task_id=ctx["task_id"], context_token=ctx["context_token"])
        self.assertIs(prop.get("ok"), False, prop)
        error = prop.get("error", "") or prop.get("verdict", "")
        self.assertIn("TASK_CONTEXT_NOT_READY", error, prop)

        # apply_patch should not succeed (scribe not done, so write path blocked)
        ap = self.call("apply_patch", agent_id=aid, patch_id="nonexistent",
                        task_id=ctx["task_id"], context_token=ctx["context_token"])
        self.assertIs(ap.get("ok"), False, ap)
        self.assertNotEqual(ap.get("verdict"), "PATCH_APPLIED", ap)

        # file should be unchanged
        self.assertEqual((self.root / "README.md").read_text(), "test project\n")

    # ── B2.1: scope gate bypass tests ────────────────────────────────────────

    def test_global_scope_without_reason_blocked(self) -> None:
        _make_scribe_rag(self.root, returncode=0, stdout="unrelated database content")
        ctx = self.register_and_before(intent="write", resource="README.md")
        sq = self.call("scribe_query", **ctx, query="project-wide context", limit=3)
        self.assertIs(sq.get("ok"), False, sq)
        self.assertEqual(sq.get("verdict"), "SCRIBE_CONTEXT_IRRELEVANT_FOR_WRITE", sq)

    def test_global_scope_with_reason_passes(self) -> None:
        _make_scribe_rag(self.root, returncode=0, stdout="project unrelated content")
        ctx = self.register_and_before(intent="write", resource="README.md")
        sq = self.call("scribe_query", **ctx, query="project-wide refactor because:global change", limit=3)
        self.assertIs(sq.get("ok"), True, sq)
        self.assertEqual(sq.get("verdict"), "SCRIBE_QUERY_DONE", sq)
        self.assertIn("task_context", sq, sq)
        self.assertIs(sq["task_context"].get("scribe_done"), True, sq)

    def test_generic_token_in_resource_does_not_validate_scope(self) -> None:
        _make_scribe_rag(self.root, returncode=0, stdout="unrelated database pool content")
        ctx = self.register_and_before(intent="write", resource="src/utils/helper.py")
        sq = self.call("scribe_query", **ctx, query="edit utility code", limit=3)
        self.assertIs(sq.get("ok"), False, sq)
        self.assertEqual(sq.get("verdict"), "SCRIBE_CONTEXT_IRRELEVANT_FOR_WRITE", sq)
