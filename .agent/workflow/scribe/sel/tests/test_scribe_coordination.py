from __future__ import annotations

import contextlib
import os
import tempfile
import unittest
from pathlib import Path

from scribe_test_utils import load_script_module


scribe_coordination = load_script_module("scribe_coordination")
cmd_main = getattr(scribe_coordination, "main")


class ScribeCoordinationTests(unittest.TestCase):
    @contextlib.contextmanager
    def isolated_coordination_dir(self, root: Path):
        old_dir = os.environ.get("SCRIBE_COORDINATION_DIR")
        os.environ["SCRIBE_COORDINATION_DIR"] = str(root / "coordination")
        try:
            yield
        finally:
            if old_dir is None:
                os.environ.pop("SCRIBE_COORDINATION_DIR", None)
            else:
                os.environ["SCRIBE_COORDINATION_DIR"] = old_dir

    def run_cli(self, *args: str) -> tuple[int, str]:
        import contextlib as ctx
        import io
        import sys

        stdout = io.StringIO()
        old_argv = sys.argv[:]
        sys.argv = ["scribe coordination", *args]
        try:
            with ctx.redirect_stdout(stdout):
                code = cmd_main()
        finally:
            sys.argv = old_argv
        return code, stdout.getvalue()

    def test_duplicate_semantic_claim_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_coordination_dir(Path(tmp)):
            first, _ = self.run_cli("claim", "--agent", "a", "--claim", "indicator:x", "--task", "X")
            second, output = self.run_cli("claim", "--agent", "b", "--claim", "indicator:x", "--task", "X again")

        self.assertEqual(first, 0)
        self.assertEqual(second, 2)
        self.assertIn("semantic claim already active", output)

    def test_shared_files_are_allowed_with_rebase_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_coordination_dir(Path(tmp)):
            first, _ = self.run_cli(
                "claim",
                "--agent",
                "a",
                "--claim",
                "indicator:x",
                "--task",
                "X",
                "--expected-file",
                "IndicatorsModal.tsx",
            )
            second, output = self.run_cli(
                "claim",
                "--agent",
                "b",
                "--claim",
                "indicator:y",
                "--task",
                "Y",
                "--expected-file",
                "IndicatorsModal.tsx",
            )

        self.assertEqual(first, 0)
        self.assertEqual(second, 0)
        self.assertIn("shared_files_detected: yes", output)
        self.assertIn("rebase before delivery", output)

    def test_finish_marks_claim_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_coordination_dir(Path(tmp)):
            claim_code, _ = self.run_cli("claim", "--agent", "a", "--claim", "indicator:z", "--task", "Z")
            finish_code, output = self.run_cli(
                "finish",
                "--agent",
                "a",
                "--claim",
                "indicator:z",
                "--summary",
                "implemented Z",
                "--changed-file",
                "TechnicalIndicators.ts",
            )
            status_code, status = self.run_cli("status")

        self.assertEqual(claim_code, 0)
        self.assertEqual(finish_code, 0)
        self.assertEqual(status_code, 0)
        self.assertIn("claim finished", output)
        self.assertIn("active_claims: 0", status)


if __name__ == "__main__":
    unittest.main()
