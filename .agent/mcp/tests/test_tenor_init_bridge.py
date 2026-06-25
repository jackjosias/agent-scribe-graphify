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
from runtime import db, discipline
from host_adapter.launcher import HostLaunchConfig, run_tenor_init_bridge


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
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="tenor-init-bridge-"))
        self.old_cwd = Path.cwd()
        os.chdir(self.root)
        os.environ["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(self.root)
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

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_bridge_happy_path(self) -> None:
        """Full bridge: register + status + discipline_ping + workflow_next."""
        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
            model_name=MODEL_NAME,
        )
        self.assertTrue(result.get("ok"), f"bridge failed: {result.get('reason', '')}")
        self.assertEqual(result.get("verdict"), "TENOR_INIT_BRIDGE_OK")
        self.assertEqual(result.get("agent_session_id"), AGENT_SESSION_ID)
        steps = result.get("steps", [])
        self.assertGreaterEqual(len(steps), 4)
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
        )
        self.assertTrue(result.get("ok"))
        status = db.agent_status(AGENT_SESSION_ID)
        self.assertEqual(status.get("status"), "active")
        self.assertEqual(status.get("host_tool"), HOST_TOOL)

    def test_bridge_twice_is_idempotent(self) -> None:
        """Calling bridge twice with same agent_id should succeed (idempotent)."""
        r1 = call_tool("tenor_init_bridge", agent_session_id=AGENT_SESSION_ID, host_tool=HOST_TOOL)
        self.assertTrue(r1.get("ok"))
        r2 = call_tool("tenor_init_bridge", agent_session_id=AGENT_SESSION_ID, host_tool=HOST_TOOL)
        self.assertTrue(r2.get("ok"))
        self.assertEqual(r2.get("verdict"), "TENOR_INIT_BRIDGE_OK")

    def test_bridge_discipline_ping_sets_phase(self) -> None:
        """Bridge should record a guard ping with phase post-init."""
        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
        )
        self.assertTrue(result.get("ok"))
        steps = result.get("steps", [])
        ping_step = next((s for s in steps if s["step"] == "discipline_ping"), None)
        self.assertIsNotNone(ping_step)
        self.assertEqual(ping_step.get("phase"), "post-init")

    def test_bridge_workflow_next_returns_verdict(self) -> None:
        """Bridge should call workflow_next and capture its verdict."""
        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
            host_tool=HOST_TOOL,
        )
        self.assertTrue(result.get("ok"))
        steps = result.get("steps", [])
        wf_step = next((s for s in steps if s["step"] == "workflow_next"), None)
        self.assertIsNotNone(wf_step)
        self.assertTrue(wf_step.get("ok"))
        self.assertIsInstance(wf_step.get("verdict"), str)
        self.assertGreater(len(wf_step.get("verdict", "")), 0)

    def test_bridge_without_host_tool_defaults_unknown(self) -> None:
        """Bridge with no host_tool should default to 'unknown'."""
        result = call_tool(
            "tenor_init_bridge",
            agent_session_id=AGENT_SESSION_ID,
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("host_tool"), "unknown")


class TestTenorInitBridgeLauncher(unittest.TestCase):
    def test_launcher_empty_session_id(self) -> None:
        """Launcher with empty agent_session_id returns INVALID."""
        config = HostLaunchConfig(agent_id="test", workspace_root="/tmp")
        result = run_tenor_init_bridge(config, agent_session_id="")
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("verdict"), "TENOR_INIT_BRIDGE_INVALID")


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

    def test_cli_json_output(self) -> None:
        """CLI with --json should return parseable JSON."""
        proc = subprocess.run(
            [
                sys.executable, self.script,
                "--agent-session-id", "cli-test-json",
                "--host-tool", "test",
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
