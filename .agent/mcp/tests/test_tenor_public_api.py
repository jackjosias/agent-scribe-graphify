from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import server_ext as mcp
from runtime import db, task_context, tenor_public_api
from _strict_cleanup import remove_tree_strict


class TenorPublicApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "feature.txt").write_bytes(b"before\n")
        self.old_cwd = Path.cwd()
        self.old_root = mcp.server.ROOT
        self.old_bound = getattr(mcp.server, "_MCP_BOUND_AGENT_ID", "")
        self.old_scribe = mcp.server.scribe_query
        self.old_graphify = mcp.server.graphify_query
        os.chdir(self.root)
        mcp.server.ROOT = self.root
        db.init_db(self.root)
        db.register_agent("test", "unit", "agent-a")
        mcp.server._MCP_BOUND_AGENT_ID = "agent-a"
        tenor_public_api.ensure_schema(self.root)

        def fake_scribe_query(
            query: str,
            limit: int = 5,
            agent_id: str = "",
            task_id: str = "",
            context_token: str = "",
        ) -> dict[str, object]:
            task_context.mark_scribe_done(
                agent_id,
                task_id,
                context_token,
                result_count=1,
                result_resources="src/feature.txt",
            )
            return mcp.server.ok({
                "ok": True,
                "verdict": "SCRIBE_QUERY_DONE",
                "query": query,
                "limit": limit,
                "result": {"returncode": 0, "stdout": "feature.txt historical decision"},
            })

        def fake_graphify_query(
            query: str = "",
            resource: str = "",
            agent_id: str = "",
            task_id: str = "",
            context_token: str = "",
        ) -> dict[str, object]:
            task_context.mark_graphify_done(agent_id, task_id, context_token)
            return mcp.server.ok({
                "ok": True,
                "verdict": "GRAPHIFY_QUERY_DONE",
                "query": query,
                "resource": resource,
            })

        mcp.server.scribe_query = fake_scribe_query
        mcp.server.graphify_query = fake_graphify_query

    def tearDown(self) -> None:
        tenor_public_api._TASK_TOKENS.clear()
        mcp.server.scribe_query = self.old_scribe
        mcp.server.graphify_query = self.old_graphify
        mcp.server._MCP_BOUND_AGENT_ID = self.old_bound
        mcp.server.ROOT = self.old_root
        os.chdir(self.old_cwd)
        remove_tree_strict(self.tmp.name)
        self.tmp.cleanup()

    def call(self, tool: str, **arguments: object) -> dict[str, object]:
        response = mcp.handle({
            "jsonrpc": "2.0",
            "id": tool,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        })
        return json.loads(response["result"]["content"][0]["text"])

    def test_tools_list_advertises_only_bootstrap_plus_four_task_tools(self) -> None:
        response = mcp.handle({"jsonrpc": "2.0", "id": "tools", "method": "tools/list", "params": {}})
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(names, list(tenor_public_api.PUBLIC_TOOL_NAMES))
        self.assertEqual(
            set(tenor_public_api.PUBLIC_TASK_TOOLS),
            {"tenor_task_start", "tenor_apply_changeset", "tenor_activity", "tenor_task_control"},
        )
        self.assertNotIn("workflow_next", names)
        self.assertNotIn("propose_patch", names)

    def test_unbound_process_cannot_start_task(self) -> None:
        mcp.server._MCP_BOUND_AGENT_ID = ""
        result = self.call("tenor_task_start", objective="inspect", intent="read", resources=["src/feature.txt"])
        self.assertEqual(result["verdict"], "TENOR_SESSION_NOT_BOUND")

    def test_one_start_call_runs_scribe_and_graphify_server_side(self) -> None:
        result = self.call(
            "tenor_task_start",
            objective="fix feature safely",
            intent="write",
            resources=["src/feature.txt"],
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["verdict"], "TENOR_TASK_READY")
        self.assertEqual(result["scribe"]["verdict"], "SCRIBE_QUERY_DONE")
        self.assertEqual(result["graphify"]["verdict"], "GRAPHIFY_QUERY_DONE")
        task = task_context.task_status(result["task_id"])
        self.assertTrue(task["scribe_done"])
        self.assertTrue(task["graphify_done"])

    def test_identical_retry_rehydrates_blocked_task_without_creating_a_duplicate(self) -> None:
        working_scribe = mcp.server.scribe_query

        def unavailable_scribe_query(
            query: str,
            limit: int = 5,
            agent_id: str = "",
            task_id: str = "",
            context_token: str = "",
        ) -> dict[str, object]:
            del query, limit, agent_id, task_id, context_token
            return mcp.server.ok({
                "ok": False,
                "verdict": "SCRIBE_UNAVAILABLE",
                "result": {"returncode": 1, "stdout": "", "stderr": "temporary outage"},
            })

        mcp.server.scribe_query = unavailable_scribe_query
        first = self.call(
            "tenor_task_start",
            objective="retry context hydration",
            intent="write",
            resources=["src/feature.txt"],
        )
        self.assertEqual(first["verdict"], "TENOR_TASK_SCRIBE_FAILED")
        self.assertEqual(task_context.task_status(first["task_id"])["status"], "active")

        mcp.server.scribe_query = working_scribe
        retried = self.call(
            "tenor_task_start",
            objective="retry context hydration",
            intent="write",
            resources=["src/feature.txt"],
        )
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(retried["verdict"], "TENOR_TASK_RESUMED")
        self.assertEqual(retried["task_id"], first["task_id"])
        task = task_context.task_status(retried["task_id"])
        self.assertTrue(task["scribe_done"])
        self.assertTrue(task["graphify_done"])

    def test_second_public_call_commits_validates_records_and_finishes(self) -> None:
        started = self.call(
            "tenor_task_start",
            objective="replace feature atomically",
            intent="write",
            resources=["src/feature.txt"],
        )
        before_hash = hashlib.sha256(b"before\n").hexdigest()
        result = self.call(
            "tenor_apply_changeset",
            task_id=started["task_id"],
            changes=[{
                "path": "src/feature.txt",
                "operation": "replace",
                "base_hash": before_hash,
                "content": "after\n",
            }],
            validators=[{
                "argv": [sys.executable, "-c", "from pathlib import Path; assert Path('src/feature.txt').read_text() == 'after\\n'"],
                "timeout_seconds": 20,
            }],
            summary="feature replacement validated",
            request_id="public-api-success",
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["verdict"], "TENOR_CHANGESET_COMMITTED_TASK_FINISHED")
        self.assertTrue(result["terminal"])
        self.assertEqual((self.root / "src" / "feature.txt").read_text(encoding="utf-8"), "after\n")
        self.assertEqual(task_context.task_status(started["task_id"])["status"], "finished")
        record = self.root / result["scribe_record"]["record_path"]
        self.assertTrue(record.is_file())

    def test_other_bound_agent_cannot_control_or_apply_first_agents_task(self) -> None:
        started = self.call(
            "tenor_task_start",
            objective="owned task",
            intent="write",
            resources=["src/feature.txt"],
        )
        db.register_agent("test", "unit", "agent-b")
        mcp.server._MCP_BOUND_AGENT_ID = "agent-b"
        control = self.call("tenor_task_control", task_id=started["task_id"], action="cancel")
        self.assertEqual(control["verdict"], "TENOR_TASK_OWNER_MISMATCH")
        apply = self.call(
            "tenor_apply_changeset",
            task_id=started["task_id"],
            changes=[{
                "path": "src/feature.txt",
                "operation": "replace",
                "base_hash": hashlib.sha256(b"before\n").hexdigest(),
                "content": "owned\n",
            }],
        )
        self.assertEqual(apply["verdict"], "TENOR_TASK_OWNER_MISMATCH")
        self.assertEqual((self.root / "src" / "feature.txt").read_text(encoding="utf-8"), "before\n")

    def test_read_task_finishes_through_control_without_write_ceremony(self) -> None:
        started = self.call(
            "tenor_task_start",
            objective="inspect feature",
            intent="read",
            resources=["src/feature.txt"],
        )
        finished = self.call(
            "tenor_task_control",
            task_id=started["task_id"],
            action="finish",
            summary="inspection complete",
        )
        self.assertTrue(finished["ok"], finished)
        self.assertEqual(finished["verdict"], "TENOR_TASK_FINISHED")
        self.assertTrue(finished["terminal"])

    def test_scribe_receipt_survives_project_root_symlink_alias(self) -> None:
        alias_root = Path(self.tmp.name) / "project-alias"
        try:
            alias_root.symlink_to(self.root, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        mcp.server.ROOT = alias_root
        started = self.call(
            "tenor_task_start",
            objective="inspect through aliased root",
            intent="read",
            resources=["src/feature.txt"],
        )
        self.assertTrue(started["ok"], started)
        finished = self.call(
            "tenor_task_control",
            task_id=started["task_id"],
            action="finish",
            summary="aliased-root inspection complete",
        )
        self.assertTrue(finished["ok"], finished)
        self.assertEqual(finished["verdict"], "TENOR_TASK_FINISHED")
        record_path = str(finished["scribe_record"])
        self.assertFalse(Path(record_path).is_absolute())
        self.assertTrue((self.root / record_path).is_file())


if __name__ == "__main__":
    unittest.main()
