from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import server_ext as mcp
from runtime import (
    db,
    direct_fs_tripwire,
    graphify_readiness,
    tenor_changeset,
    tenor_jobs,
)
from _workspace_fixture import prepare_graphify_fixture


def call_tool(name: str, **args: Any) -> dict[str, Any]:
    result = mcp.handle({"jsonrpc": "2.0", "id": name, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return json.loads(result["result"]["content"][0]["text"])


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


def init_project(root: Path) -> None:
    (root / ".agent" / "state" / "runtime").mkdir(parents=True)
    (root / ".agent" / "state" / "outputs").mkdir(parents=True)
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    git(root, "init")
    git(root, "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "add", "README.md", "tracked.txt")
    git(root, "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-m", "init")


class DirectFsTripwireTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        init_project(self.root)
        self.old_cwd = Path.cwd()
        self.old_root = mcp.server.ROOT
        self.old_agent = mcp.server.AGENT_DIR
        self.old_root_env = os.environ.get("AGENT_SCRIBE_GRAPHIFY_ROOT")
        self.old_fixture_env = os.environ.get(graphify_readiness.FIXTURE_ENV)
        os.chdir(self.root)
        fixture_env = prepare_graphify_fixture(self.root)
        os.environ["AGENT_SCRIBE_GRAPHIFY_ROOT"] = fixture_env["AGENT_SCRIBE_GRAPHIFY_ROOT"]
        os.environ[graphify_readiness.FIXTURE_ENV] = fixture_env[graphify_readiness.FIXTURE_ENV]
        mcp.server.ROOT = self.root.resolve()
        mcp.server.AGENT_DIR = self.root / ".agent"
        self.agent = call_tool("bootstrap", host_tool="tripwire-test", model_name="test", run_legacy_bootstrap=False)["agent"]["agent_id"]

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        mcp.server.ROOT = self.old_root
        mcp.server.AGENT_DIR = self.old_agent
        if self.old_root_env is None:
            os.environ.pop("AGENT_SCRIBE_GRAPHIFY_ROOT", None)
        else:
            os.environ["AGENT_SCRIBE_GRAPHIFY_ROOT"] = self.old_root_env
        if self.old_fixture_env is None:
            os.environ.pop(graphify_readiness.FIXTURE_ENV, None)
        else:
            os.environ[graphify_readiness.FIXTURE_ENV] = self.old_fixture_env
        self.tmp.cleanup()

    def before(self, intent: str = "write", resource: str = "tracked.txt") -> dict[str, str]:
        before = call_tool("before_task", agent_id=self.agent, request=f"{intent} {resource}", intent=intent, resource=resource)
        self.assertEqual(before["verdict"], "BEFORE_TASK_OK", before)
        return {"task_id": before["task_id"], "context_token": before["context_token"]}

    def ready(self, intent: str = "write", resource: str = "tracked.txt") -> dict[str, str]:
        ctx = self.before(intent=intent, resource=resource)
        sq = call_tool("scribe_query", agent_id=self.agent, **ctx, query="tripwire", limit=5)
        self.assertIn(sq["verdict"], {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"})
        if intent != "read":
            gq = call_tool("graphify_query", agent_id=self.agent, **ctx, query="tripwire", resource=resource)
            self.assertIn(gq["verdict"], {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"})
        return ctx

    def lease(self, action: str, ctx: dict[str, str], resource: str = "tracked.txt") -> str:
        result = call_tool("pre_action_guard", agent_id=self.agent, planned_action=action, intent="write", resource=resource, **ctx)
        self.assertEqual(result["verdict"], "PRE_ACTION_GUARD_OK", result)
        return result["action_lease"]["lease_id"]

    def claim(self, ctx: dict[str, str], resource: str = "tracked.txt") -> str:
        result = call_tool("claim_resource", agent_id=self.agent, resource=resource, mode="patch_queue", ttl_seconds=600, action_lease_id=self.lease("claim_resource", ctx, resource), **ctx)
        self.assertEqual(result["verdict"], "CLAIM_GRANTED", result)
        return result["claim_id"]

    def release_claim(self, claim_id: str) -> None:
        result = call_tool("release_claim", agent_id=self.agent, claim_id=claim_id, summary="release")
        self.assertEqual(result["verdict"], "CLAIM_RELEASED", result)

    def apply_authorized_patch(self, ctx: dict[str, str], resource: str = "tracked.txt", replacement: str = "patched\n") -> None:
        self.claim(ctx, resource)
        lock = call_tool("resource_lock_claim", agent_id=self.agent, resource=resource, ttl_seconds=600, **ctx)
        self.assertEqual(lock["verdict"], "RESOURCE_LOCK_ACQUIRED", lock)
        fh = call_tool("file_hash", resource=resource)
        patch = call_tool(
            "propose_patch", agent_id=self.agent, target=resource, base_hash=fh["hash"],
            diff_text=f"@@ -1,1 +1,1 @@\n-base\n+{replacement.rstrip()}\n",
            action_lease_id=self.lease("propose_patch", ctx, resource), **ctx,
        )
        self.assertEqual(patch["status"], "PATCH_PROPOSED", patch)
        applied = call_tool("apply_patch", agent_id=self.agent, patch_id=patch["patch_id"], action_lease_id=self.lease("apply_patch", ctx, resource), **ctx)
        self.assertEqual(applied["verdict"], "PATCH_APPLIED", applied)

    def audit(self, ctx: dict[str, str], resource: str = "") -> dict[str, Any]:
        return call_tool("workspace_audit", agent_id=self.agent, task_id=ctx["task_id"], resource=resource)

    def test_01_before_task_mutating_creates_snapshot(self) -> None:
        ctx = self.before()
        result = direct_fs_tripwire.detect_unauthorized_mutations(self.root, ctx["task_id"], self.agent)
        self.assertEqual(result["verdict"], direct_fs_tripwire.TRIPWIRE_CLEAN)

    def test_02_before_task_read_only_does_not_create_snapshot(self) -> None:
        ctx = self.before(intent="read")
        result = direct_fs_tripwire.detect_unauthorized_mutations(self.root, ctx["task_id"], self.agent)
        self.assertEqual(result["verdict"], "DIRECT_FS_TRIPWIRE_NO_SNAPSHOT")

    def test_03_direct_write_tracked_detected(self) -> None:
        ctx = self.before()
        (self.root / "tracked.txt").write_text("direct\n", encoding="utf-8")
        result = self.audit(ctx)
        self.assertEqual(result["verdict"], "DIRECT_WRITE_BYPASS_DETECTED")
        self.assertEqual(result["suspects"][0]["path"], "tracked.txt")

    def test_04_direct_create_untracked_detected(self) -> None:
        ctx = self.before(resource="new.txt")
        (self.root / "new.txt").write_text("direct\n", encoding="utf-8")
        result = self.audit(ctx)
        self.assertEqual(result["verdict"], "DIRECT_WRITE_BYPASS_DETECTED")

    def test_05_direct_delete_detected(self) -> None:
        ctx = self.before()
        (self.root / "tracked.txt").unlink()
        result = self.audit(ctx)
        self.assertEqual(result["verdict"], "DIRECT_WRITE_BYPASS_DETECTED")

    def test_06_apply_patch_authorized_is_clean(self) -> None:
        ctx = self.ready()
        self.apply_authorized_patch(ctx)
        result = self.audit(ctx)
        self.assertEqual(result["verdict"], "WORKSPACE_AUDIT_OK", result)

    def test_07_delete_resource_authorized_is_clean(self) -> None:
        ctx = self.ready(intent="delete")
        self.claim(ctx)
        fh = call_tool("file_hash", resource="tracked.txt")
        deleted = call_tool("delete_resource", agent_id=self.agent, resource="tracked.txt", base_hash=fh["hash"], confirm_phrase="DELETE tracked.txt", reason="test", action_lease_id=self.lease("delete_resource", ctx), **ctx)
        self.assertEqual(deleted["verdict"], "RESOURCE_DELETED", deleted)
        result = self.audit(ctx)
        self.assertEqual(result["verdict"], "WORKSPACE_AUDIT_OK", result)

    def test_08_direct_modification_after_authorized_patch_detected(self) -> None:
        ctx = self.ready()
        self.apply_authorized_patch(ctx)
        (self.root / "tracked.txt").write_text("direct after patch\n", encoding="utf-8")
        result = self.audit(ctx)
        self.assertEqual(result["verdict"], "DIRECT_WRITE_BYPASS_DETECTED")

    def test_09_agent_state_runtime_ignored(self) -> None:
        ctx = self.before()
        path = self.root / ".agent" / "state" / "runtime" / "noise.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("noise\n", encoding="utf-8")
        self.assertEqual(self.audit(ctx)["verdict"], "WORKSPACE_AUDIT_OK")

    def test_10_agent_state_outputs_ignored(self) -> None:
        ctx = self.before()
        path = self.root / ".agent" / "state" / "outputs" / "noise.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("noise\n", encoding="utf-8")
        self.assertEqual(self.audit(ctx)["verdict"], "WORKSPACE_AUDIT_OK")

    def test_11_pytest_cache_ignored(self) -> None:
        ctx = self.before()
        path = self.root / ".pytest_cache" / "v" / "cache.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("cache\n", encoding="utf-8")
        self.assertEqual(self.audit(ctx)["verdict"], "WORKSPACE_AUDIT_OK")

    def test_12_pycache_ignored(self) -> None:
        ctx = self.before()
        path = self.root / "pkg" / "__pycache__" / "mod.pyc"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"cache")
        self.assertEqual(self.audit(ctx)["verdict"], "WORKSPACE_AUDIT_OK")

    def test_13_symlink_does_not_follow_target(self) -> None:
        ctx = self.before(resource="passwd-link")
        (self.root / "passwd-link").symlink_to("/etc/passwd")
        result = self.audit(ctx)
        self.assertEqual(result["verdict"], "DIRECT_WRITE_BYPASS_DETECTED")
        self.assertTrue(any(item["hash"].startswith("symlink:") for item in result["suspects"]))

    def test_14_snapshot_idempotent_for_same_task(self) -> None:
        ctx = self.before()
        first = direct_fs_tripwire.workspace_snapshot(self.root, ctx["task_id"], self.agent)
        self.assertEqual(first["verdict"], "DIRECT_FS_TRIPWIRE_SNAPSHOT_EXISTS")

    def test_15_finish_task_blocks_on_direct_write(self) -> None:
        ctx = self.ready()
        (self.root / "tracked.txt").write_text("direct\n", encoding="utf-8")
        result = call_tool("finish_task", agent_id=self.agent, summary="finish", action_lease_id=self.lease("finish_task", ctx), **ctx)
        self.assertEqual(result["verdict"], "DIRECT_WRITE_BYPASS_DETECTED")
        self.assertEqual((result["must_call"] or {})["tool"], "workspace_audit")

    def test_16_finish_task_continues_to_scribe_gate_for_authorized_mutation(self) -> None:
        ctx = self.ready()
        claim_id = self.claim(ctx)
        self.release_claim(claim_id)
        result = call_tool("finish_task", agent_id=self.agent, summary="finish", action_lease_id=self.lease("finish_task", ctx), **ctx)
        self.assertIn(result["verdict"], {"SCRIBE_COMMIT_GATE_REQUIRED", "TASK_FINISHED_OK"}, result)

    def test_17_workspace_audit_reports_suspect_paths(self) -> None:
        ctx = self.before()
        (self.root / "tracked.txt").write_text("direct\n", encoding="utf-8")
        result = self.audit(ctx)
        self.assertEqual([item["path"] for item in result["suspects"]], ["tracked.txt"])

    def test_18_opencode_hostile_direct_write_finish_blocked(self) -> None:
        ctx = self.ready(resource="tracked.txt")
        (self.root / "tracked.txt").write_text("hostile shell write\n", encoding="utf-8")
        result = call_tool("finish_task", agent_id=self.agent, summary="hostile", action_lease_id=self.lease("finish_task", ctx), **ctx)
        self.assertEqual(result["verdict"], "DIRECT_WRITE_BYPASS_DETECTED")

    def test_19_manual_cleanup_restores_clean_audit(self) -> None:
        ctx = self.before()
        (self.root / "tracked.txt").write_text("direct\n", encoding="utf-8")
        self.assertEqual(self.audit(ctx)["verdict"], "DIRECT_WRITE_BYPASS_DETECTED")
        (self.root / "tracked.txt").write_text("base\n", encoding="utf-8")
        self.assertEqual(self.audit(ctx)["verdict"], "WORKSPACE_AUDIT_OK")

    def test_20_no_regression_scribe_commit_gate_required(self) -> None:
        ctx = self.ready()
        claim_id = self.claim(ctx)
        self.release_claim(claim_id)
        result = call_tool("finish_task", agent_id=self.agent, summary="gate", action_lease_id=self.lease("finish_task", ctx), **ctx)
        self.assertNotEqual(result["verdict"], "DIRECT_WRITE_BYPASS_DETECTED")

    def test_21_preexisting_dirty_memoire_unchanged_is_not_new_bypass(self) -> None:
        memoire = self.root / direct_fs_tripwire.MEMOIRE_FILE
        memoire.write_text("preexisting dirty memory\n", encoding="utf-8")
        ctx = self.ready(resource="tracked.txt")
        self.apply_authorized_patch(ctx, resource="tracked.txt", replacement="patched tracked\n")
        result = self.audit(ctx)
        self.assertEqual(result["verdict"], "WORKSPACE_AUDIT_OK", result)
        self.assertFalse(any(item["path"] == direct_fs_tripwire.MEMOIRE_FILE for item in result.get("suspects", [])), result)

    def test_22_memoire_modified_after_snapshot_is_bypass(self) -> None:
        memoire = self.root / direct_fs_tripwire.MEMOIRE_FILE
        memoire.write_text("preexisting dirty memory\n", encoding="utf-8")
        ctx = self.ready(resource="README.md")
        memoire.write_text("preexisting dirty memory\nnew direct write\n", encoding="utf-8")
        result = self.audit(ctx)
        self.assertEqual(result["verdict"], "DIRECT_WRITE_BYPASS_DETECTED", result)
        self.assertTrue(any(item["path"] == direct_fs_tripwire.MEMOIRE_FILE for item in result.get("suspects", [])), result)

    def test_23_other_task_receipt_cannot_authorize_current_direct_write(self) -> None:
        current = self.before(resource="tracked.txt")
        spoofed = b"spoofed by another task\n"
        direct_fs_tripwire.record_authorized_mutation(
            "task-prior",
            self.agent,
            "tracked.txt",
            "tenor_apply_changeset",
            after_hash=hashlib.sha256(spoofed).hexdigest(),
            project_root=self.root,
        )
        (self.root / "tracked.txt").write_bytes(spoofed)
        result = self.audit(current)
        self.assertEqual(result["verdict"], "DIRECT_WRITE_BYPASS_DETECTED", result)
        self.assertEqual(result["authorized_mutations"], [], result)

    def test_24_direct_delete_is_detected_without_git(self) -> None:
        shutil.rmtree(self.root / ".git")
        current = self.before(resource="tracked.txt")
        (self.root / "tracked.txt").unlink()
        result = self.audit(current)
        self.assertEqual(result["verdict"], "DIRECT_WRITE_BYPASS_DETECTED", result)
        self.assertEqual(result["suspects"][0]["path"], "tracked.txt", result)
        self.assertEqual(result["suspects"][0]["status"], "ABSENT", result)

    def test_25_active_tenor_write_requires_exact_live_sql_proof(self) -> None:
        current = self.before(resource="tracked.txt")
        tenor_changeset.ensure_schema(self.root)
        submitted = tenor_jobs.submit_job(
            self.root,
            kind="changeset",
            agent_id="other-agent",
            task_id="other-task",
            request_id="active-fenced-write",
            payload={"changes": []},
            max_runtime_seconds=60,
            auto_launch=False,
        )
        claimed = tenor_jobs.claim_job(self.root, str(submitted["job_id"]))
        fence = claimed["worker_fence"]
        changeset_id = "cs-active-fenced-write"
        before = hashlib.sha256(b"base\n").hexdigest()
        after = hashlib.sha256(b"tenor concurrent\n").hexdigest()
        now = int(time.time())
        with db.connect(self.root) as con:
            con.execute(
                f"""
                INSERT INTO {tenor_changeset.TRANSACTION_TABLE}(
                  changeset_id,request_id,request_fingerprint,task_id,agent_id,
                  owner_pid,execution_job_id,worker_instance_id,fence_token,
                  status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    changeset_id,
                    "active-write-request",
                    "active-write-fingerprint",
                    "other-task",
                    "other-agent",
                    999_999_999,
                    submitted["job_id"],
                    fence["worker_instance_id"],
                    fence["fence_token"],
                    "applying",
                    now,
                    now,
                ),
            )
            con.execute(
                f"""
                INSERT INTO {tenor_changeset.FILE_TABLE}(
                  changeset_id,ordinal,resource,operation,base_hash,new_hash,
                  backup_path,staged_path,applied
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    changeset_id,
                    0,
                    "tracked.txt",
                    "replace",
                    before,
                    after,
                    "",
                    "",
                    -1,
                ),
            )
            current_time = time.time()
            con.execute(
                f"""
                INSERT INTO {tenor_changeset.LOCK_TABLE}(
                  lock_id,resource,agent_id,task_id,changeset_id,
                  execution_job_id,worker_instance_id,fence_token,
                  mode,created_at,expires_at,heartbeat_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "lock-active-fenced-write",
                    "tracked.txt",
                    "other-agent",
                    "other-task",
                    changeset_id,
                    submitted["job_id"],
                    fence["worker_instance_id"],
                    fence["fence_token"],
                    "exclusive",
                    current_time,
                    current_time + 600,
                    current_time,
                ),
            )
        (self.root / "tracked.txt").write_text(
            "tenor concurrent\n",
            encoding="utf-8",
        )
        attested = self.audit(current)
        self.assertEqual(attested["verdict"], "WORKSPACE_AUDIT_OK", attested)
        self.assertTrue(
            any(
                item["tool"] == "tenor_active_fenced_write"
                for item in attested["authorized_mutations"]
            ),
            attested,
        )

        with db.connect(self.root) as con:
            con.execute(
                f"UPDATE {tenor_jobs.JOB_TABLE} SET lease_expires_at=? WHERE job_id=?",
                (int(time.time()) - 1, submitted["job_id"]),
            )
        rejected = self.audit(current)
        self.assertEqual(
            rejected["verdict"],
            direct_fs_tripwire.DIRECT_WRITE_BYPASS_DETECTED,
            rejected,
        )

    def test_26_canonical_proof_survives_context_flag_window(self) -> None:
        shutil.copy2(
            Path(__file__).resolve().parents[3]
            / direct_fs_tripwire.MEMOIRE_FILE,
            self.root / direct_fs_tripwire.MEMOIRE_FILE,
        )
        current = self.ready(resource="tracked.txt")
        record = call_tool(
            "scribe_record",
            agent_id=self.agent,
            request="tripwire canonical proof",
            summary="TRIPWIRE_CANONICAL_PROOF_WINDOW",
            touched_resources=["tracked.txt"],
            verdict="PASS",
            record_type="validation",
            task_id=current["task_id"],
            context_token=current["context_token"],
        )
        record_path = self.root / record["record_path"]
        payload = json.loads(record_path.read_text(encoding="utf-8"))
        from runtime import canonical_memory_gate

        promoted = canonical_memory_gate.promote_record(
            self.root,
            payload,
            record_path,
            scope="tracked.txt",
            memory_policy="canonical_required",
            agent_id=self.agent,
            task_id=current["task_id"],
        )
        self.assertTrue(promoted["ok"], promoted)
        with db.connect(self.root) as con:
            con.execute(
                """
                UPDATE task_context_v2
                SET scribe_record_promoted=0,scribe_record_entry_id=NULL
                WHERE task_id=? AND agent_id=?
                """,
                (current["task_id"], self.agent),
            )
        result = self.audit(current)
        self.assertEqual(result["verdict"], "WORKSPACE_AUDIT_OK", result)
        self.assertTrue(
            any(
                item["patch_id"] == promoted["entry_id"]
                and item["tool"] == "scribe_promote_record"
                for item in result["authorized_mutations"]
            ),
            result,
        )


if __name__ == "__main__":
    unittest.main()
