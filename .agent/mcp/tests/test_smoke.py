#!/usr/bin/env python3
"""Smoke tests — quick pass-fail check for core functionality.

Runs in <10s.  Designed to be executed after a build or deployment to
decide whether the full test suite is worth running.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
SERVER_ENTRY = str(MCP_DIR / "server_entry.py")

PROJECT_ROOT = Path(os.environ.get(
    "AGENT_SCRIBE_GRAPHIFY_ROOT",
    Path(__file__).resolve().parents[4],
)).resolve()

INTERNAL_TOOL_SUBSET = {
    "session_status",
    "register_agent",
    "agent_status",
    "file_hash",
    "list_tasks",
    "task_status",
    "workspace_audit",
    "pre_action_guard",
    "workflow_next",
    "propose_patch",
    "apply_patch",
    "finish_task",
    "scribe_query",
    "graphify_query",
    "lease_extend",
    "resource_lock_claim",
    "resource_lock_release",
    "resource_lock_status",
    "resource_lock_heartbeat",
    "scribe_commit_gate_status",
    "scribe_commit_gate_resolve",
    "portability_check",
    "bootstrap",
    "heartbeat",
    "list_agents",
    "discipline_ping",
    "before_task",
    "claim_resource",
    "delete_resource",
    "resume_task_context",
    "graphify_required_check",
    "verify_proof",
}

PUBLIC_TOOLS = {
    "file_hash",
    "tenor_init_bridge",
    "portability_check",
    "graphify_required_check",
    "graphify_project_build",
    "tenor_task_start",
    "tenor_apply_changeset",
    "tenor_activity",
    "tenor_task_control",
}

MIN_INTERNAL_TOOLS = 42
TIMEOUT = 15


def _load_server_ext():
    from importlib import util as importutil
    sys.path.insert(0, str(MCP_DIR))
    spec = importutil.spec_from_file_location(
        "server_ext", str(MCP_DIR / "server_ext.py"),
    )
    mod = importutil.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class SmokeTestImport(unittest.TestCase):
    """Level 0 — module loads without syntax or import errors."""

    @classmethod
    def setUpClass(cls):
        cls._mod = _load_server_ext()

    def test_server_module_imports(self) -> None:
        import server as srv
        tools = getattr(srv, "TOOLS", None)
        self.assertIsNotNone(tools, "server.TOOLS not found")
        self.assertGreaterEqual(len(tools), MIN_INTERNAL_TOOLS)

    def test_tool_names(self) -> None:
        import server as srv
        names = set(srv.TOOLS.keys())
        missing = INTERNAL_TOOL_SUBSET - names
        self.assertSetEqual(missing, set(),
                            f"Missing tools: {sorted(missing)}")

    def test_strict_lease_tools_present(self) -> None:
        strict = getattr(self._mod, "_STRICT_LEASE_TOOLS", set())
        self.assertIn("propose_patch", strict)
        self.assertIn("apply_patch", strict)
        self.assertIn("finish_task", strict)


    def test_resource_lock_claim_schema_requires_task_id_and_context_token(self) -> None:
        schema = self._mod.tool_schema("resource_lock_claim")
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        self.assertIn("task_id", props)
        self.assertIn("context_token", props)
        self.assertIn("task_id", required)
        self.assertIn("context_token", required)
        self.assertFalse(schema.get("additionalProperties"))

    def test_apply_patch_schema_exposes_task_context_and_action_lease(self) -> None:
        schema = self._mod.tool_schema("apply_patch")
        props = schema.get("properties", {})
        for field in ("agent_id", "patch_id", "task_id", "context_token", "action_lease_id"):
            self.assertIn(field, props)
        self.assertFalse(schema.get("additionalProperties"))

    def test_propose_patch_schema_exposes_task_context_and_action_lease(self) -> None:
        schema = self._mod.tool_schema("propose_patch")
        props = schema.get("properties", {})
        for field in ("agent_id", "target", "base_hash", "diff_text", "task_id", "context_token", "action_lease_id"):
            self.assertIn(field, props)
        self.assertFalse(schema.get("additionalProperties"))

    def test_resource_lock_schema_contracts_are_aligned_with_runtime(self) -> None:
        release = self._mod.tool_schema("resource_lock_release")
        heartbeat = self._mod.tool_schema("resource_lock_heartbeat")
        status = self._mod.tool_schema("resource_lock_status")
        self.assertEqual(set(release.get("required", [])), {"agent_id", "resource"})
        self.assertNotIn("task_id", release.get("properties", {}))
        self.assertNotIn("context_token", release.get("properties", {}))
        self.assertEqual(set(heartbeat.get("required", [])), {"agent_id", "resource"})
        self.assertNotIn("task_id", heartbeat.get("properties", {}))
        self.assertNotIn("context_token", heartbeat.get("properties", {}))
        self.assertEqual(set(status.get("required", [])), {"resource"})
        self.assertEqual(set(status.get("properties", {}).keys()), {"resource"})


class SmokeTestServer(unittest.TestCase):
    """Level 1 — server starts, accepts initialize, handles basic calls."""

    def setUp(self) -> None:
        self.proc: subprocess.Popen | None = None

    def _start(self) -> None:
        env = os.environ.copy()
        env["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(PROJECT_ROOT)
        self.proc = subprocess.Popen(
            [sys.executable, SERVER_ENTRY],
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )

    def _write(self, req: dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()

    def _read(self) -> dict[str, Any]:
        assert self.proc and self.proc.stdout
        r, _, _ = select.select([self.proc.stdout], [], [], TIMEOUT)
        if not r:
            raise TimeoutError(
                f"No response within {TIMEOUT}s\n"
                f"stderr:{self._stderr_snapshot()}")
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError(
                f"stdout closed\nstderr:{self._stderr_snapshot()}")
        return json.loads(line.strip())

    def _stderr_snapshot(self) -> str:
        if self.proc and self.proc.stderr:
            r, _, _ = select.select([self.proc.stderr], [], [], 0.5)
            if r:
                return "\n".join(
                    line.strip()
                    for line in self.proc.stderr.readlines()
                )
        return "(empty)"

    def tearDown(self) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    # ── tests ──────────────────────────────────────────────────────────────

    def test_initialize(self) -> None:
        self._start()
        self._write({
            "jsonrpc": "2.0", "id": "smoke-init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "1.0"},
            },
        })
        resp = self._read()
        self.assertIn("result", resp)
        self.assertNotIn("error", resp)
        info = resp["result"].get("serverInfo", {})
        self.assertEqual(info.get("name"), "agent-scribe-graphify")

    def test_session_status(self) -> None:
        self._start()
        self._write({
            "jsonrpc": "2.0", "id": "smoke-init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "1.0"},
            },
        })
        self._read()
        self._write({
            "jsonrpc": "2.0", "id": "smoke-ss",
            "method": "tools/call",
            "params": {
                "name": "session_status",
                "arguments": {},
            },
        })
        resp = self._read()
        self.assertNotIn("error", resp)
        result = resp.get("result", {})
        content = result.get("content", [{}])[0].get("text", "{}")
        data = json.loads(content)
        self.assertTrue(data.get("ok"), f"session_status not ok: {data}")

    def test_register_and_tools_list(self) -> None:
        self._start()
        self._write({
            "jsonrpc": "2.0", "id": "smoke-init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "1.0"},
            },
        })
        self._read()

        self._write({
            "jsonrpc": "2.0", "id": "smoke-reg",
            "method": "tools/call",
            "params": {
                "name": "register_agent",
                "arguments": {
                    "host_tool": "smoke",
                    "agent_id": "smoke-tester",
                },
            },
        })
        resp = self._read()
        self.assertNotIn("error", resp)
        result = resp.get("result", {})
        content = result.get("content", [{}])[0].get("text", "{}")
        data = json.loads(content)
        self.assertTrue(data.get("ok"), f"register not ok: {data}")

        self._write({
            "jsonrpc": "2.0", "id": "smoke-tl",
            "method": "tools/list",
            "params": {},
        })
        resp = self._read()
        self.assertNotIn("error", resp)
        tools = resp.get("result", {}).get("tools", [])
        self.assertEqual(len(tools), len(PUBLIC_TOOLS),
                         f"Unexpected public tool count: {len(tools)}")
        names = {t["name"] for t in tools}
        self.assertSetEqual(names, PUBLIC_TOOLS)

    def test_discipline_ping(self) -> None:
        self._start()
        self._write({
            "jsonrpc": "2.0", "id": "smoke-init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "1.0"},
            },
        })
        self._read()
        self._write({
            "jsonrpc": "2.0", "id": "smoke-dp",
            "method": "tools/call",
            "params": {
                "name": "discipline_ping",
                "arguments": {},
            },
        })
        resp = self._read()
        self.assertNotIn("error", resp)
        result = resp.get("result", {})
        content = result.get("content", [{}])[0].get("text", "{}")
        data = json.loads(content)
        self.assertIn("forbidden", data)
        self.assertIn("AGENT_ID_REQUIRED", data.get("verdict", ""))

    def test_startup_time(self) -> None:
        t0 = time.perf_counter()
        self._start()
        self._write({
            "jsonrpc": "2.0", "id": "smoke-init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "1.0"},
            },
        })
        self._read()
        elapsed = (time.perf_counter() - t0) * 1000.0
        self.assertLess(elapsed, 5_000,
                        f"Startup {elapsed:.0f}ms > 5000ms")
        print(f"\n  startup-to-init: {elapsed:.0f}ms")
