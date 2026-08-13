from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import server_ext as mcp
from runtime import db, task_context, tenor_changeset, tenor_decision, tenor_jobs, tenor_public_api
from _strict_cleanup import remove_tree_strict


class TenorTaskReclaimTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "feature.txt").write_text("before\n", encoding="utf-8")
        self.old_cwd = Path.cwd()
        self.old_root = mcp.server.ROOT
        self.old_bound = getattr(mcp.server, "_MCP_BOUND_AGENT_ID", "")
        os.chdir(self.root)
        mcp.server.ROOT = self.root
        db.init_db(self.root)
        db.register_agent("codex-cli", "test", "owner")
        db.register_agent("codex-cli", "test", "new-owner")
        db.register_agent("codex-cli", "test", "racer")
        mcp.server._MCP_BOUND_AGENT_ID = "new-owner"
        task_context.ensure_schema()
        tenor_public_api.ensure_schema(self.root)
        tenor_changeset.ensure_schema(self.root)
        tenor_jobs.ensure_schema(self.root)

    def tearDown(self) -> None:
        tenor_public_api._TASK_TOKENS.clear()
        mcp.server._MCP_BOUND_AGENT_ID = self.old_bound
        mcp.server.ROOT = self.old_root
        os.chdir(self.old_cwd)
        remove_tree_strict(self.tmp.name)
        self.tmp.cleanup()

    def call(self, **arguments: object) -> dict[str, object]:
        response = mcp.handle({
            "jsonrpc": "2.0",
            "id": "reclaim",
            "method": "tools/call",
            "params": {"name": "tenor_task_control", "arguments": arguments},
        })
        return json.loads(response["result"]["content"][0]["text"])

    def task(self, *, status: str = "active", changeset_id: str = "") -> tuple[str, str]:
        created = task_context.create_task_context(
            agent_id="owner",
            request="orphan task",
            intent="write",
            resource="src/feature.txt",
            requires_graphify=False,
            ttl_seconds=900,
        )
        task_id = str(created["task_id"])
        tenor_public_api._write_activity(
            task_id,
            "owner",
            "orphan task",
            "write",
            "src/feature.txt",
            ["src/feature.txt"],
            status=status,
            current_action="ready",
            last_action="graphify_query",
            next_action="tenor_apply_changeset",
        )
        if changeset_id:
            tenor_public_api._advance(task_id, last_changeset_id=changeset_id)
        return task_id, str(created["context_token"])

    def mark_owner(self, *, pid: int, last_seen: int) -> None:
        with db.connect(self.root) as con:
            con.execute(
                "UPDATE agents SET pid=?,last_seen=?,status='active' WHERE agent_id='owner'",
                (pid, last_seen),
            )

    def test_live_owner_is_rejected(self) -> None:
        task_id, _ = self.task()
        self.mark_owner(pid=os.getpid(), last_seen=int(time.time()))
        result = self.call(task_id=task_id, action="reclaim", expected_owner_agent_id="owner")
        self.assertEqual(result["verdict"], "TENOR_TASK_OWNER_ALIVE")

    def test_dead_owner_is_reclaimed_and_task_id_is_preserved(self) -> None:
        task_id, _ = self.task()
        self.mark_owner(pid=2_000_000_000, last_seen=int(time.time()) - 3600)
        result = self.call(task_id=task_id, action="reclaim", expected_owner_agent_id="owner")
        self.assertEqual(result["verdict"], "TENOR_TASK_RECLAIMED")
        self.assertEqual(result["task_id"], task_id)
        self.assertEqual(task_context.task_status(task_id)["agent_id"], "new-owner")
        self.assertEqual(tenor_public_api._activity_row(task_id)["agent_id"], "new-owner")

    def test_reclaim_is_idempotent_for_new_owner(self) -> None:
        task_id, _ = self.task()
        self.mark_owner(pid=2_000_000_000, last_seen=int(time.time()) - 3600)
        first = self.call(task_id=task_id, action="reclaim", expected_owner_agent_id="owner")
        second = self.call(task_id=task_id, action="reclaim", expected_owner_agent_id="owner")
        self.assertEqual(first["verdict"], "TENOR_TASK_RECLAIMED")
        self.assertEqual(second["verdict"], "TENOR_TASK_ALREADY_OWNED")

    def test_concurrent_reclaim_has_one_winner(self) -> None:
        task_id, _ = self.task()
        self.mark_owner(pid=2_000_000_000, last_seen=int(time.time()) - 3600)
        barrier = threading.Barrier(2)
        results: list[dict[str, object]] = []

        def reclaim(agent_id: str) -> None:
            barrier.wait()
            results.append(tenor_public_api._reclaim_task(
                agent_id=agent_id,
                task_id=task_id,
                expected_owner_agent_id="owner",
            ))

        threads = [
            threading.Thread(target=reclaim, args=("new-owner",)),
            threading.Thread(target=reclaim, args=("racer",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        verdicts = sorted(str(result["verdict"]) for result in results)
        self.assertEqual(verdicts, ["TENOR_TASK_OWNER_CHANGED", "TENOR_TASK_RECLAIMED"])

    def test_wrong_expected_owner_is_rejected(self) -> None:
        task_id, _ = self.task()
        self.mark_owner(pid=2_000_000_000, last_seen=int(time.time()) - 3600)
        result = self.call(task_id=task_id, action="reclaim", expected_owner_agent_id="other-owner")
        self.assertEqual(result["verdict"], "TENOR_TASK_OWNER_CHANGED")

    def test_terminal_task_is_rejected(self) -> None:
        task_id, _ = self.task(status="finished")
        self.mark_owner(pid=2_000_000_000, last_seen=int(time.time()) - 3600)
        result = self.call(task_id=task_id, action="reclaim", expected_owner_agent_id="owner")
        self.assertEqual(result["verdict"], "TENOR_TASK_TERMINAL")

    def test_recent_dead_heartbeat_is_still_forbidden(self) -> None:
        task_id, _ = self.task()
        self.mark_owner(pid=2_000_000_000, last_seen=int(time.time()))
        result = self.call(task_id=task_id, action="reclaim", expected_owner_agent_id="owner")
        self.assertEqual(result["verdict"], "TENOR_TASK_RECLAIM_FORBIDDEN")

    def test_old_context_token_is_invalid_after_reclaim(self) -> None:
        task_id, old_token = self.task()
        self.mark_owner(pid=2_000_000_000, last_seen=int(time.time()) - 3600)
        self.call(task_id=task_id, action="reclaim", expected_owner_agent_id="owner")
        with self.assertRaises(task_context.TaskContextError) as error:
            task_context.verify_active_context("owner", task_id, old_token)
        self.assertEqual(error.exception.code, "TASK_CONTEXT_AGENT_MISMATCH")

    def test_changeset_reference_is_preserved(self) -> None:
        task_id, _ = self.task(changeset_id="changeset-1")
        self.mark_owner(pid=2_000_000_000, last_seen=int(time.time()) - 3600)
        result = self.call(task_id=task_id, action="reclaim", expected_owner_agent_id="owner")
        self.assertEqual(result["verdict"], "TENOR_TASK_RECLAIMED")
        self.assertEqual(tenor_public_api._activity_row(task_id)["last_changeset_id"], "changeset-1")

    def test_active_publication_blocks_reclaim(self) -> None:
        task_id, _ = self.task()
        self.mark_owner(pid=2_000_000_000, last_seen=int(time.time()) - 3600)
        with db.connect(self.root) as con:
            con.execute(
                f"""
                INSERT INTO {tenor_changeset.TRANSACTION_TABLE}(
                    changeset_id,request_id,request_fingerprint,task_id,agent_id,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                ("cs-1", "req-1", "fp", task_id, "owner", "applying", int(time.time()), int(time.time())),
            )
        result = self.call(task_id=task_id, action="reclaim", expected_owner_agent_id="owner")
        self.assertEqual(result["verdict"], "TENOR_TASK_ACTIVE_PUBLICATION")

    def test_audit_event_is_emitted(self) -> None:
        task_id, _ = self.task()
        self.mark_owner(pid=2_000_000_000, last_seen=int(time.time()) - 3600)
        self.call(task_id=task_id, action="reclaim", expected_owner_agent_id="owner")
        with db.connect(self.root) as con:
            event = con.execute(
                "SELECT type,payload FROM events WHERE type='tenor.task_reclaimed' ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(event)
        self.assertIn(task_id, str(event["payload"]))

    def test_no_duplicate_task_is_created(self) -> None:
        task_id, _ = self.task()
        self.mark_owner(pid=2_000_000_000, last_seen=int(time.time()) - 3600)
        before = task_context.list_tasks()["count"]
        result = self.call(task_id=task_id, action="reclaim", expected_owner_agent_id="owner")
        after = task_context.list_tasks()["count"]
        self.assertEqual(result["task_id"], task_id)
        self.assertEqual(before, after)

    def test_recovery_epoch_is_monotone(self) -> None:
        task_id, _ = self.task()
        self.mark_owner(pid=2_000_000_000, last_seen=int(time.time()) - 3600)
        before = int(tenor_public_api._activity_row(task_id).get("recovery_epoch", 0))
        result = self.call(task_id=task_id, action="reclaim", expected_owner_agent_id="owner")
        self.assertEqual(result["verdict"], "TENOR_TASK_RECLAIMED")
        self.assertEqual(int(result["recovery_epoch"]), before + 1)

    def test_reclaim_migrates_active_decision_capsule_atomically(self) -> None:
        task_id, _ = self.task()
        tenor_decision.ensure_schema(self.root)
        payload = {
            "schema": "tenor_decision_capsule_v1",
            "task_id": task_id,
            "agent_id": "owner",
            "objective": "orphan task",
            "intent": "write",
            "scope": "test",
            "resources": ["src/feature.txt"],
            "recovery_epoch": 0,
        }
        payload_json = tenor_decision._json(payload)
        capsule_hash = tenor_decision._sha256_bytes(payload_json.encode("utf-8"))
        with db.connect(self.root) as con:
            con.execute(
                f"INSERT INTO {tenor_decision.CAPSULE_TABLE}(task_id,agent_id,capsule_hash,payload_json,status,created_at) VALUES(?,?,?,?,?,?)",
                (task_id, "owner", capsule_hash, payload_json, "active", int(time.time())),
            )
        self.mark_owner(pid=2_000_000_000, last_seen=int(time.time()) - 3600)
        result = self.call(task_id=task_id, action="reclaim", expected_owner_agent_id="owner")
        self.assertEqual(result["verdict"], "TENOR_TASK_RECLAIMED")
        capsule = tenor_decision.load_capsule(self.root, task_id)
        self.assertIsNotNone(capsule)
        self.assertEqual(capsule["agent_id"], "new-owner")
        self.assertEqual(capsule["payload"]["agent_id"], "new-owner")
        self.assertEqual(capsule["payload"]["recovery_epoch"], result["recovery_epoch"])
        self.assertEqual(capsule["capsule_hash"], tenor_decision._sha256_bytes(tenor_decision._json(capsule["payload"]).encode("utf-8")))

    def test_existing_control_actions_remain_advertised(self) -> None:
        schema = tenor_public_api.tool_schema("tenor_task_control")
        actions = schema["properties"]["action"]["enum"]
        for action in ("pause", "resume", "cancel", "finish", "memory_promote", "memory_skip", "reclaim"):
            self.assertIn(action, actions)
        self.assertIn("expected_owner_agent_id", schema["properties"])


if __name__ == "__main__":
    unittest.main()
