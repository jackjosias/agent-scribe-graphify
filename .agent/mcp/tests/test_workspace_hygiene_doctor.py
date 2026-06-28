from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parents[1] / "scripts" / "workspace_hygiene_doctor.py"


def make_repo(parent: Path, name: str = "repo") -> Path:
    root = parent / name
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".agent").mkdir()
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    return root


class WorkspaceHygieneDoctorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self.tmp.name) / "workspace"
        self.parent.mkdir()
        self.root = make_repo(self.parent)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_01_dry_run_detects_parent_agent(self) -> None:
        (self.parent / ".agent").mkdir()
        result = self._run("--dry-run")
        self.assertEqual(result["verdict"], "PARENT_WORKSPACE_POLLUTION_FOUND")
        self.assertTrue(result["found_parent_agent"])

    def test_02_dry_run_detects_parent_graphify_out(self) -> None:
        (self.parent / "graphify-out").mkdir()
        result = self._run("--dry-run")
        self.assertTrue(result["found_parent_graphify_out"])

    def test_03_dry_run_detects_parent_scribe_out(self) -> None:
        (self.parent / "scribe-out").mkdir()
        result = self._run("--dry-run")
        self.assertTrue(result["found_parent_scribe_out"])

    def test_04_dry_run_deletes_nothing(self) -> None:
        (self.parent / ".agent").mkdir()
        self._run("--dry-run")
        self.assertTrue((self.parent / ".agent").exists())

    def test_05_apply_removes_only_three_exact_parent_paths(self) -> None:
        for name in (".agent", "graphify-out", "scribe-out", "keep-me"):
            (self.parent / name).mkdir()
        result = self._run("--apply")
        self.assertEqual(result["verdict"], "PARENT_WORKSPACE_CLEANED")
        for name in (".agent", "graphify-out", "scribe-out"):
            self.assertFalse((self.parent / name).exists())
        self.assertTrue((self.parent / "keep-me").exists())
        self.assertTrue((self.root / ".agent").exists())

    def test_06_refuses_if_parent_is_git_repo(self) -> None:
        (self.parent / ".git").mkdir()
        (self.parent / ".agent").mkdir()
        result = self._run("--apply", expect_ok=False)
        self.assertEqual(result["verdict"], "PARENT_WORKSPACE_CLEANUP_REFUSED")
        self.assertTrue((self.parent / ".agent").exists())

    def test_07_refuses_if_true_repo_missing(self) -> None:
        result = self._run("--root", str(self.parent / "missing"), expect_ok=False)
        self.assertEqual(result["verdict"], "PARENT_WORKSPACE_CLEANUP_REFUSED")

    def test_08_refuses_symlinks(self) -> None:
        outside = Path(self.tmp.name) / "outside-agent"
        outside.mkdir()
        (self.parent / ".agent").symlink_to(outside, target_is_directory=True)
        result = self._run("--apply", expect_ok=False)
        self.assertEqual(result["verdict"], "PARENT_WORKSPACE_CLEANUP_REFUSED")
        self.assertTrue(outside.exists())

    def test_09_json_output_is_structured(self) -> None:
        result = self._run("--dry-run")
        self.assertIn("verdict", result)
        self.assertIn("would_remove", result)

    def test_10_verdict_ok_when_parent_clean(self) -> None:
        result = self._run("--dry-run")
        self.assertEqual(result["verdict"], "WORKSPACE_HYGIENE_OK")

    def _run(self, *args: str, expect_ok: bool = True) -> dict[str, object]:
        argv = [sys.executable, str(SCRIPT), "--root", str(self.root), *args]
        proc = subprocess.run(argv, text=True, capture_output=True, timeout=15)
        if expect_ok:
            self.assertEqual(proc.returncode, 0, proc.stderr)
        else:
            self.assertNotEqual(proc.returncode, 0)
        return json.loads(proc.stdout)


if __name__ == "__main__":
    unittest.main()
