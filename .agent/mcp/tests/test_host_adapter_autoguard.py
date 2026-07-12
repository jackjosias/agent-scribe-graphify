#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))
ROOT_DIR = MCP_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import server_ext as mcp
from host_adapter.instructions import install_host_instructions, update_marked_block, verify_instruction_installation
from host_adapter.launcher import HostLaunchConfig, TENOR_INIT_REQUIRED, run_pre_action_guard, run_preflight, run_workspace_audit
from host_adapter.policy import HostPolicy, HostVerdict
from host_adapter.templates import render_minimal_host_instructions
from runtime import db, discipline, graphify_readiness, installation_state, patch_queue, task_context


def _json_payload(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["result"]["content"][0]["text"])


def call_tool(name: str, **args: Any) -> dict[str, Any]:
    return _json_payload(mcp.handle({
        "jsonrpc": "2.0",
        "id": f"test-{name}",
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }))


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(root), text=True, capture_output=True, timeout=15, check=False)


class HostAdapterAutoGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="host-adapter-tests-"))
        self.old_cwd = Path.cwd()
        self.old_fixture_env = os.environ.get(graphify_readiness.FIXTURE_ENV)
        os.environ[graphify_readiness.FIXTURE_ENV] = "1"
        os.chdir(self.root)

        self.agent_dir = self.root / ".agent"
        (self.agent_dir / "mcp").mkdir(parents=True, exist_ok=True)
        (self.agent_dir / "state" / "patch_queue").mkdir(parents=True, exist_ok=True)
        entry_file = self.agent_dir / "mcp" / "server_entry.py"
        entry_file.write_text(
            "#!/usr/bin/env python3\n"
            "import runpy\n"
            f"runpy.run_path({str(MCP_DIR / 'server_ext.py')!r}, run_name='__main__')\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            entry_file.chmod(0o755)

        git(self.root, "init")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "config", "user.name", "Host Adapter Test")

        prepared = installation_state.ensure_fresh_installation_state(self.root)
        self.assertTrue(prepared["ok"])
        self.assertTrue(installation_state.finalize_installation_state(self.root)["ok"])
        self.assertTrue(graphify_readiness.write_smoke_fixture(self.root)["ok"])

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
        if self.old_fixture_env is None:
            os.environ.pop(graphify_readiness.FIXTURE_ENV, None)
        else:
            os.environ[graphify_readiness.FIXTURE_ENV] = self.old_fixture_env
        shutil.rmtree(self.root, ignore_errors=True)

    def test_policy_requires_complete_v216_surface(self) -> None:
        required = HostPolicy(self.root).get_required_tools()
        for tool in (
            "workflow_next", "before_task", "discipline_ping", "scribe_query",
            "graphify_query", "pre_action_guard", "resource_lock_claim",
            "propose_patch", "apply_patch", "workspace_audit", "finish_task",
            "tenor_init_bridge",
        ):
            self.assertIn(tool, required)

    def test_policy_marks_missing_mcp_as_unsafe(self) -> None:
        policy = HostPolicy(self.root)
        self.assertFalse(policy.validate_mcp_tools(["some_other_tool"]))
        verdict = policy.decide_host_safety_level(["some_other_tool"], {"workspace_write": True})
        self.assertEqual(verdict, HostVerdict.UNSAFE)

    def test_policy_never_calls_normal_writable_host_safe(self) -> None:
        policy = HostPolicy(self.root)
        verdict = policy.decide_host_safety_level(
            policy.get_required_tools(),
            {"workspace_write": True, "shell_access": True},
            instructions_installed=True,
        )
        self.assertEqual(verdict, HostVerdict.SAFE_CANDIDATE)

    def test_render_minimal_host_instructions_contains_v216_order(self) -> None:
        instructions = render_minimal_host_instructions("opencode")
        self.assertIn("AGENT-SCRIBE-GRAPHIFY AUTO-GUARD", instructions)
        self.assertIn("scribe tenor-init", instructions)
        self.assertIn("HOST_MCP_UNBOUND", instructions)
        self.assertIn("discipline_ping", instructions)
        self.assertIn("workspace_audit", instructions)
        self.assertIn("prose-only", instructions)

    def test_install_instructions_is_atomic_and_idempotent(self) -> None:
        target = self.root / "AGENTS.md"
        first = install_host_instructions(target, "opencode", self.root)
        self.assertTrue(first["ok"])
        content = target.read_text(encoding="utf-8")
        second = install_host_instructions(target, "opencode", self.root)
        self.assertTrue(second["ok"])
        self.assertFalse(second["changed"])
        self.assertEqual(content, target.read_text(encoding="utf-8"))
        self.assertTrue(verify_instruction_installation(target))
        self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_concurrent_install_instructions_is_collision_proof(self) -> None:
        target = self.root / "AGENTS.md"
        target.write_text("Manual project rule.\n", encoding="utf-8")

        def install(_: int) -> dict[str, Any]:
            return install_host_instructions(target, "opencode", self.root)

        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(install, range(64)))

        self.assertTrue(all(result.get("ok") for result in results), results)
        content = target.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("Manual project rule."))
        self.assertEqual(content.count("auto-guard:start"), 1)
        self.assertEqual(content.count("auto-guard:end"), 1)
        self.assertTrue(verify_instruction_installation(target))
        self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_install_instructions_preserves_existing_content(self) -> None:
        target = self.root / "AGENTS.md"
        target.write_text("Existing manual instructions.\n", encoding="utf-8")
        result = install_host_instructions(target, "opencode", self.root)
        self.assertTrue(result["ok"])
        content = target.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("Existing manual instructions."))
        self.assertEqual(content.count("auto-guard:start"), 1)
        self.assertEqual(content.count("auto-guard:end"), 1)

    def test_preflight_requires_tenor_before_server_probe(self) -> None:
        fresh = Path(tempfile.mkdtemp(prefix="host-preflight-uninitialized-"))
        try:
            (fresh / ".agent" / "mcp").mkdir(parents=True)
            (fresh / ".agent" / "mcp" / "server_entry.py").write_text("# marker\n", encoding="utf-8")
            result = run_preflight(HostLaunchConfig(host_type="opencode", workspace_root=fresh))
            self.assertFalse(result["ok"])
            self.assertEqual(result["verdict"], TENOR_INIT_REQUIRED)
            self.assertEqual(result["state"], "LOCAL_INIT_REQUIRED")
        finally:
            shutil.rmtree(fresh, ignore_errors=True)

    def test_preflight_separates_local_server_from_host_visibility(self) -> None:
        result = run_preflight(HostLaunchConfig(host_type="opencode", workspace_root=self.root))
        self.assertTrue(result["local_server_ready"])
        self.assertIsNone(result["host_tools_visible_to_llm"])
        self.assertEqual(result["host_visibility_verdict"], "HOST_MCP_UNBOUND")
        self.assertTrue(result["instruction_block_ok"])

    def test_guard_routes_to_before_task_when_no_task(self) -> None:
        config = HostLaunchConfig(agent_id="test-agent", host_type="opencode", workspace_root=self.root)
        self.assertEqual(call_tool("register_agent", agent_id="test-agent", host_tool="opencode")["verdict"], "AGENT_REGISTERED")
        result = run_pre_action_guard(config, "implement feature", "write", "code.py", "claim_resource")
        self.assertTrue(result.get("ok", True))
        self.assertEqual(result.get("verdict"), "NEXT_ACTION_REQUIRED")
        self.assertEqual(result.get("state"), "BEFORE_TASK_REQUIRED")
        self.assertEqual(result.get("must_call", {}).get("tool"), "before_task")

    def test_guard_returns_action_lease_when_context_ready(self) -> None:
        agent_id = "test-agent"
        config = HostLaunchConfig(agent_id=agent_id, host_type="opencode", workspace_root=self.root)
        call_tool("register_agent", agent_id=agent_id, host_tool="opencode")
        before = call_tool("before_task", agent_id=agent_id, request="fix bug", intent="write", resource="code.py")
        task_id, token = before["task_id"], before["context_token"]
        call_tool("scribe_query", agent_id=agent_id, task_id=task_id, context_token=token, query="some logic")
        call_tool("graphify_query", agent_id=agent_id, task_id=task_id, context_token=token, query="some logic", resource="code.py")
        result = run_pre_action_guard(config, "fix bug", "write", "code.py", "claim_resource", task_id, token)
        self.assertEqual(result.get("verdict"), "PRE_ACTION_GUARD_OK")
        self.assertEqual(result.get("state"), "ACTION_LEASE_ISSUED")
        self.assertIn("action_lease", result)
        self.assertIn("lease_id", result["action_lease"])

    def test_audit_detects_direct_write(self) -> None:
        agent_id = "test-agent"
        config = HostLaunchConfig(agent_id=agent_id, host_type="opencode", workspace_root=self.root)
        call_tool("register_agent", agent_id=agent_id, host_tool="opencode")
        before = call_tool("before_task", agent_id=agent_id, request="fix bug", intent="write", resource="code.py")
        target = self.root / "code.py"
        target.write_text("import os\n", encoding="utf-8")
        git(self.root, "add", "code.py")
        git(self.root, "commit", "-m", "add code.py")
        clean = run_workspace_audit(config, task_id=before["task_id"], resource="code.py")
        self.assertEqual(clean.get("verdict"), "WORKSPACE_AUDIT_OK")
        target.write_text("import os\n# bypass write\n", encoding="utf-8")
        bypass = run_workspace_audit(config, task_id=before["task_id"], resource="code.py")
        self.assertEqual(bypass.get("verdict"), "DIRECT_WRITE_BYPASS_DETECTED")

    def test_update_marked_block_never_duplicates(self) -> None:
        content = "Line 1\n<!-- agent-scribe-graphify:auto-guard:start -->\nBlock\n<!-- agent-scribe-graphify:auto-guard:end -->\nLine 2"
        block = "<!-- agent-scribe-graphify:auto-guard:start -->\nNew Block\n<!-- agent-scribe-graphify:auto-guard:end -->"
        updated = update_marked_block(content, block)
        self.assertEqual(updated.count("auto-guard:start"), 1)
        self.assertEqual(updated.count("auto-guard:end"), 1)

    def test_path_traversal_rejected_cross_platform(self) -> None:
        outside = self.root.parent / "outside-workspace-target.md"
        with self.assertRaises(ValueError):
            install_host_instructions(outside, "opencode", self.root)

    def test_agent_cannot_use_other_agent_lease(self) -> None:
        call_tool("register_agent", agent_id="agent-a", host_tool="opencode")
        call_tool("register_agent", agent_id="agent-b", host_tool="opencode")
        lease = discipline.issue_action_lease(agent_id="agent-a", action="claim_resource", resource="code.py")
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
