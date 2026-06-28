from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from scribe_test_utils import load_script_module


scribe_bootstrap = load_script_module("scribe_bootstrap")
bootstrap_project = getattr(scribe_bootstrap, "bootstrap_project")
create_scribe_from_template = getattr(scribe_bootstrap, "create_scribe_from_template")
ensure_graphify = getattr(scribe_bootstrap, "ensure_graphify")
has_application_code = getattr(scribe_bootstrap, "has_application_code")

scribe_state = load_script_module("scribe_state")
update_state_after_write = getattr(scribe_state, "update_state_after_write")

scribe_install_templates = load_script_module("scribe_install_templates")
render_scribe_adapter = getattr(scribe_install_templates, "render_scribe_adapter")


class ScribeBootstrapTests(unittest.TestCase):
    def run_bootstrap(self, root: Path):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            return bootstrap_project(root, agent="test-agent", agent_type="cli", skip_graphify=True)

    def test_bootstrap_initializes_empty_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            report = self.run_bootstrap(root)

            self.assertTrue(report.new_project)
            self.assertEqual(report.doctor_code, 0)
            self.assertTrue(report.sync_repaired)
            self.assertTrue((root / ".agent" / "workflow" / "scribe" / "scribe").exists())
            self.assertTrue((root / ".agent" / "workflow" / "scribe" / "scribe-rag").exists())
            self.assertTrue((root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").exists())
            self.assertTrue((root / ".agent" / "state" / "outputs" / "scribe-out" / "state.json").exists())
            self.assertTrue((root / "AGENTS.md").exists())
            self.assertTrue((root / ".agent" / "rules" / "scribe.md").exists())
            self.assertTrue((root / ".agent" / ".gitignore").exists())
            self.assertTrue((root / ".graphifyignore").exists())

    def test_bootstrap_detects_package_stack_when_scribe_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                '{"name":"demo-chat","dependencies":{"next":"1","express":"1","socket.io":"1","@prisma/client":"1"}}',
                encoding="utf-8",
            )

            report = self.run_bootstrap(root)
            scribe = (root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").read_text(encoding="utf-8")

            self.assertTrue(report.new_project)
            self.assertIn('project_name: "demo-chat"', scribe)
            self.assertIn('stack: "Node.js / Next.js / Express / Socket.IO / Prisma"', scribe)

    def test_bootstrap_is_idempotent_on_existing_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scribe_path = create_scribe_from_template(root)
            update_state_after_write(
                scribe_path,
                "existing-agent",
                "cli",
                "JOURNAL-000",
                ["PAT-GRAPH-001", "JOURNAL-000"],
                "install",
            )
            before = scribe_path.read_text(encoding="utf-8")

            report = self.run_bootstrap(root)

            self.assertFalse(report.new_project)
            self.assertEqual(report.doctor_code, 0)
            self.assertFalse(report.sync_repaired)
            self.assertEqual(scribe_path.read_text(encoding="utf-8"), before)

    def test_installed_adapter_exposes_tenor_init(self) -> None:
        adapter = render_scribe_adapter()

        self.assertIn('scribe tenor-init [--root PATH]', adapter)
        self.assertIn('"tenor-init": "scribe_tenor_init.py"', adapter)
        self.assertIn('bootstrap, tenor-init, clean', adapter)

    def test_graphify_placeholder_is_info_on_empty_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            status, infos, warnings, errors = ensure_graphify(root, lambda *_: None, skip_graphify=False)

            self.assertFalse(has_application_code(root))
            self.assertEqual(status, "placeholder")
            self.assertIn("Graphify: placeholder initialisé", infos[0])
            self.assertEqual(warnings, [])
            self.assertEqual(errors, [])
            self.assertTrue((root / ".agent" / "state" / "outputs" / "graphify-out" / "GRAPH_REPORT.md").exists())

    def test_graphify_missing_is_error_when_app_code_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text('{"name":"app"}', encoding="utf-8")
            original_which = scribe_bootstrap.shutil.which
            try:
                scribe_bootstrap.shutil.which = lambda _: None
                status, infos, warnings, errors = ensure_graphify(root, lambda *_: None, skip_graphify=False)
            finally:
                scribe_bootstrap.shutil.which = original_which

            self.assertTrue(has_application_code(root))
            self.assertEqual(status, "missing")
            self.assertEqual(infos, [])
            self.assertEqual(warnings, [])
            self.assertIn("Graphify manquant sur projet avec code", errors[0])


if __name__ == "__main__":
    unittest.main()
