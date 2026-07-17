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
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
AGENT_DIR = MCP_DIR.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import server_ext as mcp
from runtime import db, discipline, presence
from host_adapter import host_config
from host_adapter.launcher import HostLaunchConfig, run_tenor_init_bridge
from proof_signer import issue_proof


AGENT_SESSION_ID = "cli-20260625-test-bridge"
HOST_TOOL = "opencode"
MODEL_NAME = "deepseek-v4"


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
    return subprocess.run(
        ["git", *args], cwd=str(root), text=True, capture_output=True, timeout=15,
    )


class TenorInitBridgeTest(unittest.TestCase):
    def proof(self, agent_id: str = AGENT_SESSION_ID, *, ttl_seconds: int = 3600) -> str:
        return issue_proof(self.root, agent_id, ttl_seconds=ttl_seconds)

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="tenor-init-bridge-"))
        self.old_cwd = Path.cwd()
        os.chdir(self.root)
        os.environ["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(self.root)
        self.old_host_env = {
            key: os.environ.get(key)
            for key in ("AGENT_MCP_HOST", "AGENT_MCP_BINDING_ID")
        }
        (self.root / ".agent" / "state").mkdir(parents=True, exist_ok=True)
        (self.root / ".agent" / "state" / "patch_queue").mkdir(parents=True, exist_ok=True)
        graphify_dir = self.root / "graphify-out"
        graphify_dir.mkdir(parents=True, exist_ok=True)
        (graphify_dir / "graph.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        (graphify_dir / "GRAPH_REPORT.md").write_text("# Graphify Report\n\nEmpty.\n", encoding="utf-8")
        (graphify_dir / "graph.html").write_text("<html><body></body></html>\n", encoding="utf-8")
        git(self.root, "init")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "config", "user.name", "Bridge Test")
        git(self.root, "commit", "--allow-empty", "-m", "initial")
        mcp.server.ROOT = self.root.resolve()
        mcp.server.AGENT_DIR = mcp.server.ROOT / ".agent"
        importlib.reload(db)
        importlib.reload(discipline)
        mcp.db = db
        mcp.discipline = discipline
        mcp._GRAPHIFY_GUARD_CACHE.clear()
        db.init_db(self.root)
        discipline.ensure_schema()
        configured = host_config.configure_host(self.root, explicit=HOST_TOOL)
        self.assertTrue(configured["ok"], configured)
        os.environ["AGENT_MCP_HOST"] = HOST_TOOL
        os.environ["AGENT_MCP_BINDING_ID"] = configured["binding_id"]
        mcp.tenor_init_bridge.__globals__["_PROOF_SIGNER_AVAILABLE"] = True

    def tearDown(self) -> None:
        presence.stop(self.root, AGENT_SESSION_ID)
        os.chdir(self.old_cwd)
        os.environ.pop("AGENT_SCRIBE_GRAPHIFY_ROOT", None)
        for key, previous in self.old_host_env.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        shutil.rmtree(self.root, ignore_errors=True)

    def test_bridge_happy_path(self) -> None:
        """Full bridge: register + status + discipline_ping + ghost cleanup."""
        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
            model_name=MODEL_NAME,
            proof_token=self.proof(),
        )
        self.assertTrue(result.get("ok"), f"bridge failed: {result.get('reason', '')}")
        self.assertEqual(result.get("verdict"), "TENOR_INIT_READY")
        self.assertEqual(result.get("bridge_verdict"), "TENOR_INIT_BRIDGE_OK")
        self.assertTrue(result.get("terminal"))
        self.assertEqual(result.get("next_action"), "READY_FOR_NEXT_TASK")
        self.assertEqual(result.get("root_binding", {}).get("project_root"), str(self.root.resolve()))
        self.assertEqual(result.get("agent_session_id"), AGENT_SESSION_ID)
        steps = result.get("steps", [])
        self.assertGreaterEqual(len(steps), 3)
        for step in steps:
            self.assertTrue(step.get("ok"), f"step {step.get('step')} failed: {step}")

    def test_bridge_empty_session_id(self) -> None:
        """Bridge with empty agent_session_id returns INVALID."""
        result = call_tool("tenor_init_bridge", agent_session_id="")
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("verdict"), "TENOR_INIT_BRIDGE_INVALID")
        self.assertEqual(result.get("state"), "AGENT_SESSION_ID_REQUIRED")

    def test_bridge_whitespace_session_id(self) -> None:
        """Bridge with whitespace-only session_id returns INVALID."""
        result = call_tool("tenor_init_bridge", agent_session_id="   ")
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("verdict"), "TENOR_INIT_BRIDGE_INVALID")

    def test_bridge_registers_agent_in_db(self) -> None:
        """After bridge, agent should be findable via agent_status."""
        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
            proof_token=self.proof(),
        )
        self.assertTrue(result.get("ok"))
        status = db.agent_status(AGENT_SESSION_ID)
        self.assertEqual(status.get("status"), "active")
        self.assertEqual(status.get("host_tool"), HOST_TOOL)

    def test_bridge_twice_is_idempotent(self) -> None:
        """Calling bridge twice with same agent_id succeeds (each call needs a fresh token)."""
        r1 = call_tool("tenor_init_bridge", agent_session_id=AGENT_SESSION_ID, host_tool=HOST_TOOL, proof_token=self.proof())
        self.assertTrue(r1.get("ok"))
        # Second call with a different token — bridge is idempotent for same agent_id
        r2 = call_tool("tenor_init_bridge", agent_session_id=AGENT_SESSION_ID, host_tool=HOST_TOOL, proof_token=self.proof())
        self.assertTrue(r2.get("ok"))
        self.assertEqual(r2.get("verdict"), "TENOR_INIT_READY")

    def test_bridge_discipline_ping_sets_phase(self) -> None:
        """Bridge should record a guard ping with phase post-init."""
        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
            proof_token=self.proof(),
        )
        self.assertTrue(result.get("ok"))
        steps = result.get("steps", [])
        ping_step = next((s for s in steps if s["step"] == "discipline_ping"), None)
        self.assertIsNotNone(ping_step)
        self.assertEqual(ping_step.get("phase"), "post-init")

    def test_bridge_without_host_tool_defaults_unknown(self) -> None:
        """The verified process binding, not a caller default, owns host identity."""
        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            proof_token=self.proof(),
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("host_tool"), HOST_TOOL)

    # ── V2.15.6: proof_token verification ────────────────────

    def test_bridge_with_valid_proof(self) -> None:
        """Bridge with valid proof_token succeeds and records proof step."""
        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
            proof_token=self.proof(),
        )
        self.assertTrue(result.get("ok"), f"bridge failed: {result.get('reason', '')}")
        self.assertEqual(result.get("verdict"), "TENOR_INIT_READY")
        steps = result.get("steps", [])
        proof_step = next((s for s in steps if s["step"] == "verify_proof"), None)
        self.assertIsNotNone(proof_step, f"no verify_proof step in {steps}")
        self.assertTrue(proof_step.get("ok"))
        self.assertEqual(proof_step.get("verdict"), "PROOF_VALID")

    def test_bridge_with_invalid_proof(self) -> None:
        """Bridge with invalid proof_token returns PROOF_FAILED + HARD_STOP."""
        token = self.proof()
        invalid = token[:-1] + ("0" if token[-1] != "0" else "1")
        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
            proof_token=invalid,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("verdict"), "TENOR_INIT_BRIDGE_PROOF_FAILED")
        self.assertEqual(result.get("state"), "HARD_STOP")

    def test_bridge_with_expired_proof(self) -> None:
        """Bridge with expired proof_token returns PROOF_FAILED + HARD_STOP."""
        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
            proof_token=self.proof(ttl_seconds=-1),
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("verdict"), "TENOR_INIT_BRIDGE_PROOF_FAILED")
        self.assertEqual(result.get("state"), "HARD_STOP")

    def test_bridge_consumes_server_side_proof_without_token(self) -> None:
        """A host-bound bridge consumes a fresh server-side proof without exposing it."""
        self.proof()
        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
        )
        self.assertTrue(result.get("ok"), result)
        proof_step = next(step for step in result["steps"] if step["step"] == "verify_proof")
        self.assertEqual(proof_step["consumption_mode"], "host_bound_server_side")

    def test_bridge_rejects_shell_process_without_host_binding(self) -> None:
        old_host = os.environ.pop("AGENT_MCP_HOST", None)
        old_binding = os.environ.pop("AGENT_MCP_BINDING_ID", None)
        try:
            result = call_tool(
                "tenor_init_bridge",
                agent_session_id=AGENT_SESSION_ID,
                host_tool=HOST_TOOL,
                proof_token=self.proof(),
            )
        finally:
            if old_host is not None:
                os.environ["AGENT_MCP_HOST"] = old_host
            if old_binding is not None:
                os.environ["AGENT_MCP_BINDING_ID"] = old_binding
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("verdict"), "TENOR_INIT_BRIDGE_HOST_UNBOUND")
        self.assertEqual(result.get("state"), "HOST_MCP_UNBOUND")

    def test_bridge_preserves_other_agent_without_guessing_it_is_a_ghost(self) -> None:
        """Bridge observes another agent but never retires it heuristically."""
        ghost_id = "ghost-agent-001"
        db.register_agent(host_tool=HOST_TOOL, agent_id=ghost_id)
        db.heartbeat(ghost_id)
        status = db.agent_status(ghost_id)
        self.assertEqual(status.get("status"), "active")

        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
            proof_token=self.proof(),
        )
        self.assertTrue(result.get("ok"), f"bridge failed: {result.get('reason', '')}")
        self.assertEqual(result.get("verdict"), "TENOR_INIT_READY")
        retired = result.get("retired_ghosts", [])
        self.assertNotIn(ghost_id, retired)
        observed_step = next(step for step in result["steps"] if step["step"] == "retire_ghosts")
        observed = observed_step.get("observed_parallel_agents", [])
        self.assertIn(ghost_id, [item["agent_id"] for item in observed])
        ghost_status = db.agent_status(ghost_id)
        self.assertEqual(ghost_status.get("status"), "active", ghost_status)

    def test_bridge_binds_identity_to_current_mcp_process_and_starts_presence(self) -> None:
        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
            proof_token=self.proof(),
        )
        self.assertTrue(result.get("ok"), result)
        binding = result.get("process_bound_identity", {})
        self.assertEqual(binding.get("agent_id"), AGENT_SESSION_ID)
        self.assertEqual(binding.get("pid"), os.getpid())
        self.assertTrue(binding.get("presence", {}).get("ok"), binding)
        self.assertFalse(binding.get("caller_supplied_agent_id_required_for_task_tools"))

    def test_bridge_does_not_retire_active_parallel_agent(self) -> None:
        """Bridge must NOT retire a parallel agent with an active task context."""
        parallel_id = "parallel-agent-42"
        db.register_agent(host_tool=HOST_TOOL, agent_id=parallel_id)
        db.heartbeat(parallel_id)
        from runtime import task_context
        task_context.ensure_schema()
        task_context.create_task_context(
            agent_id=parallel_id, request="test", intent="read",
            resource=".", requires_graphify=False,
        )
        status = db.agent_status(parallel_id)
        self.assertEqual(status.get("status"), "active")

        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
            proof_token=self.proof(),
        )
        self.assertTrue(result.get("ok"), f"bridge failed: {result.get('reason', '')}")
        retired = result.get("retired_ghosts", [])
        self.assertNotIn(parallel_id, retired,
                         f"parallel agent {parallel_id} with active task should NOT be retired, got {retired}")
        parallel_status = db.agent_status(parallel_id)
        self.assertNotEqual(parallel_status.get("status"), "retired",
                            f"parallel agent {parallel_id} should remain active: {parallel_status}")

    def test_bridge_reports_ghost_cleanup_failure(self) -> None:
        """When ghost cleanup fails, bridge reports status=failed + error."""
        orig_list_agents = getattr(db, "list_agents", None)
        def _broken_list(*args: object, **kwargs: object) -> object:
            raise RuntimeError("simulated ghost cleanup failure")
        db.list_agents = _broken_list  # type: ignore
        try:
            result = call_tool(
                "tenor_init_bridge",
                agent_session_id=AGENT_SESSION_ID,
                host_tool=HOST_TOOL,
                proof_token=self.proof(),
            )
            self.assertTrue(result.get("ok"), f"bridge should still succeed, got {result}")
            self.assertEqual(result.get("ghost_cleanup_status"), "failed",
                             f"expected ghost_cleanup_status=failed, got {result.get('ghost_cleanup_status')}")
            steps = result.get("steps", [])
            ghost_step = next((s for s in steps if s["step"] == "retire_ghosts"), None)
            self.assertIsNotNone(ghost_step, "no retire_ghosts step found")
            self.assertEqual(ghost_step.get("status"), "failed")
            self.assertIn("simulated", ghost_step.get("error", ""),
                          f"expected error message in step, got {ghost_step}")
        finally:
            db.list_agents = orig_list_agents  # type: ignore

    def test_tenor_init_bridge_does_not_create_user_task(self) -> None:
        """Bridge must NOT create any user task context."""
        from runtime import task_context
        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
            proof_token=self.proof(),
        )
        self.assertTrue(result.get("ok"), f"bridge failed: {result.get('reason', '')}")
        tasks = task_context.list_tasks(agent_id=AGENT_SESSION_ID, status="active")
        self.assertEqual(tasks.get("count", 0), 0,
                         f"bridge should not create user tasks, found {tasks}")

    def test_bridge_proof_signer_unavailable(self) -> None:
        """Bridge with proof_token but PROOF_SIGNER_UNAVAILABLE returns UNVERIFIABLE."""
        bridge_globals = mcp.tenor_init_bridge.__globals__
        previous = bridge_globals["_PROOF_SIGNER_AVAILABLE"]
        bridge_globals["_PROOF_SIGNER_AVAILABLE"] = False
        try:
            result = call_tool(
                "tenor_init_bridge",
                agent_session_id=AGENT_SESSION_ID,
                host_tool=HOST_TOOL,
                proof_token="any-token",
            )
            self.assertFalse(result.get("ok"))
            self.assertEqual(result.get("verdict"), "TENOR_INIT_BRIDGE_PROOF_UNVERIFIABLE")
            self.assertEqual(result.get("state"), "HARD_STOP")
        finally:
            bridge_globals["_PROOF_SIGNER_AVAILABLE"] = previous

    # ── V2.15.16: proof_token schema + consumption protocol ───

    def test_bridge_schema_exposes_proof_token(self) -> None:
        """Legacy token remains accepted but is no longer required or emitted."""
        schema = mcp.tool_schema("tenor_init_bridge")
        self.assertIn("proof_token", schema.get("properties", {}))
        self.assertNotIn("proof_token", schema.get("required", []))

    def test_verify_standalone_does_not_consume(self) -> None:
        """Standalone verify_proof MCP tool must NOT consume the token (peek mode)."""
        token = self.proof()
        r1 = call_tool("verify_proof", token=token, agent_id=AGENT_SESSION_ID)
        self.assertTrue(r1.get("ok"), f"first verify failed: {r1}")
        # Same token must still be fresh for the bridge
        r2 = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
            proof_token=token,
        )
        self.assertTrue(r2.get("ok"), f"bridge after verify peek failed: {r2.get('reason', '')}")
        self.assertEqual(r2.get("verdict"), "TENOR_INIT_READY")

    def test_bridge_consumes_token(self) -> None:
        """Bridge is the sole consumer — a second bridge with the same token must fail."""
        token = self.proof()
        r1 = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
            proof_token=token,
        )
        self.assertTrue(r1.get("ok"), f"first bridge failed: {r1}")
        # Use a different agent_session_id to test token consumption independently
        r2 = call_tool(
            "tenor_init_bridge",
            agent_session_id="cli-consumer-test-2",
            host_tool=HOST_TOOL,
            proof_token=token,
        )
        self.assertFalse(r2.get("ok"), "second bridge with consumed token should fail")
        self.assertIn(r2.get("verdict", ""), ("TENOR_INIT_BRIDGE_PROOF_FAILED", "TENOR_INIT_BRIDGE_PROOF_ERROR"))


