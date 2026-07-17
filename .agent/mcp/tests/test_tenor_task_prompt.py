#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
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
from host_adapter import tenor_task_prompt as ttp
from _strict_cleanup import remove_tree_strict


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


def contains_sections(prompt: str) -> bool:
    indicators = [
        "Avant toute action",
        "tenor_task_start",
        "tenor_apply_changeset",
        "tenor_activity",
        "tenor_task_control",
        "Aucune ecriture directe",
        "SCRIBE et Graphify en interne",
        "base_hash",
        "rollback",
        "HOST_MCP_UNBOUND",
    ]
    return all(indicator in prompt for indicator in indicators)


class TestTenorTaskPromptCore(unittest.TestCase):
    def test_happy_path_defaults(self) -> None:
        result = ttp.generate_task_prompt(task="corrige le bug auth")
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "TENOR_TASK_PROMPT_READY")
        self.assertIn("corrige le bug auth", result["prompt"])
        self.assertIn("STANDARD", result["prompt"])
        self.assertIn("write", result["prompt"])
        self.assertIn("a determiner via Graphify/SCRIBE", result["prompt"])
        self.assertTrue(contains_sections(result["prompt"]))
        self.assertEqual(result["required_first_actions"], ["tenor_task_start"])
        self.assertEqual(result["required_finish_actions"], ["tenor_apply_changeset", "tenor_task_control"])
        self.assertIn("legacy_manual_choreography", result["forbidden"])

    def test_empty_task(self) -> None:
        result = ttp.generate_task_prompt(task="")
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "TENOR_TASK_PROMPT_INVALID")
        self.assertEqual(result["prompt"], "")

    def test_whitespace_task(self) -> None:
        result = ttp.generate_task_prompt(task="   ")
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "TENOR_TASK_PROMPT_INVALID")
        self.assertEqual(result["prompt"], "")

    def test_nano_mode(self) -> None:
        result = ttp.generate_task_prompt(task="fix bug", mode="NANO")
        self.assertTrue(result["ok"])
        self.assertIn("Mode NANO", result["prompt"])
        self.assertIn("tache < 30 min", result["prompt"])

    def test_quick_mode(self) -> None:
        result = ttp.generate_task_prompt(task="fix bug", mode="QUICK")
        self.assertTrue(result["ok"])
        self.assertIn("QUICK", result["prompt"])

    def test_critical_mode(self) -> None:
        result = ttp.generate_task_prompt(task="migrate db", mode="CRITICAL")
        self.assertTrue(result["ok"])
        self.assertIn("Mode CRITICAL", result["prompt"])
        self.assertIn("Validators renforces obligatoires", result["prompt"])

    def test_invalid_mode_falls_to_standard(self) -> None:
        result = ttp.generate_task_prompt(task="fix", mode="ULTRA")
        self.assertTrue(result["ok"])
        self.assertIn("STANDARD", result["prompt"])

    def test_read_intent(self) -> None:
        result = ttp.generate_task_prompt(task="inspect code", intent="read")
        self.assertTrue(result["ok"])
        self.assertIn("read", result["prompt"])

    def test_delete_intent(self) -> None:
        result = ttp.generate_task_prompt(task="remove dead code", intent="delete")
        self.assertTrue(result["ok"])
        self.assertIn("delete", result["prompt"])

    def test_invalid_intent_falls_to_write(self) -> None:
        result = ttp.generate_task_prompt(task="fix", intent="fly")
        self.assertTrue(result["ok"])
        self.assertIn("write", result["prompt"])

    def test_descriptive_fix_alias_is_canonicalized_to_write(self) -> None:
        result = ttp.generate_task_prompt(task="fix auth", intent="fix")
        self.assertTrue(result["ok"])
        self.assertIn("Intent : write.", result["prompt"])
        self.assertNotIn("Intent : fix.", result["prompt"])

    def test_resource_provided(self) -> None:
        result = ttp.generate_task_prompt(task="fix auth", resource="src/auth/login.ts")
        self.assertTrue(result["ok"])
        self.assertIn("src/auth/login.ts", result["prompt"])
        self.assertNotIn("a determiner via Graphify/SCRIBE", result["prompt"])

    def test_resource_empty(self) -> None:
        result = ttp.generate_task_prompt(task="fix auth", resource="")
        self.assertTrue(result["ok"])
        self.assertIn("a determiner via Graphify/SCRIBE", result["prompt"])

    def test_small_model_tier(self) -> None:
        result = ttp.generate_task_prompt(task="fix auth", model_tier="small")
        self.assertTrue(result["ok"])
        self.assertIn("Mode petit modele", result["prompt"])
        self.assertIn("API TENOR compacte", result["prompt"])
        self.assertIn("Aucun Edit/Bash natif", result["prompt"])

    def test_large_model_tier_default(self) -> None:
        result = ttp.generate_task_prompt(task="fix auth", model_tier="large")
        self.assertTrue(result["ok"])
        self.assertNotIn("Mode petit modele", result["prompt"])

    def test_invalid_model_tier_falls_to_large(self) -> None:
        result = ttp.generate_task_prompt(task="fix auth", model_tier="tiny")
        self.assertTrue(result["ok"])
        self.assertNotIn("Mode petit modele", result["prompt"])

    def test_nano_mode_with_small_model(self) -> None:
        result = ttp.generate_task_prompt(
            task="fix", mode="NANO", model_tier="small",
        )
        self.assertTrue(result["ok"])
        self.assertIn("Mode petit modele", result["prompt"])
        self.assertIn("Mode NANO", result["prompt"])
        self.assertTrue(contains_sections(result["prompt"]))

    def test_critical_mode_with_small_model(self) -> None:
        result = ttp.generate_task_prompt(
            task="migrate", mode="CRITICAL", model_tier="small",
        )
        self.assertTrue(result["ok"])
        self.assertIn("Mode petit modele", result["prompt"])
        self.assertIn("Mode CRITICAL", result["prompt"])
        self.assertTrue(contains_sections(result["prompt"]))

    def test_all_fields_present(self) -> None:
        result = ttp.generate_task_prompt(
            task="fix auth",
            mode="STANDARD",
            intent="write",
            resource="src/auth/login.ts",
            model_tier="large",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "TENOR_TASK_PROMPT_READY")
        self.assertIsInstance(result["prompt"], str)
        self.assertGreater(len(result["prompt"]), 100)
        self.assertIsInstance(result["required_first_actions"], list)
        self.assertIsInstance(result["required_finish_actions"], list)
        self.assertIsInstance(result["forbidden"], list)

    def test_lowercase_mode_normalized(self) -> None:
        result = ttp.generate_task_prompt(task="fix", mode="nano")
        self.assertTrue(result["ok"])
        self.assertIn("Mode NANO", result["prompt"])

    def test_full_intents_list(self) -> None:
        for intent in ["read", "write", "delete"]:
            result = ttp.generate_task_prompt(task=f"task for {intent}", intent=intent)
            self.assertTrue(result["ok"], f"failed for intent={intent}")
            self.assertIn(intent, result["prompt"])


