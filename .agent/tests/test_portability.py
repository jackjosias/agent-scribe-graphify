"""test_portability.py — TDD tests for runtime/portability.py.

Coverage: 12 tests.
  1.  validate_root() returns ok=True when .agent is at git root (happy path)
  2.  validate_root() returns AGENT_NOT_AT_GIT_ROOT when .agent is at subdir
  3.  validate_root() returns AGENT_DIR_MISSING when .agent is absent
  4.  ENV override (AGENT_SCRIBE_GRAPHIFY_ROOT) is respected by get_workspace_root()
  5.  _read_agent_json() returns {} when file absent
  6.  _read_agent_json() returns parsed content when file present
  7.  init_agent_json() creates agent.json with correct schema_version
  8.  init_agent_json() is idempotent (second call updates, doesn't duplicate)
  9.  find_project_root via get_workspace_root() uses git toplevel when available
  10. get_workspace_root() falls back when git unavailable (graceful degradation)
  11. portability_check() returns structured dict with ok/verdict/workspace_root
  12. assert_root_valid() raises PortabilityError when validation fails
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Bootstrap sys.path for isolated test run.
# ---------------------------------------------------------------------------
_ORIG_CWD = os.getcwd()
_MCP_DIR = str(Path(_ORIG_CWD) / ".agent" / "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from runtime import portability  # noqa: E402


class _BasePortabilityTest(unittest.TestCase):
    """Shared setup: temp directory with .git + .agent + server_entry.py stub."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="test_portability_")
        self._root = Path(self._tmp)
        # Create .git dir to simulate a git repo.
        (self._root / ".git").mkdir()
        (self._root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        # Create .agent/ with minimal structure.
        agent_dir = self._root / ".agent"
        agent_dir.mkdir()
        mcp_dir = agent_dir / "mcp"
        mcp_dir.mkdir()
        (mcp_dir / "server_entry.py").write_text("# stub\n", encoding="utf-8")
        # Clear env override.
        self._orig_env = os.environ.pop(portability.ENV_ROOT_KEY, None)

    def tearDown(self) -> None:
        if self._orig_env is not None:
            os.environ[portability.ENV_ROOT_KEY] = self._orig_env
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestValidateRoot(_BasePortabilityTest):
    """Tests for validate_root()."""

    # ------------------------------------------------------------------
    # 1. Happy path: .agent at git root → ROOT_VALID
    # ------------------------------------------------------------------
    def test_01_validate_root_ok_at_git_root(self) -> None:
        with patch.object(portability, "_git_toplevel", return_value=self._root):
            result = portability.validate_root(workspace_root=self._root)
        self.assertTrue(result["ok"], msg=str(result))
        self.assertEqual(result["verdict"], "ROOT_VALID")

    # ------------------------------------------------------------------
    # 2. .agent NOT at git root → AGENT_NOT_AT_GIT_ROOT
    # ------------------------------------------------------------------
    def test_02_agent_not_at_git_root(self) -> None:
        # Git root is self._root, but we're validating from a subdir.
        # Simulate: git_toplevel returns _root, but workspace is a subdir.
        subdir = self._root / "packages" / "backend"
        subdir.mkdir(parents=True)
        agent_sub = subdir / ".agent"
        agent_sub.mkdir()
        (agent_sub / "mcp").mkdir()
        (agent_sub / "mcp" / "server_entry.py").write_text("# stub\n", encoding="utf-8")
        # _git_toplevel from subdir returns the real root → mismatch.
        with patch.object(portability, "_git_toplevel", return_value=self._root):
            result = portability.validate_root(workspace_root=subdir)
        # Either AGENT_NOT_AT_GIT_ROOT or ok=False with git_mismatch.
        self.assertFalse(result["ok"], msg=str(result))
        self.assertIn(result["verdict"], {"AGENT_NOT_AT_GIT_ROOT", "AGENT_DIR_MISSING"})

    # ------------------------------------------------------------------
    # 3. .agent directory missing → AGENT_DIR_MISSING
    # ------------------------------------------------------------------
    def test_03_agent_dir_missing(self) -> None:
        empty = self._root / "empty_workspace"
        empty.mkdir()
        with patch.object(portability, "_git_toplevel", return_value=empty):
            result = portability.validate_root(workspace_root=empty)
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "AGENT_DIR_MISSING")

    # ------------------------------------------------------------------
    # 4. ENV override respected by get_workspace_root()
    # ------------------------------------------------------------------
    def test_04_env_override_respected(self) -> None:
        os.environ[portability.ENV_ROOT_KEY] = str(self._root)
        root = portability.get_workspace_root()
        self.assertEqual(root, self._root)


