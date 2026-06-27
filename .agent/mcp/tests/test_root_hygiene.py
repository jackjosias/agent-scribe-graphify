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

import server_ext as mcp
from runtime import root_hygiene


def call_tool(name: str, **args: Any) -> dict[str, Any]:
    result = mcp.handle({"jsonrpc": "2.0", "id": name, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return json.loads(result["result"]["content"][0]["text"])


def init_repo(root: Path) -> None:
    (root / ".agent").mkdir(parents=True)
    (root / "AGENTS.md").write_text("agents\n", encoding="utf-8")
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=str(root), check=True, capture_output=True)


class RootHygieneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_root = mcp.server.ROOT
        self.old_agent = mcp.server.AGENT_DIR
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        mcp.server.ROOT = self.old_root
        mcp.server.AGENT_DIR = self.old_agent
        self.tmp.cleanup()

    def test_01_tool_schema_exists(self) -> None:
        names = {tool["name"] for tool in mcp.list_tools()}
        self.assertIn("root_hygiene_status", names)
        schema = mcp.tool_schema("root_hygiene_status")
        self.assertIn("strict", schema.get("properties", {}))

    def test_02_normal_repo_returns_ok_or_warn(self) -> None:
        root = Path(self.tmp.name) / "repo"
        root.mkdir()
        init_repo(root)
        report = root_hygiene.inspect_root_hygiene(root)
        self.assertIn(report["verdict"], {root_hygiene.ROOT_HYGIENE_OK, root_hygiene.ROOT_HYGIENE_WARN})

    def test_03_no_destructive_cleanup_occurs(self) -> None:
        root = Path(self.tmp.name) / "repo"
        root.mkdir(); init_repo(root)
        sentinel = root.parent / ".agent" / "sentinel.txt"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("keep\n", encoding="utf-8")
        root_hygiene.inspect_root_hygiene(root)
        self.assertTrue(sentinel.exists())

    def test_04_detects_parent_agent(self) -> None:
        parent = Path(self.tmp.name) / "parent"
        root = parent / "repo"
        root.mkdir(parents=True); init_repo(root)
        (parent / ".agent").mkdir()
        report = root_hygiene.inspect_root_hygiene(root)
        codes = {item["code"] for item in report["warnings"]}
        self.assertIn(root_hygiene.PARENT_AGENT_ROOT_DETECTED, codes)

    def test_05_detects_generated_outputs_outside_git_root(self) -> None:
        parent = Path(self.tmp.name) / "parent"
        root = parent / "repo"
        root.mkdir(parents=True); init_repo(root)
        (parent / "graphify-out").mkdir()
        (parent / "scribe-out").mkdir()
        report = root_hygiene.inspect_root_hygiene(root)
        codes = [item["code"] for item in report["warnings"]]
        self.assertGreaterEqual(codes.count(root_hygiene.GENERATED_OUTPUT_OUTSIDE_GIT_ROOT), 2)

    def test_06_strict_fails_without_git_root(self) -> None:
        root = Path(self.tmp.name) / "nogit"
        root.mkdir()
        with self.assertRaises(RuntimeError):
            root_hygiene.assert_safe_project_root(root, strict=True)

    def test_07_strict_passes_clean_layout(self) -> None:
        root = Path(self.tmp.name) / "repo"
        root.mkdir(); init_repo(root)
        report = root_hygiene.assert_safe_project_root(root, strict=True, max_parent_depth=0)
        self.assertEqual(report["errors"], [])

    def test_08_report_never_includes_secret_named_paths(self) -> None:
        root = Path(self.tmp.name) / "secret-token-root" / "repo"
        root.mkdir(parents=True); init_repo(root)
        report = root_hygiene.inspect_root_hygiene(root)
        blob = json.dumps(report).lower()
        self.assertNotIn("secret-token-root", blob)

    def test_09_paths_are_json_serializable_strings(self) -> None:
        root = Path(self.tmp.name) / "repo"
        root.mkdir(); init_repo(root)
        report = root_hygiene.inspect_root_hygiene(root)
        json.dumps(report)
        self.assertIsInstance(report["agent_dir"], str)

    def test_10_tool_is_read_only(self) -> None:
        root = Path(self.tmp.name) / "repo"
        root.mkdir(); init_repo(root)
        mcp.server.ROOT = root.resolve(); mcp.server.AGENT_DIR = root / ".agent"
        before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
        result = call_tool("root_hygiene_status")
        after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
        self.assertIn(result["verdict"], {root_hygiene.ROOT_HYGIENE_OK, root_hygiene.ROOT_HYGIENE_WARN})
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
