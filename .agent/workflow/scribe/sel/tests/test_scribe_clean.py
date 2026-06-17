from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scribe_test_utils import load_script_module


scribe_clean = load_script_module("scribe_clean")
build_cleanup_plan = getattr(scribe_clean, "build_cleanup_plan")
apply_cleanup = getattr(scribe_clean, "apply_cleanup")
count_scribe_noise = getattr(scribe_clean, "count_scribe_noise")


class ScribeCleanTests(unittest.TestCase):
    def touch(self, path: Path, content: str = "x") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_dry_run_detects_scribe_noise_without_active_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.touch(root / "scribe-out" / "state.json", "{}")
            self.touch(root / "scribe-out" / "scribe-doctor-report.md")
            self.touch(root / "scribe-out" / "scribe-dashboard.html")
            self.touch(root / "scribe-out" / "scribe-doctor-before-report.md")
            self.touch(root / "scribe-out" / "scribe-export-check.json")
            self.touch(root / "scribe-out" / "scribe-dashboard-mobile.png")

            plan = build_cleanup_plan(root, include_graphify=False, max_ast_files=50)

            planned = {candidate.path.name for candidate in plan}
            self.assertEqual(planned, {"scribe-doctor-before-report.md", "scribe-export-check.json", "scribe-dashboard-mobile.png"})
            self.assertEqual(count_scribe_noise(root), 3)
            self.assertTrue((root / "scribe-out" / "state.json").exists())

    def test_apply_removes_only_known_scribe_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.touch(root / "scribe-out" / "locks" / "scribe.lock")
            self.touch(root / "scribe-out" / "archive" / "memory.yaml")
            self.touch(root / "scribe-out" / "scribe-doctor-after-report.md")
            self.touch(root / "scribe-out" / "scribe-doctor-shim-report.md")
            self.touch(root / "scribe-out" / "commit-plan" / "README.md")
            self.touch(root / "scribe-out" / "notes.txt")

            removed = apply_cleanup(build_cleanup_plan(root, include_graphify=False, max_ast_files=50))

            self.assertEqual(removed, 3)
            self.assertFalse((root / "scribe-out" / "scribe-doctor-after-report.md").exists())
            self.assertFalse((root / "scribe-out" / "scribe-doctor-shim-report.md").exists())
            self.assertFalse((root / "scribe-out" / "commit-plan").exists())
            self.assertTrue((root / "scribe-out" / "locks" / "scribe.lock").exists())
            self.assertTrue((root / "scribe-out" / "archive" / "memory.yaml").exists())
            self.assertTrue((root / "scribe-out" / "notes.txt").exists())

    def test_graphify_ast_cache_prunes_to_lru_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ast_dir = root / "graphify-out" / "cache" / "ast"
            bundle_ast_dir = root / "scribe-out" / "bundle-graph" / "scribe" / "cache" / "ast"
            for index in range(55):
                self.touch(ast_dir / f"{index:02d}.json")
                self.touch(bundle_ast_dir / f"{index:02d}.json")

            plan = build_cleanup_plan(root, include_graphify=True, max_ast_files=50)
            removed = apply_cleanup(plan)

            self.assertEqual(removed, 10)
            self.assertEqual(len(list(ast_dir.glob("*.json"))), 50)
            self.assertEqual(len(list(bundle_ast_dir.glob("*.json"))), 50)

    def test_agent_cache_removes_pycache_without_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.touch(root / ".agent" / "workflow" / "tool" / "__pycache__" / "x.pyc")
            self.touch(root / ".agent" / "workflow" / "tool" / "source.py")

            plan = build_cleanup_plan(root, include_graphify=False, max_ast_files=50, include_agent_cache=True)
            removed = apply_cleanup(plan)

            self.assertEqual(removed, 1)
            self.assertFalse((root / ".agent" / "workflow" / "tool" / "__pycache__").exists())
            self.assertTrue((root / ".agent" / "workflow" / "tool" / "source.py").exists())


if __name__ == "__main__":
    unittest.main()
