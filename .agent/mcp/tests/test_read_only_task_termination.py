from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class ReadOnlyTaskTerminationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        shutil.copytree(ROOT / ".agent" / "mcp", self.root / ".agent" / "mcp")
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True, exist_ok=True)
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

    def register(self, agent_id: str = "read-test-agent") -> dict[str, object]:
        return self.call("register_agent", agent_id=agent_id, host_tool="test", model_name="unit")

    def test_full_read_only_workflow_terminates_cleanly(self) -> None:
        """Reproduit le scénario terrain : read task → finish → READY_FOR_NEXT_TASK."""
        agent_id = "read-test-agent"

        # 1. Register agent (simule TENOR INIT sans créer de tâche)
        reg = self.register(agent_id)
        self.assertEqual(reg.get("verdict"), "AGENT_REGISTERED")

        # 2. before_task (read) — crée le contexte de tâche
        bt = self.call("before_task", agent_id=agent_id, request="dis moi où on s est arrêté", intent="read", resource=".")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        context_token = bt["context_token"]

        # 3. scribe_query
        sq = self.call("scribe_query", agent_id=agent_id, task_id=task_id, context_token=context_token, query="progress", limit=1)
        self.assertIn(sq.get("verdict"), {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"})

        # 4. workflow_next après scribe — ne doit pas retourner CLAIM_REQUIRED
        wn1 = self.call("workflow_next", agent_id=agent_id, task_id=task_id, context_token=context_token, last_verdict="SCRIBE_QUERY_DONE")
        self.assertNotEqual(wn1.get("verdict"), "CLAIM_REQUIRED", f"unexpected claim after scribe: {wn1}")

        # 5. scribe_record (optionnel, lit)
        sr = self.call("scribe_record", agent_id=agent_id, summary="read-only check", verdict="ok")
        self.assertEqual(sr.get("verdict"), "SCRIBE_RECORD_STAGED_ONLY", sr)
        self.assertFalse(sr.get("canonical_memory_updated"), sr)
        self.assertTrue(sr.get("record_json_created"), sr)

        # 6. finish_task avec intent="read" — pas de lease requis
        ft = self.call("finish_task", agent_id=agent_id, task_id=task_id, context_token=context_token, intent="read", summary="read-only done")
        self.assertEqual(ft.get("verdict"), "TASK_FINISHED_OK", f"finish failed: {ft}")
        self.assertIn("task_context", ft, "task_context missing from finish response")
        tc = ft["task_context"]
        self.assertEqual(tc.get("status"), "finished")

        # 7. workflow_next avec TASK_FINISHED_OK — doit retourner READY_FOR_NEXT_TASK
        wn2 = self.call("workflow_next", agent_id=agent_id, last_verdict="TASK_FINISHED_OK")
        self.assertEqual(wn2.get("verdict"), "READY_FOR_NEXT_TASK",
                         f"expected READY_FOR_NEXT_TASK, got {wn2.get('verdict')}: {wn2}")
        self.assertEqual(wn2.get("state"), "READY_FOR_NEXT_TASK")

        # 8. Vérifier l'absence de boucle : un deuxième workflow_next reste terminal
        wn3 = self.call("workflow_next", agent_id=agent_id, last_verdict="TASK_FINISHED_OK")
        self.assertEqual(wn3.get("verdict"), "READY_FOR_NEXT_TASK",
                         f"second workflow_next should also be terminal, got {wn3.get('verdict')}")

    def test_write_task_still_requires_action_lease(self) -> None:
        """Write task finish sans lease renvoie ACTION_LEASE_REQUIRED."""
        agent_id = "write-lease-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="edit tracked", intent="write", resource="README.md")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        context_token = bt["context_token"]

        ft = self.call("finish_task", agent_id=agent_id, task_id=task_id, context_token=context_token, intent="write")
        self.assertEqual(ft.get("verdict"), "ACTION_LEASE_REQUIRED",
                         f"write finish without lease should fail, got {ft.get('verdict')}")

    def test_malicious_read_intent_on_write_task_requires_lease(self) -> None:
        """Host ment : déclare intent='read' mais le contexte stocké dit 'write' → lease requis."""
        agent_id = "malicious-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="edit tracked", intent="write", resource="README.md")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        context_token = bt["context_token"]

        ft = self.call("finish_task", agent_id=agent_id, task_id=task_id, context_token=context_token, intent="read")
        self.assertEqual(ft.get("verdict"), "ACTION_LEASE_REQUIRED",
                         f"malicious read intent on write task should require lease, got {ft.get('verdict')}")

    def test_no_claim_after_read_finish(self) -> None:
        """Après finish read, workflow_next ne lance jamais CLAIM_REQUIRED."""
        agent_id = "no-claim-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="list files", intent="read", resource=".")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        context_token = bt["context_token"]

        ft = self.call("finish_task", agent_id=agent_id, task_id=task_id, context_token=context_token, intent="read")
        self.assertEqual(ft.get("verdict"), "TASK_FINISHED_OK")

        for i in range(3):
            wn = self.call("workflow_next", agent_id=agent_id, last_verdict="TASK_FINISHED_OK")
            self.assertNotEqual(wn.get("verdict"), "CLAIM_REQUIRED",
                                f"iteration {i}: unexpected CLAIM_REQUIRED: {wn}")
            self.assertEqual(wn.get("verdict"), "READY_FOR_NEXT_TASK",
                             f"iteration {i}: expected READY_FOR_NEXT_TASK, got {wn.get('verdict')}")

    def test_no_loop_via_resume_task_context_after_finish(self) -> None:
        """Aucun resume_task_context après un finish propre — pas de boucle."""
        agent_id = "no-loop-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="show status", intent="read", resource=".")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        context_token = bt["context_token"]

        ft = self.call("finish_task", agent_id=agent_id, task_id=task_id, context_token=context_token, intent="read")
        self.assertEqual(ft.get("verdict"), "TASK_FINISHED_OK")

        rtc = self.call("resume_task_context", agent_id=agent_id, task_id=task_id)
        self.assertFalse(rtc.get("ok"), f"resume after finish should fail, got {rtc}")
        self.assertIn("TASK_CONTEXT_EXPIRED", rtc.get("error", ""),
                      f"resume after finish should complain about expired/finished context, got {rtc}")

    def test_finish_task_unknown_task_id_does_not_fallback_to_declared_read(self) -> None:
        """task_id inexistant + intent=read → TASK_CONTEXT_UNKNOWN_TASK, pas TASK_FINISHED_OK."""
        agent_id = "unknown-task-agent"
        self.register(agent_id)

        ft = self.call("finish_task", agent_id=agent_id, task_id="nonexistent-task-42", context_token="ignored", intent="read")
        self.assertFalse(ft.get("ok"), f"should fail, got {ft}")
        self.assertEqual(ft.get("verdict"), "TASK_CONTEXT_UNKNOWN_TASK",
                         f"expected TASK_CONTEXT_UNKNOWN_TASK, got {ft.get('verdict')}")
        self.assertEqual(ft.get("state"), "HARD_STOP")

    def test_finish_task_agent_mismatch_does_not_fallback_to_declared_read(self) -> None:
        """Agent A crée tâche, Agent B finish avec task_id A + intent=read → TASK_AGENT_MISMATCH."""
        agent_a = "mismatch-agent-a"
        agent_b = "mismatch-agent-b"
        self.register(agent_a)
        self.register(agent_b)

        bt = self.call("before_task", agent_id=agent_a, request="edit tracked", intent="write", resource="README.md")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        context_token = bt["context_token"]

        ft = self.call("finish_task", agent_id=agent_b, task_id=task_id, context_token=context_token, intent="read")
        self.assertFalse(ft.get("ok"), f"should fail, got {ft}")
        self.assertEqual(ft.get("verdict"), "TASK_AGENT_MISMATCH",
                         f"expected TASK_AGENT_MISMATCH, got {ft.get('verdict')}")
        self.assertEqual(ft.get("state"), "HARD_STOP")

    # ── V2.15.17: read-only FSM purity ─────────────────────────

    def test_read_workflow_next_never_requires_claim(self) -> None:
        """workflow_next across the read lifecycle never returns CLAIM_REQUIRED."""
        agent_id = "fsm-purity-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="check project", intent="read", resource=".")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        ctx = bt["context_token"]

        wn0 = self.call("workflow_next", agent_id=agent_id, request="check project", intent="read",
                         task_id=task_id, context_token=ctx, last_verdict="BEFORE_TASK_OK")
        v0 = wn0.get("verdict", "")
        self.assertNotIn(v0, ("CLAIM_REQUIRED", "ACTION_LEASE_REQUIRED", "PATCH_QUEUE_REQUIRED",
                               "PRE_ACTION_GUARD_REQUIRED", "APPLY_PATCH_REQUIRED"),
                         f"workflow_next after before_task returned write verdict: {wn0}")

    def test_read_task_forbids_patch_queue_claim(self) -> None:
        """claim_resource(mode=patch_queue) on a read task returns READ_ONLY_CLAIM_FORBIDDEN."""
        agent_id = "no-patch-claim-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="read only", intent="read", resource=".")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        ctx = bt["context_token"]

        claim = self.call("claim_resource", agent_id=agent_id, resource="README.md",
                          mode="patch_queue", ttl_seconds=600,
                          task_id=task_id, context_token=ctx)
        self.assertFalse(claim.get("ok"), f"claim should be forbidden, got {claim}")
        self.assertEqual(claim.get("verdict"), "READ_ONLY_CLAIM_FORBIDDEN",
                         f"expected READ_ONLY_CLAIM_FORBIDDEN, got {claim.get('verdict')}")
        self.assertEqual(claim.get("state"), "HARD_STOP")

    def test_read_task_allows_no_claim_finish(self) -> None:
        """Read task finishes cleanly without needing any claim."""
        agent_id = "no-claim-finish-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="read project", intent="read", resource=".")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        ctx = bt["context_token"]

        sq = self.call("scribe_query", agent_id=agent_id, task_id=task_id, context_token=ctx,
                        query="project status", limit=1)
        self.assertIn(sq.get("verdict"), {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"})

        ft = self.call("finish_task", agent_id=agent_id, task_id=task_id, context_token=ctx,
                        intent="read", summary="read-only check")
        self.assertEqual(ft.get("verdict"), "TASK_FINISHED_OK",
                         f"finish should succeed without claim, got {ft}")
        self.assertTrue(ft.get("terminal", False),
                        "finish_task should include terminal=True hint")
        self.assertEqual(ft.get("next_state_hint"), "READY_FOR_NEXT_TASK",
                         "finish_task should include next_state_hint")

    def test_read_task_does_not_reenter_after_finish(self) -> None:
        """Multiple workflow_next calls after read finish stay terminal."""
        agent_id = "terminal-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="read", intent="read", resource=".")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        ctx = bt["context_token"]

        ft = self.call("finish_task", agent_id=agent_id, task_id=task_id, context_token=ctx, intent="read")
        self.assertEqual(ft.get("verdict"), "TASK_FINISHED_OK")

        for i in range(3):
            wn = self.call("workflow_next", agent_id=agent_id, last_verdict="TASK_FINISHED_OK")
            self.assertEqual(wn.get("verdict"), "READY_FOR_NEXT_TASK",
                             f"iteration {i}: expected READY_FOR_NEXT_TASK, got {wn.get('verdict')}: {wn}")
            self.assertNotEqual(wn.get("verdict"), "CLAIM_REQUIRED",
                                f"iteration {i}: unexpected CLAIM_REQUIRED")
            self.assertNotEqual(wn.get("must_call", {}).get("tool"), "resume_task_context",
                                f"iteration {i}: unexpected resume_task_context after finish")

    def test_read_task_patch_queue_claim_field_regression(self) -> None:
        """workflow_next during read task never recommends claim_resource/propose_patch."""
        agent_id = "regression-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="read project", intent="read", resource=".")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        ctx = bt["context_token"]

        sq = self.call("scribe_query", agent_id=agent_id, task_id=task_id, context_token=ctx,
                        query="status", limit=1)
        self.assertIn(sq.get("verdict"), {"SCRIBE_QUERY_DONE", "SCRIBE_UNAVAILABLE"})

        wn = self.call("workflow_next", agent_id=agent_id, request="read project", intent="read",
                        task_id=task_id, context_token=ctx, last_verdict="SCRIBE_QUERY_DONE")
        must_tool = wn.get("must_call", {}).get("tool", "")
        self.assertNotIn(must_tool, ("claim_resource", "propose_patch", "apply_patch", "delete_resource"),
                         f"workflow_next after scribe on read task returned write tool: {wn}")

    def test_read_only_claim_mode_read_is_not_required(self) -> None:
        """claim_resource(mode=read) on read task is allowed but never required by workflow_next."""
        agent_id = "mode-read-agent"
        self.register(agent_id)

        bt = self.call("before_task", agent_id=agent_id, request="read", intent="read", resource=".")
        self.assertEqual(bt.get("verdict"), "BEFORE_TASK_OK")
        task_id = bt["task_id"]
        ctx = bt["context_token"]

        for _ in range(5):
            wn = self.call("workflow_next", agent_id=agent_id, request="read", intent="read",
                            task_id=task_id, context_token=ctx, last_verdict="BEFORE_TASK_OK")
            must_tool = wn.get("must_call", {}).get("tool", "")
            self.assertNotEqual(must_tool, "claim_resource",
                                f"workflow_next should not require claim_resource on read task: {wn}")


if __name__ == "__main__":
    unittest.main()
