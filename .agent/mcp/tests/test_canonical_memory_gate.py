from __future__ import annotations

import importlib
import json
import os
from datetime import date
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
ROOT = Path(__file__).resolve().parents[3]
SCRIBE_RAG = ROOT / ".agent" / "workflow" / "scribe" / "scribe-rag"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import server_ext as mcp
from runtime import canonical_memory_gate, db, discipline, patch_queue, direct_fs_tripwire, scribe_commit_gate, task_context
import scribe_store as _scribe_store
import scribe_doctor_model as _scribe_doctor_model

RESOURCE = "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
AGENT_A = "memory-agent-a"


def call_tool(name: str, **args: Any) -> dict[str, Any]:
    result = mcp.handle({"jsonrpc": "2.0", "id": name, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return json.loads(result["result"]["content"][0]["text"])


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


class CanonicalMemoryGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True)
        (self.root / ".agent" / "state" / "outputs").mkdir(parents=True)
        graphify_dir = self.root / "graphify-out"
        graphify_dir.mkdir(parents=True)
        (graphify_dir / "graph.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        (graphify_dir / "GRAPH_REPORT.md").write_text("# Canonical Memory Gate Test Graph\n", encoding="utf-8")
        (graphify_dir / "graph.html").write_text("<html><body></body></html>\n", encoding="utf-8")
        shutil.copy2(ROOT / "AGENT-MEMOIRE_PROJECT_STATUS.scribe", self.root / RESOURCE)
        (self.root / "README.md").write_text("readme\n", encoding="utf-8")
        (self.root / "tracked.txt").write_text("base\n", encoding="utf-8")
        git(self.root, "init")
        git(self.root, "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "add", "AGENT-MEMOIRE_PROJECT_STATUS.scribe", "README.md", "tracked.txt")
        git(self.root, "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-m", "init")
        self.old_cwd = Path.cwd()
        self.old_root = mcp.server.ROOT
        self.old_agent = mcp.server.AGENT_DIR
        os.chdir(self.root)
        mcp.server.ROOT = self.root.resolve()
        mcp.server.AGENT_DIR = self.root / ".agent"
        importlib.reload(db)
        importlib.reload(patch_queue)
        importlib.reload(task_context)
        importlib.reload(discipline)
        importlib.reload(direct_fs_tripwire)
        importlib.reload(canonical_memory_gate)
        importlib.reload(scribe_commit_gate)
        importlib.reload(_scribe_doctor_model)
        importlib.reload(_scribe_doctor_model)
        importlib.reload(_scribe_store)
        mcp.db = db
        mcp.patch_queue = patch_queue
        mcp.task_context = task_context
        mcp.discipline = discipline
        mcp.direct_fs_tripwire = direct_fs_tripwire
        mcp.canonical_memory_gate = canonical_memory_gate
        mcp.scribe_commit_gate = scribe_commit_gate
        db.init_db(self.root)
        discipline.ensure_schema()
        scribe_commit_gate.ensure_schema()
        canonical_memory_gate._ensure_schema(self.root)
        self.agent = call_tool("bootstrap", host_tool="memory-test", model_name="test", run_legacy_bootstrap=False)["agent"]["agent_id"]

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        mcp.server.ROOT = self.old_root
        mcp.server.AGENT_DIR = self.old_agent
        self.tmp.cleanup()

    def register(self, agent_id: str = AGENT_A) -> None:
        result = call_tool("register_agent", agent_id=agent_id, host_tool="test", model_name="unit")
        self.assertEqual((result.get("agent") or {}).get("status"), "active", result)

    def ready_context(self, intent: str = "write", resource: str = RESOURCE) -> dict[str, str]:
        self.register()
        before = call_tool("before_task", agent_id=AGENT_A, request=f"{intent} {resource}", intent=intent, resource=resource)
        self.assertEqual(before.get("verdict"), "BEFORE_TASK_OK", before)
        ctx = {"task_id": before["task_id"], "context_token": before["context_token"]}
        sq = call_tool("scribe_query", agent_id=AGENT_A, **ctx, query=f"{intent} {resource}", limit=1)
        self.assertIn(sq.get("verdict"), {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}, sq)
        if intent != "read":
            gq = call_tool("graphify_query", agent_id=AGENT_A, **ctx, query="impact", resource=resource)
            self.assertIn(gq.get("verdict"), {"GRAPHIFY_QUERY_DONE", "GRAPHIFY_UNAVAILABLE"}, gq)
        return ctx

    def lease(self, ctx: dict[str, str], action: str = "finish_task", intent: str = "write") -> str:
        result = call_tool("pre_action_guard", agent_id=AGENT_A, task_id=ctx["task_id"], context_token=ctx["context_token"], resource=RESOURCE, intent=intent, planned_action=action)
        if result.get("verdict") == "NEXT_ACTION_REQUIRED" and result.get("must_call", {}).get("tool") == "scribe_query":
            args = result["must_call"]["args"]
            sq = call_tool("scribe_query", **args)
            self.assertIn(sq.get("verdict"), {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}, sq)
            result = call_tool("pre_action_guard", agent_id=AGENT_A, task_id=ctx["task_id"], context_token=ctx["context_token"], resource=RESOURCE, intent=intent, planned_action=action)
        self.assertEqual(result.get("verdict"), "PRE_ACTION_GUARD_OK", result)
        return result["action_lease"]["lease_id"]

    def claim(self, ctx: dict[str, str], resource: str = RESOURCE) -> str:
        lease_id = self.lease(ctx, "claim_resource")
        result = call_tool("claim_resource", agent_id=AGENT_A, resource=resource, mode="patch_queue", ttl_seconds=600, action_lease_id=lease_id, **ctx)
        self.assertEqual(result.get("verdict"), "CLAIM_GRANTED", result)
        return result["claim_id"]

    def release_claim(self, claim_id: str) -> None:
        result = call_tool("release_claim", agent_id=AGENT_A, claim_id=claim_id, summary="release")
        self.assertEqual(result.get("verdict"), "CLAIM_RELEASED", result)

    def inject_yaml_date_entry(self, entry_id: str = "JOURNAL-DATE-REGRESSION") -> None:
        memo_file = self.root / RESOURCE
        original = memo_file.read_text(encoding="utf-8")
        marker = "\nmetrics:\n"
        entry = f"""
  - id: "{entry_id}"
    date: 2026-07-08
    mode: "STANDARD"
    agent_type: "cli"
    surface: "yaml-date-regression"
    hot_entries_consulted: ["INV-F002"]
    l0_abstract: "YAML_DATE_REGRESSION"
    pourquoi: "Regression fixture for unquoted YAML date parsing."
    scribe_delta: "{entry_id}"
"""
        self.assertIn(marker, original)
        memo_file.write_text(original.replace(marker, f"{entry}{marker}", 1), encoding="utf-8")

    def apply_authorized_scribe_patch(self, ctx: dict[str, str], token: str) -> None:
        claim_id = self.claim(ctx)
        lock = call_tool("resource_lock_claim", agent_id=AGENT_A, resource=RESOURCE, ttl_seconds=600, **ctx)
        self.assertEqual(lock.get("verdict"), "RESOURCE_LOCK_ACQUIRED", lock)
        original = (self.root / RESOURCE).read_text(encoding="utf-8")
        marker = "\nmetrics:\n"
        entry = f"""
  - id: "JOURNAL-TEST-{token}"
    date: "2026-06-30"
    mode: "STANDARD"
    agent_type: "cli"
    surface: "canonical-memory"
    hot_entries_consulted: ["INV-F002", "PAT-GIT-001"]
    l0_abstract: "CANONICAL_PROMOTION_{token}"
    pourquoi: "Canonical memory gate test entry for {token}."
    scribe_delta: "JOURNAL-TEST-{token}"
"""
        self.assertIn(marker, original)
        updated = original.replace(marker, f"{entry}{marker}", 1)
        diff_text = "".join(
            subprocess.run(
                [sys.executable, "-c", "from difflib import unified_diff; import sys; old=open(sys.argv[1],encoding='utf-8').read().splitlines(True); new=open(sys.argv[2],encoding='utf-8').read().splitlines(True); sys.stdout.write(''.join(unified_diff(old,new,fromfile=sys.argv[1],tofile=sys.argv[2])))", str(self.root / RESOURCE), str(self.root / RESOURCE)],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        # The previous subprocess only shells a diff helper; replace the temporary file contents explicitly.
        scratch = self.root / ".agent" / "state" / "runtime" / f"scribe-{token}.tmp"
        scratch.write_text(updated, encoding="utf-8")
        diff_text = subprocess.run(
            [
                sys.executable,
                "-c",
                "from difflib import unified_diff; import sys; old=open(sys.argv[1],encoding='utf-8').read().splitlines(True); new=open(sys.argv[2],encoding='utf-8').read().splitlines(True); sys.stdout.write(''.join(unified_diff(old,new,fromfile=sys.argv[1],tofile=sys.argv[2])))",
                str(self.root / RESOURCE),
                str(scratch),
            ],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        base_hash = call_tool("file_hash", resource=RESOURCE)["hash"]
        proposed = call_tool(
            "propose_patch",
            agent_id=AGENT_A,
            target=RESOURCE,
            base_hash=base_hash,
            diff_text=diff_text,
            action_lease_id=self.lease(ctx, "propose_patch"),
            **ctx,
        )
        self.assertEqual(proposed.get("status"), "PATCH_PROPOSED", proposed)
        applied = call_tool(
            "apply_patch",
            agent_id=AGENT_A,
            patch_id=proposed["patch_id"],
            action_lease_id=self.lease(ctx, "apply_patch"),
            **ctx,
        )
        self.assertEqual(applied.get("verdict"), "PATCH_APPLIED", applied)
        self.release_claim(claim_id)
        scratch.unlink(missing_ok=True)
        patch_id = proposed["patch_id"]
        memo_file = self.root / RESOURCE
        memo_content = memo_file.read_text(encoding="utf-8")
        memo_file.write_text(memo_content + f"\n# applied-patch-ref:{patch_id}\n", encoding="utf-8")
        import hashlib
        memo_hash = "sha256:" + hashlib.sha256(memo_file.read_bytes()).hexdigest()
        direct_fs_tripwire.record_authorized_mutation(
            task_id=ctx.get("task_id", ""), agent_id=AGENT_A,
            resource=RESOURCE, tool="scribe_patch_proof",
            project_root=self.root,
            after_hash=memo_hash,
        )
        sq = call_tool("scribe_query", agent_id=AGENT_A, **ctx,
                       query=f"patch proof {token}", limit=3)
        self.assertIn(sq.get("verdict"), {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}, sq)

    def finish(self, ctx: dict[str, str], intent: str = "write", summary: str = "finish", skip_reason: str = "") -> dict[str, Any]:
        lease_id = self.lease(ctx, "finish_task", intent=intent)
        return call_tool("finish_task", agent_id=AGENT_A, summary=summary, intent=intent, canonical_memory_skip_reason=skip_reason, action_lease_id=lease_id, **ctx)

    def test_01_read_only_finish_not_blocked(self) -> None:
        ctx = self.ready_context(intent="read")
        result = call_tool("finish_task", agent_id=AGENT_A, summary="read done", intent="read", **ctx)
        self.assertEqual(result.get("verdict"), "TASK_FINISHED_OK", result)
        self.assertTrue(result.get("terminal"), result)

    def test_02_mutating_scribe_record_only_requires_canonical_memory(self) -> None:
        ctx = self.ready_context()
        record = call_tool(
            "scribe_record",
            agent_id=AGENT_A,
            request="mutating task validation",
            summary="validation result requires promotion",
            touched_resources=[RESOURCE],
            verdict="ok",
            record_type="validation",
        )
        self.assertEqual(record.get("verdict"), "SCRIBE_RECORD_STAGED_ONLY", record)
        self.assertTrue(record.get("record_json_created"), record)
        self.assertFalse(record.get("canonical_memory_updated"), record)
        self.assertTrue(record.get("canonical_memory_required"), record)
        self.assertEqual(record.get("promotion_tool"), "scribe_promote_record", record)
        result = self.finish(ctx, summary="mutating finish")
        self.assertEqual(result.get("verdict"), "CANONICAL_MEMORY_REQUIRED", result)

    def test_03_mutating_promoted_memory_passes(self) -> None:
        ctx = self.ready_context()
        token = "PROMOTE_20260630"
        self.apply_authorized_scribe_patch(ctx, token)
        result = self.finish(ctx, summary="promoted memory finish")
        self.assertEqual(result.get("verdict"), "CANONICAL_MEMORY_PROMOTED", result)
        self.assertTrue(result.get("terminal"), result)
        self.assertEqual(result.get("next_state_hint"), "READY_FOR_NEXT_TASK")

    def test_04_mutating_skip_reason_serious_passes(self) -> None:
        ctx = self.ready_context()
        result = self.finish(ctx, summary="cleanup task", skip_reason="The canonical memory is already captured elsewhere in this run; the next agent only needs the runtime receipt for this no-op cleanup.")
        self.assertEqual(result.get("verdict"), "CANONICAL_MEMORY_SKIPPED_WITH_REASON", result)
        self.assertTrue(result.get("terminal"), result)

    def test_05_mutating_generic_skip_reason_rejected(self) -> None:
        ctx = self.ready_context()
        result = self.finish(ctx, summary="cleanup task", skip_reason="minor")
        self.assertEqual(result.get("verdict"), "CANONICAL_MEMORY_SKIP_REJECTED", result)

    def test_06_bug_fix_requires_promotion(self) -> None:
        ctx = self.ready_context(intent="fix")
        result = self.finish(ctx, intent="fix", summary="bug fix task")
        self.assertEqual(result.get("verdict"), "CANONICAL_MEMORY_REQUIRED", result)

    def test_07_new_feature_requires_promotion(self) -> None:
        ctx = self.ready_context()
        result = self.finish(ctx, summary="new feature delivery")
        self.assertEqual(result.get("verdict"), "CANONICAL_MEMORY_REQUIRED", result)

    def test_08_ui_ux_decision_requires_promotion(self) -> None:
        ctx = self.ready_context()
        result = self.finish(ctx, summary="ui ux decision")
        self.assertEqual(result.get("verdict"), "CANONICAL_MEMORY_REQUIRED", result)

    def test_09_feedback_correction_requires_promotion(self) -> None:
        ctx = self.ready_context()
        result = self.finish(ctx, summary="correction after feedback from the user")
        self.assertEqual(result.get("verdict"), "CANONICAL_MEMORY_REQUIRED", result)

    def test_10_scribe_rag_context_finds_canonical_entry(self) -> None:
        ctx = self.ready_context()
        token = "CONTEXT_20260630"
        self.apply_authorized_scribe_patch(ctx, token)

        store = _scribe_store.load_scribe(self.root / RESOURCE)
        entry = store.by_id(f"JOURNAL-TEST-{token}")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.value.get("l0_abstract"), f"CANONICAL_PROMOTION_{token}")
        self.assertIn(f"JOURNAL-TEST-{token}", store.search(f"CANONICAL_PROMOTION_{token}", limit=5)[0][1].entity.id if store.search(f"CANONICAL_PROMOTION_{token}", limit=5) else "")
        proc = subprocess.run([str(SCRIBE_RAG), "context", "--module", "JOURNAL"], cwd=str(self.root), text=True, capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("JOURNAL-003", proc.stdout)
        result = self.finish(ctx, summary="retrieval proof finish")
        self.assertEqual(result.get("verdict"), "CANONICAL_MEMORY_PROMOTED", result)
        self.assertEqual(result.get("retrieval_ok"), True, result)

    def test_11_direct_fs_tripwire_runs_before_canonical_gate(self) -> None:
        ctx = self.ready_context()
        (self.root / RESOURCE).write_text((self.root / RESOURCE).read_text(encoding="utf-8") + "\n# direct host edit\n", encoding="utf-8")
        result = self.finish(ctx, summary="direct host edit")
        self.assertEqual(result.get("verdict"), direct_fs_tripwire.DIRECT_WRITE_BYPASS_DETECTED, result)

    def test_12_direct_scribe_write_outside_mcp_is_blocked_before_memory_gate(self) -> None:
        ctx = self.ready_context()
        (self.root / RESOURCE).write_text((self.root / RESOURCE).read_text(encoding="utf-8") + "\n# illegal write\n", encoding="utf-8")
        result = self.finish(ctx, summary="illegal memory write")
        self.assertEqual(result.get("verdict"), direct_fs_tripwire.DIRECT_WRITE_BYPASS_DETECTED, result)

    def test_13_scribe_record_alone_is_not_canonical_memory(self) -> None:
        ctx = self.ready_context(intent="read")
        call_tool(
            "scribe_record",
            agent_id=AGENT_A,
            request="local only check",
            summary="runtime receipt only",
            touched_resources=[RESOURCE],
            verdict="ok",
            memory_policy="local_only",
        )
        result = call_tool("finish_task", agent_id=AGENT_A, task_id=ctx["task_id"], context_token=ctx["context_token"], intent="read", summary="runtime receipt only")
        self.assertEqual(result.get("verdict"), "TASK_FINISHED_OK", result)

    def test_14_promote_record_is_idempotent(self) -> None:
        ctx = self.ready_context()
        record = call_tool(
            "scribe_record",
            agent_id=AGENT_A,
            request="read-only validation run",
            summary="read-only validation result",
            touched_resources=[RESOURCE],
            verdict="PASS",
            record_type="validation",
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
        )
        self.assertEqual(record.get("verdict"), "SCRIBE_RECORD_STAGED_ONLY", record)
        promoted = call_tool(
            "scribe_promote_record",
            agent_id=AGENT_A,
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
            record_path=record["record_path"],
        )
        self.assertEqual(promoted.get("verdict"), "CANONICAL_MEMORY_PROMOTED", promoted)
        self.assertTrue(promoted.get("canonical_memory_updated"), promoted)
        duplicate = call_tool(
            "scribe_promote_record",
            agent_id=AGENT_A,
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
            record_path=record["record_path"],
        )
        self.assertEqual(duplicate.get("verdict"), "CANONICAL_MEMORY_ALREADY_PROMOTED", duplicate)
        self.assertTrue(duplicate.get("already_promoted"), duplicate)

    def test_15_readonly_validation_cannot_finish_with_durable_record_only(self) -> None:
        ctx = self.ready_context(intent="read")
        record = call_tool(
            "scribe_record",
            agent_id=AGENT_A,
            request="read-only validation run",
            summary="read-only validation result",
            touched_resources=[RESOURCE],
            verdict="PASS",
            record_type="validation",
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
        )
        self.assertEqual(record.get("verdict"), "SCRIBE_RECORD_STAGED_ONLY", record)
        result = call_tool("finish_task", agent_id=AGENT_A, task_id=ctx["task_id"], context_token=ctx["context_token"], intent="read", summary="read-only validation result")
        self.assertEqual(result.get("verdict"), "CANONICAL_MEMORY_REQUIRED", result)

    def test_16_readonly_validation_can_finish_after_promotion(self) -> None:
        ctx = self.ready_context(intent="read")
        record = call_tool(
            "scribe_record",
            agent_id=AGENT_A,
            request="read-only validation run",
            summary="read-only validation result",
            touched_resources=[RESOURCE],
            verdict="PASS",
            record_type="validation",
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
        )
        promoted = call_tool(
            "scribe_promote_record",
            agent_id=AGENT_A,
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
            record_path=record["record_path"],
        )
        self.assertEqual(promoted.get("verdict"), "CANONICAL_MEMORY_PROMOTED", promoted)
        result = call_tool("finish_task", agent_id=AGENT_A, task_id=ctx["task_id"], context_token=ctx["context_token"], intent="read", summary="read-only validation result")
        self.assertEqual(result.get("verdict"), "TASK_FINISHED_OK", result)

    def test_17_local_only_record_can_finish_without_canonical_promotion(self) -> None:
        ctx = self.ready_context(intent="read")
        record = call_tool(
            "scribe_record",
            agent_id=AGENT_A,
            request="read-only runtime note",
            summary="runtime receipt only",
            touched_resources=[RESOURCE],
            verdict="INFO",
            memory_policy="local_only",
        )
        self.assertEqual(record.get("verdict"), "SCRIBE_RECORD_STAGED_ONLY", record)
        result = call_tool("finish_task", agent_id=AGENT_A, task_id=ctx["task_id"], context_token=ctx["context_token"], intent="read", summary="runtime receipt only")
        self.assertEqual(result.get("verdict"), "TASK_FINISHED_OK", result)

    def test_18_promoted_validation_is_findable_by_scribe_query(self) -> None:
        ctx = self.ready_context()
        token = "PROMOTED_QUERY_20260630"
        record = call_tool(
            "scribe_record",
            agent_id=AGENT_A,
            request=f"validation {token}",
            summary=f"validation summary {token}",
            touched_resources=[RESOURCE],
            verdict="PASS",
            record_type="validation",
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
        )
        promoted = call_tool(
            "scribe_promote_record",
            agent_id=AGENT_A,
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
            record_path=record["record_path"],
        )
        self.assertEqual(promoted.get("verdict"), "CANONICAL_MEMORY_PROMOTED", promoted)
        query = call_tool("scribe_query", agent_id=AGENT_A, task_id=ctx["task_id"], context_token=ctx["context_token"], query=token, limit=5)
        self.assertIn(query.get("verdict"), {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"}, query)
        if query.get("verdict") == "SCRIBE_QUERY_DONE":
            stdout = str((query.get("result") or {}).get("stdout") or "")
            self.assertIn(token, stdout, query)
        else:
            self.assertIn("scribe-rag not found", str(query.get("reason") or ""), query)

    def test_20_entity_signature_accepts_yaml_date_values(self) -> None:
        entity = type(
            "FakeEntity",
            (),
            {
                "id": "ENTITY-DATE-001",
                "collection": "journal",
                "path": "AGENT-MEMOIRE_PROJECT_STATUS.scribe",
                "value": {"date": date(2026, 7, 8), "summary": "yaml date regression"},
            },
        )()
        sig1 = canonical_memory_gate._entity_signature(entity)
        sig2 = canonical_memory_gate._entity_signature(entity)
        self.assertTrue(sig1)
        self.assertEqual(sig1, sig2)

    def test_21_before_task_write_handles_yaml_date_value(self) -> None:
        self.inject_yaml_date_entry()
        self.register()
        result = call_tool("before_task", agent_id=AGENT_A, request="edit tracked", intent="write", resource=RESOURCE)
        self.assertEqual(result.get("verdict"), "BEFORE_TASK_OK", result)
        self.assertIn("task_id", result)
        self.assertIn("context_token", result)

    def test_19_canonical_delta_present_when_memory_changes(self) -> None:
        ctx = self.ready_context()
        token = "DELTA_20260630"
        self.apply_authorized_scribe_patch(ctx, token)
        result = self.finish(ctx, summary="delta proof")
        self.assertEqual(result.get("verdict"), "CANONICAL_MEMORY_PROMOTED", result)
        self.assertNotEqual(result.get("scribe_delta"), "Aucun", result)
        self.assertIn(f"JOURNAL-TEST-{token}", str(result.get("scribe_delta") or ""))
        self.assertEqual(result.get("terminal"), True)

    def test_22_yaml_valid_after_canonical_promotion(self) -> None:
        import yaml
        ctx = self.ready_context(intent="read")
        record = call_tool(
            "scribe_record",
            agent_id=AGENT_A,
            request="canonical yaml format check",
            summary="YAML_CANONICAL_FORMAT_PROBE",
            touched_resources=[RESOURCE],
            verdict="PASS",
            record_type="validation",
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
        )
        promoted = call_tool(
            "scribe_promote_record",
            agent_id=AGENT_A,
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
            record_path=record["record_path"],
        )
        self.assertEqual(promoted.get("verdict"), "CANONICAL_MEMORY_PROMOTED", promoted)
        text = (self.root / RESOURCE).read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        self.assertIsNotNone(data)
        self.assertIn("canonical", data)
        self.assertIsInstance(data["canonical"], list)
        self.assertTrue(len(data["canonical"]) > 0)
        last_canon = data["canonical"][-1]
        self.assertIn("id", last_canon)
        self.assertTrue(str(last_canon["id"]).startswith("CANON-"))

    def test_23_canonical_entry_findable_via_scribe_store(self) -> None:
        ctx = self.ready_context(intent="read")
        canon_summary = "FINDABLE_CANON_ENTRY_PROBE_20260709"
        record = call_tool(
            "scribe_record",
            agent_id=AGENT_A,
            request="findable check",
            summary=canon_summary,
            touched_resources=[RESOURCE],
            verdict="PASS",
            record_type="validation",
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
        )
        promoted = call_tool(
            "scribe_promote_record",
            agent_id=AGENT_A,
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
            record_path=record["record_path"],
        )
        self.assertEqual(promoted.get("verdict"), "CANONICAL_MEMORY_PROMOTED", promoted)
        store = _scribe_store.load_scribe(self.root / RESOURCE)
        entry = store.by_id(promoted["entry_id"])
        self.assertIsNotNone(entry, f"entry {promoted['entry_id']} not found by scribe store")
        entry_value = getattr(entry, "value", {}) or {}
        combined = " ".join(str(v) for v in entry_value.values())
        self.assertIn(canon_summary, combined)

    def test_24_canonical_promotion_write_finish_pipeline(self) -> None:
        ctx = self.ready_context()
        record = call_tool(
            "scribe_record",
            agent_id=AGENT_A,
            request="canonical write finish pipeline",
            summary="CANONICAL_WRITE_FINISH_PIPELINE_PROBE",
            touched_resources=[RESOURCE],
            verdict="PASS",
            record_type="validation",
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
        )
        promoted = call_tool(
            "scribe_promote_record",
            agent_id=AGENT_A,
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
            record_path=record["record_path"],
        )
        self.assertEqual(promoted.get("verdict"), "CANONICAL_MEMORY_PROMOTED", promoted)
        result = self.finish(ctx, summary="canonical write finish pipeline")
        self.assertIn(result.get("verdict"), ("CANONICAL_MEMORY_PROMOTED", "TASK_FINISHED_OK"), result)

    def test_25_scribe_rag_entities_preserved_after_rebuild(self) -> None:
        subprocess.run([str(SCRIBE_RAG), "build"], cwd=str(self.root), text=True, capture_output=True, timeout=30)
        ctx = self.ready_context(intent="read")
        record = call_tool(
            "scribe_record",
            agent_id=AGENT_A,
            request="scribe rag rebuild test",
            summary="SCRIBE_RAG_ENTITIES_PRESERVED_PROBE",
            touched_resources=[RESOURCE],
            verdict="PASS",
            record_type="validation",
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
        )
        promoted = call_tool(
            "scribe_promote_record",
            agent_id=AGENT_A,
            task_id=ctx["task_id"],
            context_token=ctx["context_token"],
            record_path=record["record_path"],
        )
        self.assertEqual(promoted.get("verdict"), "CANONICAL_MEMORY_PROMOTED", promoted)
        cp = subprocess.run([str(SCRIBE_RAG), "build"], cwd=str(self.root), text=True, capture_output=True, timeout=30)
        self.assertEqual(cp.returncode, 0, f"scribe-rag build failed: {cp.stderr}")
        bc = subprocess.run([str(SCRIBE_RAG), "context"], cwd=str(self.root), text=True, capture_output=True, timeout=30)
        self.assertEqual(bc.returncode, 0, f"scribe-rag context failed: {bc.stderr}")
        self.assertNotIn("entities: 0", bc.stdout)
        self.assertIn("entities:", bc.stdout)


if __name__ == "__main__":
    unittest.main()
