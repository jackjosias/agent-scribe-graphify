from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
TEMPLATES_PATH = REPO_ROOT / ".agent" / "workflow" / "scribe" / "sel" / "scripts" / "scribe_install_templates.py"


def load_templates():
    spec = importlib.util.spec_from_file_location("scribe_install_templates_parity", TEMPLATES_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TEMPLATES_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GeneratedSurfaceParityTest(unittest.TestCase):
    def test_agents_managed_surface_matches_generator_byte_for_byte(self) -> None:
        templates = load_templates()
        actual = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        expected = templates.render_agents_block()

        self.assertEqual(actual, expected)
        self.assertIn("TENOR_INIT_SAME_PROJECT` never repairs the bundle", expected)
        self.assertIn("complete raw copy of `.agent/` is a mandatory supported installation path", expected)
        self.assertIn("Default commit/push scope is the host product source", expected)
        self.assertIn(
            "keep `.agent/state/outputs/graphify-out/` and `.agent/state/outputs/scribe-out/` out of commits",
            expected,
        )
        self.assertIn("canonical output wins", expected)
        self.assertIn("_legacy_migrated/", expected)
        self.assertIn("- `.agent/rules/scribe.md`", expected)

    def test_scribe_rule_matches_generator_byte_for_byte(self) -> None:
        templates = load_templates()
        actual = (REPO_ROOT / ".agent" / "rules" / "scribe.md").read_text(encoding="utf-8")
        expected = templates.render_scribe_rule()

        self.assertEqual(actual, expected)
        self.assertIn("Invariant SAME_PROJECT", expected)
        self.assertIn("Invariant purge/migration sans perte", expected)
        self.assertIn("scribe install --force", expected)
        self.assertIn(".agent/workflow/scribe/sel/docs/scribe.md", expected)

    def test_generated_python_adapters_compile(self) -> None:
        templates = load_templates()
        compile(templates.render_scribe_adapter(), "<render_scribe_adapter>", "exec")
        compile(templates.render_shim_helper(), "<render_shim_helper>", "exec")


if __name__ == "__main__":
    unittest.main()