class TestTenorTaskPromptMCP(unittest.TestCase):
    def test_mcp_happy_path(self) -> None:
        result = call_tool("tenor_task_prompt", task="fix auth bug", intent="write")
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "TENOR_TASK_PROMPT_READY")
        self.assertIn("fix auth bug", result["prompt"])

    def test_mcp_empty_task(self) -> None:
        result = call_tool("tenor_task_prompt", task="")
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "TENOR_TASK_PROMPT_INVALID")

    def test_mcp_model_tier_small(self) -> None:
        result = call_tool(
            "tenor_task_prompt",
            task="review code",
            model_tier="small",
        )
        self.assertTrue(result["ok"])
        self.assertIn("Mode petit modele", result["prompt"])

    def test_mcp_resource(self) -> None:
        result = call_tool(
            "tenor_task_prompt",
            task="fix bug",
            resource="src/main.ts",
        )
        self.assertTrue(result["ok"])
        self.assertIn("src/main.ts", result["prompt"])

    def test_mcp_critical_mode(self) -> None:
        result = call_tool(
            "tenor_task_prompt",
            task="deploy",
            mode="CRITICAL",
            intent="write",
        )
        self.assertTrue(result["ok"])
        self.assertIn("Mode CRITICAL", result["prompt"])


class TestCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.script = str(
            Path(__file__).resolve().parent.parent.parent
            / "scripts"
            / "tenor_task.py"
        )

    def test_cli_happy(self) -> None:
        import subprocess
        proc = subprocess.run(
            [sys.executable, self.script, "--task", "fix auth bug"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("fix auth bug", proc.stdout)
        self.assertIn("tenor_task_start", proc.stdout)

    def test_cli_empty_task(self) -> None:
        import subprocess
        proc = subprocess.run(
            [sys.executable, self.script, "--task", ""],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ERREUR", proc.stdout)
        self.assertIn("TENOR_TASK_PROMPT_INVALID", proc.stdout)

    def test_cli_json_output(self) -> None:
        import subprocess
        proc = subprocess.run(
            [sys.executable, self.script, "--task", "fix bug", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual(data["verdict"], "TENOR_TASK_PROMPT_READY")
        self.assertIn("fix bug", data["prompt"])

    def test_cli_small_model(self) -> None:
        import subprocess
        proc = subprocess.run(
            [sys.executable, self.script, "--task", "review", "--model-tier", "small"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Mode petit modele", proc.stdout)

    def test_cli_nano_mode(self) -> None:
        import subprocess
        proc = subprocess.run(
            [sys.executable, self.script, "--task", "fix", "--mode", "NANO"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Mode NANO", proc.stdout)

    def test_cli_resource(self) -> None:
        import subprocess
        proc = subprocess.run(
            [sys.executable, self.script, "--task", "fix", "--resource", "src/main.ts"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("src/main.ts", proc.stdout)


class TestE2ERealRuntime(unittest.TestCase):
    """Validates that tenor_task_prompt loads in a real project runtime
    without artificial .agent/ in sys.path — the exact scenario that was
    broken in V2.15.4 field testing.

    Uses the --list-tools and --call flags (not stdio MCP protocol) for
    direct, isolated subprocess calls."""

    def setUp(self) -> None:
        import shutil
        import subprocess
        import tempfile
        self._tmpdir = Path(tempfile.mkdtemp(prefix="agent-e2e-real-"))
        self._agent_src = HERE.parent.parent
        shutil.copytree(
            str(self._agent_src),
            str(self._tmpdir / ".agent"),
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "state"),
        )
        self._entry = str(self._tmpdir / ".agent" / "mcp" / "server_entry.py")
        # git init (server_ext.py may probe git repo)
        subprocess.run(["git", "init"], cwd=str(self._tmpdir), capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "e2e@test"],
            cwd=str(self._tmpdir), capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "E2E"],
            cwd=str(self._tmpdir), capture_output=True,
        )
        Path(self._tmpdir, "README.md").write_text("# e2e\n")
        subprocess.run(
            ["git", "add", "."], cwd=str(self._tmpdir), capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(self._tmpdir), capture_output=True,
        )
        from runtime import graphify_readiness, installation_state

        prepared = installation_state.ensure_fresh_installation_state(self._tmpdir)
        self.assertTrue(prepared["ok"], prepared)
        self.assertTrue(installation_state.finalize_installation_state(self._tmpdir)["ok"])
        self.assertTrue(graphify_readiness.write_smoke_fixture(self._tmpdir)["ok"])
        self._runtime_env = {
            **os.environ,
            "AGENT_SCRIBE_GRAPHIFY_ROOT": str(self._tmpdir),
            graphify_readiness.FIXTURE_ENV: "1",
        }

    def tearDown(self) -> None:
        if self._tmpdir and self._tmpdir.exists():
            remove_tree_strict(self._tmpdir)

    def test_e2e_tool_listed(self) -> None:
        import subprocess
        proc = subprocess.run(
            [sys.executable, self._entry, "--list-tools"],
            capture_output=True, text=True, timeout=30,
            env=self._runtime_env, cwd=self._tmpdir,
        )
        self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}")
        self.assertIn("tenor_task_start", proc.stdout)

    def test_e2e_tool_returns_ready(self) -> None:
        import subprocess
        proc = subprocess.run(
            [
                sys.executable, self._entry, "--call", "tenor_task_prompt",
                "--args", '{"task": "fix auth bug"}',
            ],
            capture_output=True, text=True, timeout=30,
            env=self._runtime_env, cwd=self._tmpdir,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"returncode={proc.returncode} stderr={proc.stderr}",
        )
        mcp_resp = json.loads(proc.stdout)
        inner_text = mcp_resp["content"][0]["text"]
        result = json.loads(inner_text)
        self.assertTrue(result["ok"], f"call failed: {result}")
        self.assertEqual(result["verdict"], "TENOR_TASK_PROMPT_READY")


if __name__ == "__main__":
    unittest.main()
