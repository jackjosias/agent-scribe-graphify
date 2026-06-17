from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from scribe_test_utils import load_script_module


scribe_graphify_hooks = load_script_module("scribe_graphify_hooks")
graphify_hooks_main = getattr(scribe_graphify_hooks, "main")
CODEX_SAFE_COMMAND = getattr(scribe_graphify_hooks, "CODEX_SAFE_COMMAND")
GEMINI_SAFE_COMMAND = getattr(scribe_graphify_hooks, "GEMINI_SAFE_COMMAND")


class ScribeGraphifyHookGuardTests(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        old_argv = sys.argv[:]
        sys.argv = ["scribe graphify-hooks", *args]
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = graphify_hooks_main()
        finally:
            sys.argv = old_argv
        return code, stdout.getvalue(), stderr.getvalue()

    def write_codex_hooks(self, directory: Path) -> Path:
        path = directory / "hooks.json"
        path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {"matcher": "Bash", "hooks": [{"type": "command", "command": CODEX_SAFE_COMMAND}]}
                        ]
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def test_guard_reports_legacy_gemini_template_without_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "__main__.py"
            template.write_text('command = (\n    r"""echo \'{"decision":"allow"}\' # graphify hook"""\n)\n', encoding="utf-8")
            trusted = root / "trusted_hooks.json"
            trusted.write_text(json.dumps({"/repo": [GEMINI_SAFE_COMMAND]}) + "\n", encoding="utf-8")
            codex = self.write_codex_hooks(root)

            code, output, error = self.run_cli(
                "--no-default-templates",
                "--template",
                str(template),
                "--trusted-hooks",
                str(trusted),
                "--codex-hooks",
                str(codex),
                "--no-simulate",
            )

        self.assertEqual(code, 1)
        self.assertEqual(error, "")
        self.assertIn("legacy Gemini hook command", output)
        self.assertIn("verdict: FAIL", output)

    def test_apply_patches_template_and_trusted_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "__main__.py"
            template.write_text('command = (\n    r"""echo \'{"decision":"allow"}\' # graphify hook"""\n)\n', encoding="utf-8")
            trusted = root / "trusted_hooks.json"
            trusted.write_text(json.dumps({"/repo": [': graphify hook; echo \'{"decision":"allow"}\'']}) + "\n", encoding="utf-8")
            codex = self.write_codex_hooks(root)

            code, output, error = self.run_cli(
                "--apply",
                "--no-default-templates",
                "--template",
                str(template),
                "--trusted-hooks",
                str(trusted),
                "--codex-hooks",
                str(codex),
            )
            template_text = template.read_text(encoding="utf-8")
            trusted_payload = json.loads(trusted.read_text(encoding="utf-8"))

        self.assertEqual(code, 0, error)
        self.assertIn("verdict: PASS", output)
        self.assertIn(GEMINI_SAFE_COMMAND, template_text)
        self.assertEqual(trusted_payload["/repo"], [GEMINI_SAFE_COMMAND])


if __name__ == "__main__":
    unittest.main()
