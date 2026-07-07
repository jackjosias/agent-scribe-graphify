from __future__ import annotations

import json
import os
import shutil
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

from runtime.state_paths import prepare_state_dirs

import server_ext as mcp
from runtime import direct_fs_tripwire


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
        os.chdir(self.root)
        graphify_out = prepare_state_dirs(self.root)["graphify_out"]
        graphify_out.mkdir(parents=True, exist_ok=True)
        for name, content in (("graph.json", "{\"nodes\":[],\"edges\":[]}"), ("GRAPH_REPORT.md", "# Tripwire Test Graph Report\n"), ("graph.html", "<html><body></body></html>\n")):
            (graphify_out / name).write_text(content, encoding="utf-8")
        mcp.server.ROOT = self.root.resolve()
        mcp.server.AGENT_DIR = self.root / ".agent"
        self.agent = call_tool("bootstrap", host_tool="tripwire-test", model_name="test", run_legacy_bootstrap=False)["agent"]["agent_id"]

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        mcp.server.ROOT = self.old_root
        mcp.server.AGENT_DIR = self.old_agent
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


if __name__ == "__main__":
    unittest.main()