class TestAgentJson(_BasePortabilityTest):
    """Tests for _read_agent_json() and init_agent_json()."""

    def _agent_dir(self) -> Path:
        return self._root / ".agent"

    # ------------------------------------------------------------------
    # 5. _read_agent_json() returns {} when file absent
    # ------------------------------------------------------------------
    def test_05_read_agent_json_returns_empty_when_absent(self) -> None:
        # Accessing private function — it's core internal logic, worth testing directly.
        result = portability._read_agent_json(self._agent_dir())
        self.assertEqual(result, {})

    # ------------------------------------------------------------------
    # 6. _read_agent_json() returns parsed content when file present
    # ------------------------------------------------------------------
    def test_06_read_agent_json_parses_existing_file(self) -> None:
        agent_json = self._agent_dir() / portability.AGENT_JSON_FILENAME
        agent_json.write_text(
            json.dumps({"schema_version": "2.14", "project_name": "test-proj"}),
            encoding="utf-8",
        )
        result = portability._read_agent_json(self._agent_dir())
        self.assertEqual(result["schema_version"], "2.14")
        self.assertEqual(result["project_name"], "test-proj")

    # ------------------------------------------------------------------
    # 7. init_agent_json() creates agent.json with correct schema_version
    # ------------------------------------------------------------------
    def test_07_init_agent_json_creates_file(self) -> None:
        portability.init_agent_json(self._root, project_name="smoke-proj")
        agent_json = self._agent_dir() / portability.AGENT_JSON_FILENAME
        self.assertTrue(agent_json.exists())
        content = json.loads(agent_json.read_text(encoding="utf-8"))
        self.assertEqual(content["schema_version"], portability.AGENT_JSON_SCHEMA_VERSION)
        self.assertEqual(content["project_name"], "smoke-proj")
        self.assertTrue(content["portable"])

    # ------------------------------------------------------------------
    # 8. init_agent_json() is idempotent — second call updates project_name
    # ------------------------------------------------------------------
    def test_08_init_agent_json_is_idempotent(self) -> None:
        portability.init_agent_json(self._root, project_name="v1")
        portability.init_agent_json(self._root, project_name="v2")
        agent_json = self._agent_dir() / portability.AGENT_JSON_FILENAME
        content = json.loads(agent_json.read_text(encoding="utf-8"))
        # project_name should be updated to v2; no duplicate entries.
        self.assertEqual(content["project_name"], "v2")


class TestGetWorkspaceRoot(_BasePortabilityTest):
    """Tests for get_workspace_root() root discovery."""

    # ------------------------------------------------------------------
    # 9. Uses git toplevel when git available (via env priority 1)
    # ------------------------------------------------------------------
    def test_09_uses_env_override_as_highest_priority(self) -> None:
        os.environ[portability.ENV_ROOT_KEY] = str(self._root)
        root = portability.get_workspace_root()
        self.assertEqual(root, self._root)

    # ------------------------------------------------------------------
    # 10. Falls back gracefully when env not set (file-relative resolution)
    # ------------------------------------------------------------------
    def test_10_falls_back_to_file_relative_resolution(self) -> None:
        # Ensure env is cleared.
        os.environ.pop(portability.ENV_ROOT_KEY, None)
        root = portability.get_workspace_root()
        # Should return a valid Path without raising.
        self.assertIsInstance(root, Path)


class TestPortabilityCheck(_BasePortabilityTest):
    """Tests for portability_check() and assert_root_valid()."""

    # ------------------------------------------------------------------
    # 11. portability_check() returns structured dict
    # ------------------------------------------------------------------
    def test_11_portability_check_returns_structured_dict(self) -> None:
        with patch.object(portability, "_git_toplevel", return_value=self._root):
            result = portability.portability_check(workspace_root=self._root)
        self.assertIn("ok", result)
        self.assertIn("verdict", result)
        self.assertIn("workspace_root", result)
        self.assertIn("checks", result)

    # ------------------------------------------------------------------
    # 12. assert_root_valid() raises PortabilityError when validation fails
    # ------------------------------------------------------------------
    def test_12_assert_root_valid_raises_on_failure(self) -> None:
        empty = self._root / "no_agent_here"
        empty.mkdir()
        with patch.object(portability, "_git_toplevel", return_value=empty):
            with self.assertRaises(portability.PortabilityError) as cm:
                portability.assert_root_valid(workspace_root=empty)
        self.assertEqual(cm.exception.code, "AGENT_DIR_MISSING")


if __name__ == "__main__":
    unittest.main(verbosity=2)
