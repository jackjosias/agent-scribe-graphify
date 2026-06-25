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
        "discipline_ping",
        "workflow_next",
        "Aucune ecriture directe",
        "SCRIBE pour le contexte",
        "Graphify pour l impact structurel",
        "pre_action_guard",
        "action_lease_id",
        "workspace_audit",
        "scribe_record",
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
        self.assertEqual(result["required_first_actions"], ["discipline_ping", "workflow_next"])
        self.assertEqual(result["required_finish_actions"], ["workspace_audit", "scribe_record", "finish_task"])
        self.assertEqual(result["forbidden"], ["direct_write", "invent_tool_result", "finish_without_audit"])

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
        self.assertIn("Workflow read/check obligatoire", result["prompt"])

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
        self.assertIn("lecture/analyse/proposition uniquement", result["prompt"])

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
        for intent in ["read", "write", "refactor", "delete", "test", "debug"]:
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
        self.assertIn("discipline_ping", proc.stdout)

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


if __name__ == "__main__":
    unittest.main()
