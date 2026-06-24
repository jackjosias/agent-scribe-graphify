"""test_graphify_guard.py — Tests for runtime/graphify_guard.py.

Coverage: 12 tests.
   1. detect_graphify_binary() returns False when binary not on PATH
   2. detect_graphify_binary() returns True when fake binary on PATH
   3. get_graphify_version() returns None when binary missing
   4. get_graphify_version() returns version string from fake binary
   5. validate_graphify_installation() returns GRAPHIFY_REQUIRED_NOT_INSTALLED when missing
   6. validate_graphify_outputs() returns GRAPHIFY_OUTPUTS_MISSING when dir absent
   7. validate_graphify_outputs() returns GRAPHIFY_OUTPUTS_READY when all files present
   8. validate_graphify_outputs() returns GRAPHIFY_OUTPUTS_MISSING when file empty
   9. check_graphify_required() returns blocking = True when binary missing
  10. check_graphify_required() returns blocking = True when outputs missing
  11. check_graphify_required() returns GRAPHIFY_READY when everything ready
  12. render_graphify_install_guide() contains graphifyy (double y)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap sys.path.
# ---------------------------------------------------------------------------
_ORIG_CWD = os.getcwd()
_MCP_DIR = str(Path(_ORIG_CWD) / ".agent" / "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from runtime import graphify_guard as gg  # noqa: E402


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _fake_graphify_binary(ws: Path) -> Path:
    """Create a fake `graphify` script in ws/bin that echoes a version."""
    bindir = ws / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "graphify"
    script.write_text("#!/bin/sh\necho '0.5.0'\n", encoding="utf-8")
    script.chmod(0o755)
    return bindir


def _make_graphify_out(ws: Path) -> None:
    """Create a valid graphify-out/ with all required files."""
    out = ws / "graphify-out"
    out.mkdir(exist_ok=True)
    (out / "graph.json").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
    (out / "GRAPH_REPORT.md").write_text("# Report\n", encoding="utf-8")
    (out / "graph.html").write_text("<html></html>\n", encoding="utf-8")


def _push_path(bindir: Path) -> str:
    old = os.environ.get("PATH", "")
    os.environ["PATH"] = str(bindir) + os.pathsep + old
    return old


def _pop_path(old: str) -> None:
    os.environ["PATH"] = old


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestGuardBinaryDetection(unittest.TestCase):
    """Tests for detect_graphify_binary() and get_graphify_version()."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="test_guard_bin_")
        self._root = Path(self._tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # 1. Binary missing → False
    # ------------------------------------------------------------------
    def test_01_binary_missing(self) -> None:
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = "/dev/null"
        try:
            result = gg.detect_graphify_binary()
        finally:
            os.environ["PATH"] = old
        self.assertFalse(result)

    # ------------------------------------------------------------------
    # 2. Binary present → True
    # ------------------------------------------------------------------
    def test_02_binary_present(self) -> None:
        bindir = _fake_graphify_binary(self._root)
        old = _push_path(bindir)
        try:
            result = gg.detect_graphify_binary()
        finally:
            _pop_path(old)
        self.assertTrue(result)

    # ------------------------------------------------------------------
    # 3. get_graphify_version() returns None when missing
    # ------------------------------------------------------------------
    def test_03_version_missing_returns_none(self) -> None:
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = "/dev/null"
        try:
            version = gg.get_graphify_version()
        finally:
            os.environ["PATH"] = old
        self.assertIsNone(version)

    # ------------------------------------------------------------------
    # 4. get_graphify_version() returns version string
    # ------------------------------------------------------------------
    def test_04_version_returns_string(self) -> None:
        bindir = _fake_graphify_binary(self._root)
        old = _push_path(bindir)
        try:
            version = gg.get_graphify_version()
        finally:
            _pop_path(old)
        self.assertIsNotNone(version)
        self.assertIn("0.5.0", version)


class TestGuardValidateInstallation(unittest.TestCase):
    """Tests for validate_graphify_installation()."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="test_guard_install_")
        self._root = Path(self._tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # 5. Missing binary → GRAPHIFY_REQUIRED_NOT_INSTALLED
    # ------------------------------------------------------------------
    def test_05_install_missing_verdict(self) -> None:
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = "/dev/null"
        try:
            result = gg.validate_graphify_installation()
        finally:
            os.environ["PATH"] = old
        self.assertEqual(result.get("verdict"), gg.VERDICT_BINARY_MISSING)
        self.assertIsNone(result.get("binary_path"))


class TestGuardValidateOutputs(unittest.TestCase):
    """Tests for validate_graphify_outputs()."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="test_guard_outputs_")
        self._root = Path(self._tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # 6. Output dir missing → GRAPHIFY_OUTPUTS_MISSING
    # ------------------------------------------------------------------
    def test_06_output_dir_missing(self) -> None:
        result = gg.validate_graphify_outputs(self._root)
        self.assertEqual(result.get("verdict"), gg.VERDICT_OUTPUTS_MISSING)
        self.assertFalse(result.get("ok"))

    # ------------------------------------------------------------------
    # 7. All files present → GRAPHIFY_OUTPUTS_READY
    # ------------------------------------------------------------------
    def test_07_outputs_ready(self) -> None:
        _make_graphify_out(self._root)
        result = gg.validate_graphify_outputs(self._root)
        self.assertEqual(result.get("verdict"), "GRAPHIFY_OUTPUTS_READY")
        self.assertTrue(result.get("ok"))

    # ------------------------------------------------------------------
    # 8. Empty file → GRAPHIFY_OUTPUTS_MISSING
    # ------------------------------------------------------------------
    def test_08_outputs_empty_file(self) -> None:
        out = self._root / "graphify-out"
        out.mkdir()
        (out / "graph.json").write_text("", encoding="utf-8")
        (out / "GRAPH_REPORT.md").write_text("non-empty\n", encoding="utf-8")
        (out / "graph.html").write_text("<html></html>\n", encoding="utf-8")
        result = gg.validate_graphify_outputs(self._root)
        self.assertEqual(result.get("verdict"), gg.VERDICT_OUTPUTS_MISSING)
        self.assertFalse(result.get("ok"))


class TestGuardCheckRequired(unittest.TestCase):
    """Tests for check_graphify_required() — the main entry point."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="test_guard_check_")
        self._root = Path(self._tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # 9. Binary missing → blocking=True, write_allowed=False
    # ------------------------------------------------------------------
    def test_09_check_binary_missing_blocks(self) -> None:
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = "/dev/null"
        try:
            result = gg.check_graphify_required(
                self._root, host_type="opencode", auto_write_guide=False,
            )
        finally:
            os.environ["PATH"] = old
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("blocking"))
        self.assertFalse(result.get("write_allowed"))
        self.assertEqual(result.get("verdict"), gg.VERDICT_BINARY_MISSING)

    # ------------------------------------------------------------------
    # 10. Binary present, outputs missing → blocking=True
    # ------------------------------------------------------------------
    def test_10_check_outputs_missing_blocks(self) -> None:
        bindir = _fake_graphify_binary(self._root)
        old = _push_path(bindir)
        try:
            result = gg.check_graphify_required(
                self._root, host_type="opencode", auto_write_guide=False,
            )
        finally:
            _pop_path(old)
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("blocking"))
        self.assertFalse(result.get("write_allowed"))
        self.assertEqual(result.get("verdict"), gg.VERDICT_OUTPUTS_MISSING)

    # ------------------------------------------------------------------
    # 11. Everything ready → GRAPHIFY_READY, write_allowed=True
    # ------------------------------------------------------------------
    def test_11_check_ready_allows_write(self) -> None:
        _make_graphify_out(self._root)
        bindir = _fake_graphify_binary(self._root)
        old = _push_path(bindir)
        try:
            result = gg.check_graphify_required(
                self._root, host_type="opencode", auto_write_guide=False,
            )
        finally:
            _pop_path(old)
        self.assertTrue(result.get("ok"))
        self.assertFalse(result.get("blocking"))
        self.assertTrue(result.get("write_allowed"))
        self.assertEqual(result.get("verdict"), gg.VERDICT_READY)


class TestGuardInstallGuide(unittest.TestCase):
    """Tests for render_graphify_install_guide()."""

    # ------------------------------------------------------------------
    # 12. Guide mentions graphifyy (double y)
    # ------------------------------------------------------------------
    def test_12_guide_mentions_graphifyy(self) -> None:
        guide = gg.render_graphify_install_guide(host_type="opencode", os_name="linux")
        self.assertIn("graphifyy", guide)
        self.assertIn("graphify", guide)

    def test_12b_guide_mentions_opencode_flag(self) -> None:
        guide = gg.render_graphify_install_guide(host_type="opencode")
        self.assertIn("--platform opencode", guide)


class TestGuardAtomicWrite(unittest.TestCase):
    """Tests for write_graphify_install_guide() atomicity."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="test_guard_atomic_")
        self._root = Path(self._tmp)
        (self._root / ".agent" / "docs").mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_13_atomic_write_creates_file(self) -> None:
        result = gg.write_graphify_install_guide(self._root, host_type="opencode")
        self.assertTrue(result.get("ok"))
        target = self._root / gg.INSTALL_GUIDE_REL_PATH
        self.assertTrue(target.is_file())
        content = target.read_text(encoding="utf-8")
        self.assertIn("graphifyy", content)

    def test_14_atomic_write_no_temp_left(self) -> None:
        gg.write_graphify_install_guide(self._root, host_type="opencode")
        target = self._root / gg.INSTALL_GUIDE_REL_PATH
        tmp_path = target.with_suffix(".tmp")
        self.assertFalse(tmp_path.exists())


class TestGuardPlatformDetection(unittest.TestCase):
    """Tests for internal platform detection."""

    def test_15_detect_os_returns_lowercase(self) -> None:
        os_name = gg._detect_os()
        self.assertIn(os_name, {"linux", "darwin", "windows", "unknown"})

    def test_16_is_ubuntu_or_debian_returns_bool(self) -> None:
        # Should not crash on any system.
        result = gg._is_ubuntu_or_debian()
        self.assertIsInstance(result, bool)


class TestGuardSafeSubprocess(unittest.TestCase):
    """Tests for _safe_subprocess()."""

    def test_17_safe_subprocess_echo(self) -> None:
        result = gg._safe_subprocess(["echo", "hello"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["returncode"], 0)

    def test_18_safe_subprocess_command_not_found(self) -> None:
        result = gg._safe_subprocess(["/nonexistent/command_xyz"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["returncode"], 127)


if __name__ == "__main__":
    unittest.main(verbosity=2)
