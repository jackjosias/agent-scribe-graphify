from __future__ import annotations

"""Host-adapter suite with the hash-bound first-write discovery expectation."""

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_IMPL_PATH = HERE / "_test_host_adapter_autoguard_impl.py"
_SPEC = importlib.util.spec_from_file_location("_test_host_adapter_autoguard_impl", _IMPL_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load host-adapter test implementation: {_IMPL_PATH}")
_impl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_impl)


class HostAdapterAutoGuardTest(_impl.HostAdapterAutoGuardTest):
    def test_guard_requires_bounded_discovery_before_first_write_lease(self) -> None:
        agent_id = "test-agent"
        config = _impl.HostLaunchConfig(
            agent_id=agent_id,
            host_type="opencode",
            workspace_root=self.root,
        )
        _impl.call_tool("register_agent", agent_id=agent_id, host_tool="opencode")
        before = _impl.call_tool(
            "before_task",
            agent_id=agent_id,
            request="fix bug",
            intent="write",
            resource="code.py",
        )
        task_id, token = before["task_id"], before["context_token"]

        unrelated = _impl.mcp.server.ok({
            "ok": True,
            "verdict": "SCRIBE_QUERY_DONE",
            "result": {
                "returncode": 0,
                "stdout": "historical decision about an unrelated subsystem",
                "stderr": "",
            },
        })
        with mock.patch.object(
            _impl.mcp.server,
            "_BASE_SCRIBE_QUERY_POLICY",
            return_value=unrelated,
        ):
            scribe = _impl.call_tool(
                "scribe_query",
                agent_id=agent_id,
                task_id=task_id,
                context_token=token,
                query="resource:code.py first intervention",
            )
        self.assertEqual(scribe["verdict"], "SCRIBE_CONTEXT_MISS_FOR_WRITE")
        self.assertFalse(scribe["historical_scribe_context_found"])

        graphify = _impl.call_tool(
            "graphify_query",
            agent_id=agent_id,
            task_id=task_id,
            context_token=token,
            query="impact dependencies blast radius for code.py",
            resource="code.py",
        )
        self.assertEqual(graphify["verdict"], "GRAPHIFY_QUERY_DONE")

        blocked = _impl.run_pre_action_guard(
            config,
            "fix bug",
            "write",
            "code.py",
            "claim_resource",
            task_id,
            token,
        )
        self.assertEqual(blocked.get("verdict"), "TASK_DISCOVERY_BASE_HASH_REQUIRED")
        self.assertEqual(blocked.get("must_call", {}).get("tool"), "file_hash")

        current_hash = _impl.call_tool("file_hash", resource="code.py")["hash"]
        routed = _impl.call_tool(
            "workflow_next",
            agent_id=agent_id,
            task_id=task_id,
            context_token=token,
            request="fix bug",
            intent="write",
            resource="code.py",
            base_hash=current_hash,
            last_verdict="FILE_HASH",
        )
        self.assertEqual(routed["verdict"], "TASK_DISCOVERY_REQUIRED")
        self.assertEqual(routed["must_call"]["tool"], "record_task_discovery")

        recorded = _impl.call_tool(
            "record_task_discovery",
            agent_id=agent_id,
            task_id=task_id,
            context_token=token,
            resource="code.py",
            base_hash=current_hash,
            summary=(
                "Inspected code.py and selected the smallest correction that reuses the existing host gate."
            ),
            evidence=(
                "code.py is the exact target. Graphify was queried for its dependencies and blast radius; "
                "the surrounding host-adapter workflow and regression tests were inspected before ownership."
            ),
        )
        self.assertEqual(recorded["verdict"], "TASK_DISCOVERY_RECORDED")
        self.assertFalse(recorded["canonical_memory_updated"])
        self.assertEqual(recorded["base_hash"], current_hash)

        result = _impl.run_pre_action_guard(
            config,
            "fix bug",
            "write",
            "code.py",
            "claim_resource",
            task_id,
            token,
        )
        self.assertEqual(result.get("verdict"), "PRE_ACTION_GUARD_OK")
        self.assertEqual(result.get("state"), "ACTION_LEASE_ISSUED")


if __name__ == "__main__":
    unittest.main()
