#!/usr/bin/env python3
"""Unit and integration tests for V2.13 host adapter auto-guard.
"""

from __future__ import annotations

import importlib
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

# Insert host_adapter paths
ROOT_DIR = MCP_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import server_ext as mcp
from runtime import db, discipline, patch_queue, task_context
from host_adapter.policy import HostPolicy, HostVerdict
from host_adapter.templates import render_minimal_host_instructions
from host_adapter.instructions import (
    install_host_instructions,
    update_marked_block,
    remove_old_marked_block,
    verify_instruction_installation,
)
from host_adapter.launcher import (
    HostLaunchConfig,
    run_preflight,
    run_discipline_ping,
    run_pre_action_guard,
    run_workspace_audit,
)


def _json_payload(result: dict[str, Any]) -> dict[str, Any]:
    payload = result["result"]["content"][0]["text"]
    return json.loads(payload)


def call_tool(name: str, **args: Any) -> dict[str, Any]:
    return _json_payload(mcp.handle({
        "jsonrpc": "2.0",
        "id": f"test-{name}",
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }))


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(root), text=True, capture_output=True, timeout=15)


class HostAdapterAutoGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="host-adapter-tests-"))
        self.old_cwd = Path.cwd()
        os.chdir(self.root)

        # Structure the dummy .agent structure inside temp dir
        self.agent_dir = self.root / ".agent"
        (self.agent_dir / "mcp").mkdir(parents=True, exist_ok=True)
        (self.agent_dir / "state").mkdir(parents=True, exist_ok=True)
        (self.agent_dir / "state" / "patch_queue").mkdir(parents=True, exist_ok=True)

        # Create graphify-out with valid files for Graphify Mandatory Guard
        graphify_dir = self.root / "graphify-out"
        graphify_dir.mkdir(parents=True, exist_ok=True)
        (graphify_dir / "graph.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        (graphify_dir / "GRAPH_REPORT.md").write_text("# Graphify Report\n\nEmpty.\n", encoding="utf-8")
        (graphify_dir / "graph.html").write_text("<html><body></body></html>\n", encoding="utf-8")

        # Link server_entry.py mockup
        entry_file = self.agent_dir / "mcp" / "server_entry.py"
        entry_file.write_text(
            f"#!/usr/bin/env python3\n"
            f"import os, runpy\n"
            f"runpy.run_path('{MCP_DIR}/server_ext.py', run_name='__main__')\n",
            encoding="utf-8",
        )
        entry_file.chmod(0o755)

        # Prepare dummy git
        git(self.root, "init")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "config", "user.name", "Host Adapter Test")

        mcp.server.ROOT = self.root.resolve()
        mcp.server.AGENT_DIR = self.root / ".agent"

        importlib.reload(db)
        importlib.reload(patch_queue)
        importlib.reload(task_context)
        importlib.reload(discipline)

        mcp.db = db
        mcp.patch_queue = patch_queue
        mcp.task_context = task_context
        mcp.discipline = discipline

        db.init_db(self.root)
        discipline.ensure_schema()

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_policy_requires_v212_tools(self) -> None:
        policy = HostPolicy(self.root)
        reqs = policy.get_required_tools()
        self.assertIn("discipline_ping", reqs)
        self.assertIn("pre_action_guard", reqs)
        self.assertIn("workspace_audit", reqs)
        self.assertIn("workflow_next", reqs)
        self.assertIn("workflow_snapshot", reqs)
        self.assertIn("batch_file_hash", reqs)
        self.assertIn("resume_task_context", reqs)

    def test_policy_marks_missing_mcp_as_UNSAFE(self) -> None:
        policy = HostPolicy(self.root)
        self.assertFalse(policy.validate_mcp_tools(["some_other_tool"]))
        capabilities = {"direct_fs_write": True}
        verdict = policy.decide_host_safety_level(["some_other_tool"], capabilities)
        self.assertEqual(verdict, HostVerdict.UNSAFE)

    def test_policy_marks_direct_write_host_as_SAFE_CANDIDATE_not_SAFE(self) -> None:
        policy = HostPolicy(self.root)
        required = policy.get_required_tools()
        capabilities = {"direct_fs_write": True}
        verdict = policy.decide_host_safety_level(
            required, capabilities, instructions_installed=True,
        )
        self.assertEqual(verdict, HostVerdict.SAFE_CANDIDATE)

    def test_render_minimal_host_instructions_contains_required_steps(self) -> None:
        instr = render_minimal_host_instructions("opencode")
        self.assertIn("AGENT-SCRIBE-GRAPHIFY AUTO-GUARD", instr)
        self.assertIn("1. Use local .agent MCP.", instr)
        self.assertIn("discipline_ping", instr)
        self.assertIn("pre_action_guard", instr)
        self.assertIn("workspace_audit", instr)

    def test_install_instructions_is_idempotent(self) -> None:
        target = self.root / "AGENTS.md"
        res = install_host_instructions(target, "opencode", self.root)
        self.assertTrue(res["ok"])
        c1 = target.read_text(encoding="utf-8")

        # Second run
        res2 = install_host_instructions(target, "opencode", self.root)
        self.assertTrue(res2["ok"])
        c2 = target.read_text(encoding="utf-8")

        self.assertEqual(c1, c2)

    def test_install_instructions_does_not_destroy_existing_content(self) -> None:
        target = self.root / "AGENTS.md"
        target.write_text("Existing manual instructions.\n", encoding="utf-8")
        res = install_host_instructions(target, "opencode", self.root)
        self.assertTrue(res["ok"])
        content = target.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("Existing manual instructions."))
        self.assertIn("AGENT-SCRIBE-GRAPHIFY AUTO-GUARD", content)

    def test_guard_cli_routes_to_before_task_when_no_task(self) -> None:
        config = HostLaunchConfig(
            agent_id="test-agent",
            host_type="opencode",
            workspace_root=self.root,
        )
        # Register agent first
        res_reg = call_tool("register_agent", agent_id="test-agent", host_tool="opencode")
        self.assertEqual(res_reg["verdict"], "AGENT_REGISTERED")

        # Run guard
        res = run_pre_action_guard(
            config=config,
            request="implement feature",
            intent="write",
            resource="code.py",
            planned_action="claim_resource",
        )
        self.assertTrue(res.get("ok", True))
        self.assertEqual(res.get("verdict"), "NEXT_ACTION_REQUIRED")
        self.assertEqual(res.get("state"), "BEFORE_TASK_REQUIRED")
        self.assertEqual(res.get("must_call", {}).get("tool"), "before_task")

    def test_guard_cli_returns_action_lease_when_ready(self) -> None:
        agent_id = "test-agent"
        config = HostLaunchConfig(
            agent_id=agent_id,
            host_type="opencode",
            workspace_root=self.root,
        )
        # Register agent
        call_tool("register_agent", agent_id=agent_id, host_tool="opencode")

        # before_task
        res_bt = call_tool("before_task", agent_id=agent_id, request="fix bug", intent="write", resource="code.py")
        self.assertEqual(res_bt["verdict"], "BEFORE_TASK_OK")
        task_id = res_bt["task_id"]
        context_token = res_bt["context_token"]

        # Run scribe_query & graphify_query to make context ready
        call_tool("scribe_query", agent_id=agent_id, task_id=task_id, context_token=context_token, query="some logic")
        call_tool("graphify_query", agent_id=agent_id, task_id=task_id, context_token=context_token, query="some logic", resource="code.py")

        config.task_id = task_id
        config.context_token = context_token

        # Run guard
        res = run_pre_action_guard(
            config=config,
            request="fix bug",
            intent="write",
            resource="code.py",
            planned_action="claim_resource",
        )
        self.assertEqual(res.get("verdict"), "PRE_ACTION_GUARD_OK")
        self.assertEqual(res.get("state"), "ACTION_LEASE_ISSUED")
        self.assertIn("action_lease", res)
        self.assertIn("lease_id", res["action_lease"])

    def test_audit_cli_detects_direct_write(self) -> None:
        agent_id = "test-agent"
        config = HostLaunchConfig(
            agent_id=agent_id,
            host_type="opencode",
            workspace_root=self.root,
        )
        # Register agent
        call_tool("register_agent", agent_id=agent_id, host_tool="opencode")

        # before_task
        res_bt = call_tool("before_task", agent_id=agent_id, request="fix bug", intent="write", resource="code.py")
        task_id = res_bt["task_id"]
        context_token = res_bt["context_token"]

        # Create target file & commit
        target_file = self.root / "code.py"
        target_file.write_text("import os\n", encoding="utf-8")
        git(self.root, "add", "code.py")
        git(self.root, "commit", "-m", "add code.py")

        # Workspace audit on clean repo should be OK
        res_audit = run_workspace_audit(config, task_id=task_id, resource="code.py")
        self.assertEqual(res_audit.get("verdict"), "WORKSPACE_AUDIT_OK")

        # Edit directly bypassing MCP
        target_file.write_text("import os\n# bypass write\n", encoding="utf-8")

        # Now audit should detect the bypass!
        res_audit_bypass = run_workspace_audit(config, task_id=task_id, resource="code.py")
        self.assertEqual(res_audit_bypass.get("verdict"), "DIRECT_WRITE_BYPASS_DETECTED")

    def test_no_duplicate_marked_blocks(self) -> None:
        content = "Line 1\n<!-- agent-scribe-graphify:auto-guard:start -->\nBlock\n<!-- agent-scribe-graphify:auto-guard:end -->\nLine 2"
        updated = update_marked_block(content, "<!-- agent-scribe-graphify:auto-guard:start -->\nNew Block\n<!-- agent-scribe-graphify:auto-guard:end -->")
        # Ensure only one block exists
        self.assertEqual(updated.count("auto-guard:start"), 1)
        self.assertEqual(updated.count("auto-guard:end"), 1)

    def test_path_traversal_rejected(self) -> None:
        # Traversal target outside self.root
        bad_target = Path("/tmp/outside_workspace_target.md")
        with self.assertRaises(ValueError):
            install_host_instructions(bad_target, "opencode", self.root)

    def test_agent_cannot_use_other_agent_lease(self) -> None:
        # Register Agent A & Agent B
        call_tool("register_agent", agent_id="agent-a", host_tool="opencode")
        call_tool("register_agent", agent_id="agent-b", host_tool="opencode")

        # Issue lease for Agent A
        lease = discipline.issue_action_lease(
            agent_id="agent-a",
            action="claim_resource",
            resource="code.py",
        )
        self.assertEqual(lease["status"], "active")

        # Validate with Agent B (should raise DisciplineError)
        with self.assertRaises(discipline.DisciplineError) as context:
            discipline.validate_action_lease(
                lease_id=lease["lease_id"],
                agent_id="agent-b",
                action="claim_resource",
                resource="code.py",
            )
        self.assertEqual(context.exception.code, "ACTION_LEASE_INVALID")
        self.assertEqual(context.exception.details.get("reason"), "agent_mismatch")


if __name__ == "__main__":
    unittest.main()
