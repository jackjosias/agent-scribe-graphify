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
from unittest import mock

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))
ROOT_DIR = MCP_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import server_ext as mcp
from host_adapter import host_config, instructions as host_instructions
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


def scribe_backend_globals() -> dict[str, Any]:
    current = mcp.server.TOOLS["scribe_query"]
    visited: set[int] = set()
    while callable(current) and id(current) not in visited:
        visited.add(id(current))
        globals_dict = getattr(current, "__globals__", {})
        if "_BASE_SCRIBE_QUERY" in globals_dict:
            return globals_dict
        closure = {
            name: cell.cell_contents
            for name, cell in zip(current.__code__.co_freevars, current.__closure__ or ())
        }
        current = closure.get("current_scribe")
    raise AssertionError("unable to locate the wrapped SCRIBE backend")


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
        configured = host_config.configure_host(self.root, explicit="opencode")
        self.assertTrue(configured["ok"], configured)

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

    def assert_no_instruction_residue(self, target: Path) -> None:
        self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])
        self.assertFalse(target.with_name(f".{target.name}.agent-instructions.lock").exists())

    def test_policy_requires_complete_v216_surface(self) -> None:
        required = HostPolicy(self.root).get_required_tools()
        for tool in (
            "file_hash", "tenor_init_bridge", "portability_check",
            "graphify_required_check", "graphify_project_build",
            "tenor_task_start", "tenor_apply_changeset", "tenor_activity",
            "tenor_task_control",
        ):
            self.assertIn(tool, required)

    def test_policy_marks_missing_mcp_as_unsafe(self) -> None:
        policy = HostPolicy(self.root)
        self.assertFalse(policy.validate_mcp_tools(["some_other_tool"]))
        verdict = policy.decide_host_safety_level(["some_other_tool"], {"workspace_write": True})
        self.assertEqual(verdict, HostVerdict.UNSAFE)

    def test_opencode_native_mutation_surfaces_are_denied(self) -> None:
        config = host_config._load_jsonc((self.root / "opencode.jsonc").read_text(encoding="utf-8"))
        self.assertEqual(config["permission"]["edit"], "deny")
        self.assertEqual(config["permission"]["bash"]["*"], "deny")
        self.assertEqual(
            config["permission"]["bash"][".agent/workflow/scribe/scribe tenor-init --type cli --host opencode"],
            "allow",
        )

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
        self.assertIn("tenor_task_start", instructions)
        self.assertIn("tenor_apply_changeset", instructions)
        self.assertIn("all-or-nothing", instructions)
        self.assertIn("prose-only", instructions)
        self.assertIn("--host opencode", instructions)
        self.assertNotIn("--host <host-id>", instructions)
        self.assertIn("Never substitute `--host auto`", instructions)
        self.assertIn("TENOR_INIT_TERMINAL=false", instructions)
        self.assertIn("do not summarize, ask the user, wait, or stop", instructions)
        self.assertIn("Only `TENOR_INIT_READY`", instructions)

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
        self.assert_no_instruction_residue(target)

    def test_atomic_instruction_replace_retries_windows_sharing_violation(self) -> None:
        target = self.root / "AGENTS.md"
        attempts = 0
        real_replace = os.replace

        def transient_replace(source: Path | str, destination: Path | str) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                error = PermissionError("transient Windows sharing violation")
                error.winerror = 5
                raise error
            real_replace(source, destination)

        with (
            mock.patch.object(host_instructions._impl, "IS_WINDOWS", True),
            mock.patch.object(host_instructions._impl.os, "replace", transient_replace),
            mock.patch.object(host_instructions._impl.time, "sleep") as sleeper,
        ):
            host_instructions._impl._atomic_text_write(target, "portable\n")

        self.assertEqual(attempts, 3)
        self.assertEqual(sleeper.call_count, 2)
        self.assertEqual(target.read_bytes(), b"portable\n")

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
        self.assert_no_instruction_residue(target)

    def test_multi_process_install_instructions_is_collision_proof(self) -> None:
        target = self.root / "AGENTS.md"
        target.write_text("Manual project rule.\n", encoding="utf-8")
        script = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "from host_adapter.instructions import install_host_instructions\n"
            "target = Path(sys.argv[1])\n"
            "root = Path(sys.argv[2])\n"
            "result = install_host_instructions(target, 'opencode', root)\n"
            "print(json.dumps(result, sort_keys=True))\n"
            "raise SystemExit(0 if result.get('ok') else 1)\n"
        )
        environment = dict(os.environ)
        python_paths = [str(ROOT_DIR), str(MCP_DIR)]
        existing = environment.get("PYTHONPATH", "")
        if existing:
            python_paths.append(existing)
        environment["PYTHONPATH"] = os.pathsep.join(python_paths)

        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(target), str(self.root)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
            for _ in range(12)
        ]
        failures: list[dict[str, Any]] = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=45)
            if process.returncode != 0:
                failures.append({"returncode": process.returncode, "stdout": stdout, "stderr": stderr})

        self.assertEqual(failures, [])
        content = target.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("Manual project rule."))
        self.assertEqual(content.count("auto-guard:start"), 1)
        self.assertEqual(content.count("auto-guard:end"), 1)
        self.assertTrue(verify_instruction_installation(target))
        self.assert_no_instruction_residue(target)

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

    def test_guard_requires_bounded_discovery_before_first_write_lease(self) -> None:
        agent_id = "test-agent"
        config = HostLaunchConfig(agent_id=agent_id, host_type="opencode", workspace_root=self.root)
        call_tool("register_agent", agent_id=agent_id, host_tool="opencode")
        before = call_tool("before_task", agent_id=agent_id, request="fix bug", intent="write", resource="code.py")
        task_id, token = before["task_id"], before["context_token"]

        irrelevant = mcp.server.ok({
            "ok": False,
            "verdict": "SCRIBE_CONTEXT_IRRELEVANT_FOR_WRITE",
            "result": {"returncode": 0, "stdout": "unrelated historical result"},
        })
        with mock.patch.dict(
            scribe_backend_globals(),
            {"_BASE_SCRIBE_QUERY": mock.Mock(return_value=irrelevant)},
        ):
            scribe = call_tool(
                "scribe_query",
                agent_id=agent_id,
                task_id=task_id,
                context_token=token,
                query="some logic",
            )
        self.assertEqual(scribe["verdict"], "SCRIBE_CONTEXT_MISS_FOR_WRITE")
        call_tool("graphify_query", agent_id=agent_id, task_id=task_id, context_token=token, query="some logic", resource="code.py")

        blocked = run_pre_action_guard(config, "fix bug", "write", "code.py", "claim_resource", task_id, token)
        self.assertEqual(blocked.get("verdict"), "TASK_DISCOVERY_BASE_HASH_REQUIRED")
        self.assertEqual(blocked.get("must_call", {}).get("tool"), "file_hash")

        current_hash = call_tool("file_hash", resource="code.py")["hash"]
        routed = call_tool(
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

        recorded = call_tool(
            "record_task_discovery",
            agent_id=agent_id,
            task_id=task_id,
            context_token=token,
            resource="code.py",
            base_hash=current_hash,
            summary="Inspected code.py and selected the smallest correction that reuses the existing host gate.",
            evidence=(
                "code.py is the exact target. Graphify was queried for dependencies and blast radius; "
                "the host-adapter workflow and regression tests were inspected before ownership."
            ),
        )
        self.assertEqual(recorded["verdict"], "TASK_DISCOVERY_RECORDED")
        self.assertEqual(recorded["base_hash"], current_hash)

        result = run_pre_action_guard(config, "fix bug", "write", "code.py", "claim_resource", task_id, token)
        self.assertEqual(result.get("verdict"), "PRE_ACTION_GUARD_OK")
        self.assertEqual(result.get("state"), "ACTION_LEASE_ISSUED")


if __name__ == "__main__":
    unittest.main()