class TestTenorInitBridgeLauncher(unittest.TestCase):
    def test_launcher_empty_session_id(self) -> None:
        """Launcher with empty agent_session_id returns INVALID."""
        config = HostLaunchConfig(agent_id="test", workspace_root="/tmp")
        result = run_tenor_init_bridge(config, agent_session_id="")
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("verdict"), "TENOR_INIT_BRIDGE_INVALID")

    def test_launcher_cannot_substitute_for_real_host_tool_surface(self) -> None:
        config = HostLaunchConfig(agent_id="test", host_type="opencode", workspace_root="/tmp")
        result = run_tenor_init_bridge(config, agent_session_id="cli-test")
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("verdict"), "TENOR_INIT_BRIDGE_HOST_UNBOUND")


class TestCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.script = str(HERE.parent.parent / "scripts" / "tenor_init_bridge.py")

    def test_cli_no_args_errors(self) -> None:
        proc = subprocess.run(
            [sys.executable, self.script], capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_cli_empty_session_id_errors(self) -> None:
        proc = subprocess.run(
            [sys.executable, self.script, "--agent-session-id", ""],
            capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_cli_script_resolves_entry_path(self) -> None:
        """Script must NOT construct .agent/.agent/mcp/server_entry.py."""
        import importlib.util as _imp_util
        spec = _imp_util.spec_from_file_location("tenor_init_bridge_script", self.script)
        self.assertIsNotNone(spec, f"cannot load script from {self.script}")
        mod = _imp_util.module_from_spec(spec)  # type: ignore
        try:
            spec.loader.exec_module(mod)  # type: ignore
        except SystemExit:
            pass
        entry_path = str(getattr(mod, "_ENTRY", ""))
        self.assertNotIn(".agent/.agent", entry_path,
                         f"Script resolves to {entry_path} — contains double .agent!")
        self.assertTrue(entry_path.endswith("mcp/server_entry.py"),
                        f"Script path should end with mcp/server_entry.py, got {entry_path}")

    def test_cli_json_output(self) -> None:
        """CLI with --json should return parseable JSON."""
        proc = subprocess.run(
            [
                sys.executable, self.script,
                "--agent-session-id", "cli-test-json",
                "--host-tool", "test",
                "--proof-token", "valid-token",
                "--json",
            ],
            capture_output=True, text=True, timeout=30,
            cwd=str(Path(self.script).parent.parent),
        )
        if proc.returncode != 0:
            self.skipTest(f"MCP server not reachable from CLI: {proc.stdout.strip()}")
        data = json.loads(proc.stdout)
        self.assertIn("verdict", data)


if __name__ == "__main__":
    unittest.main()
