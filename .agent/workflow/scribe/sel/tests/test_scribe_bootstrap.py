from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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


def plan(root: Path, *, classification: str, memory_action: str, project_changed: bool) -> SimpleNamespace:
    return SimpleNamespace(
        project_root=str(root.resolve()),
        classification=classification,
        memory_action=memory_action,
        project_changed=project_changed,
    )


class ScribeBootstrapTests(unittest.TestCase):
    def run_bootstrap(
        self,
        root: Path,
        *,
        classification: str,
        memory_action: str,
        project_changed: bool,
        skip_graphify: bool = True,
    ):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            return bootstrap_project(
                root,
                agent="test-agent",
                agent_type="cli",
                skip_graphify=skip_graphify,
                installation_plan=plan(
                    root,
                    classification=classification,
                    memory_action=memory_action,
                    project_changed=project_changed,
                ),
            )

    def test_bootstrap_initializes_empty_project_with_bound_graph_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = self.run_bootstrap(
                root,
                classification="TENOR_INIT_NEW_INSTALLATION",
                memory_action="SCRIBE_MEMORY_CREATE",
                project_changed=True,
                skip_graphify=False,
            )

            self.assertTrue(report.new_project)
            self.assertEqual(report.scribe_status, "created")
            self.assertEqual(report.graphify_status, "placeholder")
            self.assertEqual(report.errors, [])
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
            graph_dir = root / ".agent" / "state" / "outputs" / "graphify-out"
            for name in ("GRAPH_REPORT.md", "graph.json", "graph.html", "GRAPHIFY_READY.json"):
                self.assertTrue((graph_dir / name).is_file(), name)

    def test_bootstrap_detects_package_stack_when_memory_creation_is_authorized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                '{"name":"demo-chat","dependencies":{"next":"1","express":"1","socket.io":"1","@prisma/client":"1"}}',
                encoding="utf-8",
            )

            report = self.run_bootstrap(
                root,
                classification="TENOR_INIT_NEW_INSTALLATION",
                memory_action="SCRIBE_MEMORY_CREATE",
                project_changed=True,
                skip_graphify=True,
            )
            scribe = (root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").read_text(encoding="utf-8")

            self.assertEqual(report.scribe_status, "created")
            self.assertIn('project_name: "demo-chat"', scribe)
            self.assertIn('stack: "Node.js / Next.js / Express / Socket.IO / Prisma"', scribe)

    def test_bootstrap_adopts_existing_memory_without_rewriting_it(self) -> None:
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

            report = self.run_bootstrap(
                root,
                classification="TENOR_INIT_SAME_PROJECT",
                memory_action="SCRIBE_MEMORY_ADOPT",
                project_changed=False,
            )

            self.assertFalse(report.new_project)
            self.assertEqual(report.scribe_status, "adopted")
            self.assertEqual(report.doctor_code, 0)
            self.assertFalse(report.sync_repaired)
            self.assertEqual(scribe_path.read_text(encoding="utf-8"), before)

    def test_bootstrap_refuses_to_infer_project_identity_without_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "TENOR_INIT_PLAN_REQUIRED"):
                bootstrap_project(Path(tmp), agent="test-agent", agent_type="cli", skip_graphify=True)

    def test_adopt_action_fails_closed_when_memory_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = self.run_bootstrap(
                root,
                classification="TENOR_INIT_SAME_PROJECT",
                memory_action="SCRIBE_MEMORY_ADOPT",
                project_changed=False,
            )
            self.assertEqual(report.scribe_status, "missing")
            self.assertTrue(report.errors)
            self.assertFalse((root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").exists())

    def test_installed_adapter_exposes_v216_tenor_init(self) -> None:
        adapter = render_scribe_adapter()
        self.assertIn('scribe tenor-init [--root PATH]', adapter)
        self.assertIn('"tenor-init": "scribe_tenor_init_v216.py"', adapter)
        compile(adapter, "<installed-scribe-adapter>", "exec")

    def test_graphify_placeholder_is_project_bound_on_empty_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status, infos, warnings, errors = ensure_graphify(root, lambda *_: None, skip_graphify=False)
            self.assertFalse(has_application_code(root))
            self.assertEqual(status, "placeholder")
            self.assertIn("empty-project placeholder bound", infos[0])
            self.assertEqual(warnings, [])
            self.assertEqual(errors, [])
            graph_dir = root / ".agent" / "state" / "outputs" / "graphify-out"
            self.assertTrue((graph_dir / "GRAPHIFY_READY.json").is_file())

    def test_graphify_requires_explicit_bounded_build_when_app_code_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text('{"name":"app"}', encoding="utf-8")
            status, infos, warnings, errors = ensure_graphify(root, lambda *_: None, skip_graphify=False)

            self.assertTrue(has_application_code(root))
            self.assertEqual(status, "build_required")
            self.assertEqual(infos, [])
            self.assertEqual(warnings, [])
            self.assertTrue(any("Graphify not ready" in error for error in errors))
            self.assertTrue(any("graph --project-build --timeout 180" in error for error in errors))
            self.assertFalse((root / ".agent" / "state" / "outputs" / "graphify-out" / "GRAPHIFY_READY.json").exists())


if __name__ == "__main__":
    unittest.main()
