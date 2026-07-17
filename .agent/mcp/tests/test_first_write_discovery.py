#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
AGENT_DIR = MCP_DIR.parent
if str(MCP_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(MCP_DIR))
if str(AGENT_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(AGENT_DIR))

import server_ext as mcp
from runtime import (
    db,
    graphify_readiness,
    installation_state,
    patch_queue,
    task_context,
    task_discovery,
)


def payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


class FirstWriteDiscoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="first-write-discovery-"))
        self.old_cwd = Path.cwd()
        self.old_fixture = os.environ.get(graphify_readiness.FIXTURE_ENV)
        os.environ[graphify_readiness.FIXTURE_ENV] = "1"
        os.chdir(self.root)

        (self.root / ".agent" / "mcp").mkdir(parents=True, exist_ok=True)
        prepared = installation_state.ensure_fresh_installation_state(self.root)
        self.assertTrue(prepared["ok"], prepared)
        finalized = installation_state.finalize_installation_state(self.root)
        self.assertTrue(finalized["ok"], finalized)
        fixture = graphify_readiness.write_smoke_fixture(self.root)
        self.assertTrue(fixture["ok"], fixture)

        mcp.server.ROOT = self.root.resolve()
        mcp.server.AGENT_DIR = self.root / ".agent"
        importlib.reload(db)
        importlib.reload(patch_queue)
        importlib.reload(task_context)
        importlib.reload(task_discovery)
        mcp.db = db
        mcp.task_context = task_context
        mcp.task_discovery = task_discovery
        db.init_db(self.root)
        db.register_agent(
            host_tool="first-write-test",
            model_name="test",
            agent_id="first-write-agent",
        )
        self.agent_id = "first-write-agent"
        self.resource = "first_write_discovery_probe_9f7a.txt"
        (self.root / self.resource).write_text("alpha\n", encoding="utf-8")
        self.memory = self.root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
        self.memory.write_text("canonical-memory-sentinel\n", encoding="utf-8")
        self.memory_hash = hashlib.sha256(self.memory.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        if self.old_fixture is None:
            os.environ.pop(graphify_readiness.FIXTURE_ENV, None)
        else:
            os.environ[graphify_readiness.FIXTURE_ENV] = self.old_fixture
        shutil.rmtree(self.root, ignore_errors=True)

    def new_task(self, resource: str | None = None) -> tuple[str, str]:
        result = task_context.create_task_context(
            self.agent_id,
            "implement a new resource with no prior SCRIBE history",
            intent="write",
            resource=resource or self.resource,
            requires_graphify=True,
        )
        return result["task_id"], result["context_token"]

    def fake_scribe_result(self, stdout: str) -> dict:
        return mcp.server.ok({
            "verdict": "SCRIBE_QUERY_DONE",
            "query": "targeted query",
            "result": {
                "returncode": 0,
                "stdout": stdout,
                "stderr": "",
            },
        })

    def test_first_write_miss_requires_bound_discovery_before_lock(self) -> None:
        task_id, token = self.new_task()
        with mock.patch.object(
            mcp.server,
            "_BASE_SCRIBE_QUERY_POLICY",
            return_value=self.fake_scribe_result(
                "unrelated historical decision about another subsystem"
            ),
        ):
            miss = payload(mcp.scribe_query(
                query=f"resource:{self.resource}",
                limit=5,
                agent_id=self.agent_id,
                task_id=task_id,
                context_token=token,
            ))
        self.assertEqual(miss["verdict"], "SCRIBE_CONTEXT_MISS_FOR_WRITE")
        self.assertEqual(miss["state"], "FIRST_WRITE_DISCOVERY_REQUIRED")
        self.assertFalse(miss["discovery"].get("discovery_done", False))

        task_context.mark_graphify_done(self.agent_id, task_id, token)

        blocked_lock = payload(mcp.server.TOOLS["resource_lock_claim"](
            agent_id=self.agent_id,
            resource=self.resource,
            task_id=task_id,
            context_token=token,
            ttl_seconds=120,
        ))
        self.assertEqual(blocked_lock["verdict"], "TASK_DISCOVERY_REQUIRED")

        next_hash = payload(mcp.workflow_next(
            agent_id=self.agent_id,
            task_id=task_id,
            context_token=token,
            request="continue exact-resource first write",
            intent="write",
            resource=self.resource,
            last_verdict="GRAPHIFY_QUERY_DONE",
        ))
        self.assertEqual(next_hash["state"], "DISCOVERY_BASE_HASH_REQUIRED")
        self.assertEqual(next_hash["must_call"]["tool"], "file_hash")

        current = patch_queue.file_hash(self.resource)
        next_record = payload(mcp.workflow_next(
            agent_id=self.agent_id,
            task_id=task_id,
            context_token=token,
            request="continue exact-resource first write",
            intent="write",
            resource=self.resource,
            base_hash=current["hash"],
            last_verdict="FILE_HASH",
        ))
        self.assertEqual(next_record["state"], "FIRST_WRITE_DISCOVERY_REQUIRED")
        self.assertEqual(next_record["must_call"]["tool"], "record_task_discovery")

        wrong_hash = payload(mcp.record_task_discovery(
            agent_id=self.agent_id,
            task_id=task_id,
            context_token=token,
            resource=self.resource,
            base_hash="0" * 64,
            summary="Verified the exact first-write target and its local role.",
            evidence=(
                f"{self.resource} is the exact target. "
                "Graphify was queried and the current implementation was inspected "
                "against its neighboring modules before any mutation was attempted."
            ),
        ))
        self.assertEqual(wrong_hash["verdict"], "TASK_DISCOVERY_BASE_HASH_MISMATCH")

        recorded = payload(mcp.record_task_discovery(
            agent_id=self.agent_id,
            task_id=task_id,
            context_token=token,
            resource=self.resource,
            base_hash=current["hash"],
            summary="Verified the exact first-write target and its local role.",
            evidence=(
                f"{self.resource} is the exact target. "
                "Graphify was queried and the current implementation was inspected "
                "against its neighboring modules before any mutation was attempted."
            ),
        ))
        self.assertEqual(recorded["verdict"], "TASK_DISCOVERY_RECORDED")
        self.assertFalse(recorded["canonical_memory_updated"])

        ready = task_context.require_context_ready(
            self.agent_id,
            task_id,
            token,
            resource=self.resource,
            strict_resource=True,
            allowed_intents={"write"},
        )
        self.assertEqual(ready["intent"], "write")

        original_bytes = (self.root / self.resource).read_bytes()
        (self.root / self.resource).write_text("changed-after-discovery\n", encoding="utf-8")
        with self.assertRaises(task_context.TaskContextError) as stale:
            task_context.require_context_ready(
                self.agent_id, task_id, token, resource=self.resource,
                strict_resource=True, allowed_intents={"write"},
            )
        self.assertEqual(stale.exception.code, "TASK_DISCOVERY_BASE_STALE")
        (self.root / self.resource).write_bytes(original_bytes)

        guard = payload(mcp.pre_action_guard(
            agent_id=self.agent_id,
            task_id=task_id,
            context_token=token,
            intent="write",
            resource=self.resource,
            request="claim exact first-write resource",
            planned_action="claim_resource",
        ))
        self.assertEqual(guard["verdict"], "PRE_ACTION_GUARD_OK")
        self.assertEqual(guard["state"], "ACTION_LEASE_ISSUED")
        self.assertEqual(guard["action_lease"]["resource"], self.resource)

        acquired = payload(mcp.server.TOOLS["resource_lock_claim"](
            agent_id=self.agent_id,
            resource=self.resource,
            task_id=task_id,
            context_token=token,
            ttl_seconds=120,
        ))
        self.assertEqual(acquired["verdict"], "RESOURCE_LOCK_ACQUIRED")
        released = payload(mcp.server.TOOLS["resource_lock_release"](
            agent_id=self.agent_id,
            resource=self.resource,
            lock_id=acquired["lock_id"],
        ))
        self.assertIn(
            released["verdict"],
            {"RESOURCE_LOCK_RELEASED", "RESOURCE_LOCK_ALREADY_RELEASED"},
        )

        self.assertEqual(
            hashlib.sha256(self.memory.read_bytes()).hexdigest(),
            self.memory_hash,
        )

    def test_query_self_mention_does_not_fake_historical_relevance(self) -> None:
        task_id, token = self.new_task()
        with mock.patch.object(
            mcp.server,
            "_BASE_SCRIBE_QUERY_POLICY",
            return_value=self.fake_scribe_result(
                "historical result about a different rendering module"
            ),
        ):
            result = payload(mcp.scribe_query(
                query=f"please inspect {self.resource}",
                agent_id=self.agent_id,
                task_id=task_id,
                context_token=token,
            ))
        self.assertEqual(result["verdict"], "SCRIBE_CONTEXT_MISS_FOR_WRITE")
        self.assertTrue(task_discovery.scribe_miss_exists(task_id))

    def test_relevant_scribe_result_does_not_require_discovery(self) -> None:
        task_id, token = self.new_task()
        with mock.patch.object(
            mcp.server,
            "_BASE_SCRIBE_QUERY_POLICY",
            return_value=self.fake_scribe_result(
                f"Prior verified context for {self.resource} and its invariant."
            ),
        ):
            result = payload(mcp.scribe_query(
                query=f"resource:{self.resource}",
                agent_id=self.agent_id,
                task_id=task_id,
                context_token=token,
            ))
        self.assertEqual(result["verdict"], "SCRIBE_QUERY_DONE")
        self.assertFalse(task_discovery.scribe_miss_exists(task_id))

    def test_whole_repo_task_is_narrowed_in_place_before_write(self) -> None:
        task_id, token = self.new_task("(whole repo)")
        task_context.mark_scribe_done(
            self.agent_id,
            task_id,
            token,
            result_count=0,
            result_resources="(whole repo)",
        )
        task_context.mark_graphify_done(self.agent_id, task_id, token)
        task_discovery.mark_scribe_miss(
            self.agent_id,
            task_id,
            token,
            "(whole repo)",
            query="whole repository discovery",
            stdout="",
            reason="no exact resource was bound",
        )

        exact = "components/SignpostStrategy.ts"
        (self.root / "components").mkdir(parents=True, exist_ok=True)
        (self.root / exact).write_text("export class SignpostStrategy {}\n", encoding="utf-8")
        routed = payload(mcp.workflow_next(
            agent_id=self.agent_id,
            task_id=task_id,
            context_token=token,
            request="correct the signpost strategy",
            intent="write",
            resource=exact,
            last_verdict="SCRIBE_CONTEXT_MISS_FOR_WRITE",
        ))
        self.assertEqual(routed["state"], "RESOURCE_SCOPING_REQUIRED")
        self.assertEqual(routed["must_call"]["tool"], "scope_task_resource")

        scoped = payload(mcp.scope_task_resource(
            agent_id=self.agent_id,
            task_id=task_id,
            context_token=token,
            resource=exact,
        ))
        self.assertEqual(scoped["verdict"], "TASK_RESOURCE_SCOPED")
        self.assertEqual(scoped["resource"], exact)
        status = task_context.task_status(task_id)
        self.assertEqual(status["resource"], exact)
        self.assertFalse(status["scribe_done"])
        self.assertFalse(status["graphify_done"])
        self.assertFalse(task_discovery.scribe_miss_exists(task_id))
        self.assertEqual(
            task_context.list_tasks(agent_id=self.agent_id, status="active")["count"],
            1,
        )

    def test_existing_directory_scope_narrows_to_descendant_without_patch_catch_22(self) -> None:
        directory = "components/technical-analysis"
        exact = f"{directory}/hooks/useDrawingManager.ts"
        (self.root / directory / "hooks").mkdir(parents=True)
        (self.root / exact).write_text("export const manager = true;\n", encoding="utf-8")
        outside = "components/other.ts"
        (self.root / "components" / "other.ts").write_text("export {};\n", encoding="utf-8")

        task_id, token = self.new_task(directory)
        task_context.mark_scribe_done(
            self.agent_id,
            task_id,
            token,
            result_count=0,
            result_resources=directory,
        )
        task_context.mark_graphify_done(self.agent_id, task_id, token)

        with self.assertRaises(task_discovery.TaskDiscoveryError) as exact_required:
            task_discovery.mark_scribe_miss(
                self.agent_id,
                task_id,
                token,
                exact,
                query="directory-scoped discovery",
                stdout="",
                reason="no history",
            )
        self.assertEqual(
            exact_required.exception.code,
            "TASK_DISCOVERY_SCOPE_TASK_RESOURCE_REQUIRED",
        )

        scoped = payload(mcp.scope_task_resource(
            agent_id=self.agent_id,
            task_id=task_id,
            context_token=token,
            resource=exact,
        ))
        self.assertEqual(scoped["verdict"], "TASK_RESOURCE_SCOPED", scoped)
        self.assertEqual(scoped["previous_resource"], directory)
        self.assertEqual(scoped["resource"], exact)

        # A container is authority for descendants only, never for a sibling.
        task_context.finish_task_context(self.agent_id, task_id, token)
        next_context = task_context.create_task_context(
            self.agent_id,
            "directory scope sibling refusal",
            intent="write",
            resource=directory,
            requires_graphify=False,
        )
        with self.assertRaises(task_context.TaskContextError) as refused:
            task_context.scope_task_resource(
                self.agent_id,
                next_context["task_id"],
                next_context["context_token"],
                outside,
            )
        self.assertEqual(refused.exception.code, "TASK_RESOURCE_OUTSIDE_CONTAINER_SCOPE")

    def test_tools_are_registered_with_strict_schemas(self) -> None:
        self.assertIn("record_task_discovery", mcp.server.TOOLS)
        self.assertIn("scope_task_resource", mcp.server.TOOLS)
        schema = mcp.tool_schema("record_task_discovery")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["required"]),
            {
                "agent_id",
                "task_id",
                "context_token",
                "resource",
                "base_hash",
                "summary",
                "evidence",
            },
        )
        scope_schema = mcp.tool_schema("scope_task_resource")
        self.assertFalse(scope_schema["additionalProperties"])
        self.assertEqual(
            set(scope_schema["required"]),
            {"agent_id", "task_id", "context_token", "resource"},
        )


if __name__ == "__main__":
    unittest.main()
