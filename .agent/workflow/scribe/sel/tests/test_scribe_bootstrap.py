from __future__ import annotations

import contextlib
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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
render_scribe_rule = getattr(scribe_install_templates, "render_scribe_rule")
render_agents_block = getattr(scribe_install_templates, "render_agents_block")


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
        self.assertIn("internal/legacy primitive", adapter)
        compile(adapter, "<installed-scribe-adapter>", "exec")

    def test_generated_surfaces_use_only_canonical_v216_entry(self) -> None:
        trigger = "TENOR INIT::[.agent/skills/init-tenor/SKILL.md]"
        for rendered in (render_scribe_rule(), render_agents_block()):
            self.assertIn(trigger, rendered)
            self.assertIn("tenor-init --type", rendered)
            self.assertIn("bootstrap", rendered)
            self.assertIn("legacy", rendered)
            self.assertNotIn("[[.agent/skills/init-tenor/SKILL.md]]", rendered)
            self.assertNotIn("fall back to bootstrap", rendered.lower())
            self.assertNotIn("fallback to bootstrap", rendered.lower())
            self.assertIn("DOCUMENTATION_SYNC_POLICY.md", rendered)

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

    def test_same_project_session_init_is_tracked_file_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "AGENTS.md"
            scribe_rule = root / ".agent" / "rules" / "scribe.md"
            graphify_ignore = root / ".graphifyignore"
            scribe_rule.parent.mkdir(parents=True, exist_ok=True)
            agents.write_text("SENTINEL-AGENTS-NO-TOUCH\n", encoding="utf-8")
            scribe_rule.write_text("SENTINEL-SCRIBE-RULE-NO-TOUCH\n", encoding="utf-8")
            graphify_ignore.write_text("SENTINEL-GRAPHIFYIGNORE-NO-TOUCH\n", encoding="utf-8")
            agents_hash = hashlib.sha256(agents.read_bytes()).hexdigest()
            scribe_rule_hash = hashlib.sha256(scribe_rule.read_bytes()).hexdigest()
            graphify_ignore_hash = hashlib.sha256(graphify_ignore.read_bytes()).hexdigest()

            scribe_path = create_scribe_from_template(root)
            update_state_after_write(
                scribe_path,
                "existing-agent",
                "cli",
                "JOURNAL-000",
                ["PAT-GRAPH-001", "JOURNAL-000"],
                "install",
            )

            with mock.patch.object(scribe_bootstrap, "run_installer") as installer:
                report = self.run_bootstrap(
                    root,
                    classification="TENOR_INIT_SAME_PROJECT",
                    memory_action="SCRIBE_MEMORY_ADOPT",
                    project_changed=False,
                )

            installer.assert_not_called()

            self.assertEqual(agents.read_text(encoding="utf-8"), "SENTINEL-AGENTS-NO-TOUCH\n")
            self.assertEqual(scribe_rule.read_text(encoding="utf-8"), "SENTINEL-SCRIBE-RULE-NO-TOUCH\n")
            self.assertEqual(graphify_ignore.read_text(encoding="utf-8"), "SENTINEL-GRAPHIFYIGNORE-NO-TOUCH\n")
            self.assertEqual(hashlib.sha256(agents.read_bytes()).hexdigest(), agents_hash)
            self.assertEqual(hashlib.sha256(scribe_rule.read_bytes()).hexdigest(), scribe_rule_hash)
            self.assertEqual(hashlib.sha256(graphify_ignore.read_bytes()).hexdigest(), graphify_ignore_hash)

            self.assertEqual(report.scribe_status, "adopted")
            self.assertFalse(report.new_project)
            self.assertEqual(report.errors, [])
            self.assertEqual(report.doctor_code, 0)
            self.assertIn("SAME_PROJECT read-only session init (installer skipped)", report.actions)

    def test_new_installation_still_calls_installer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "AGENTS.md"
            scribe_rule = root / ".agent" / "rules" / "scribe.md"
            graphify_ignore = root / ".graphifyignore"
            scribe_rule.parent.mkdir(parents=True, exist_ok=True)
            agents.write_text("SENTINEL-AGENTS\n", encoding="utf-8")
            scribe_rule.write_text("SENTINEL-SCRIBE-RULE\n", encoding="utf-8")
            graphify_ignore.write_text("SENTINEL-GRAPHIFYIGNORE\n", encoding="utf-8")

            scribe_path = create_scribe_from_template(root)
            update_state_after_write(
                scribe_path,
                "existing-agent",
                "cli",
                "JOURNAL-000",
                ["PAT-GRAPH-001", "JOURNAL-000"],
                "install",
            )

            with mock.patch.object(scribe_bootstrap, "run_installer", return_value=0) as installer:
                report = self.run_bootstrap(
                    root,
                    classification="TENOR_INIT_NEW_INSTALLATION",
                    memory_action="SCRIBE_MEMORY_ADOPT",
                    project_changed=True,
                )

            installer.assert_called_once()
            self.assertIn("rootless install verified", report.actions)


    def test_same_project_signals_drift_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "AGENTS.md"
            scribe_rule = root / ".agent" / "rules" / "scribe.md"
            graphify_ignore = root / ".graphifyignore"
            scribe_rule.parent.mkdir(parents=True, exist_ok=True)
            agents.write_text("SENTINEL-AGENTS-NO-TOUCH\n", encoding="utf-8")
            scribe_rule.write_text("SENTINEL-SCRIBE-RULE-NO-TOUCH\n", encoding="utf-8")
            graphify_ignore.write_text("SENTINEL-GRAPHIFYIGNORE-NO-TOUCH\n", encoding="utf-8")
            scribe_path = create_scribe_from_template(root)
            update_state_after_write(scribe_path, "existing-agent", "cli", "JOURNAL-000", ["JOURNAL-000"], "install")
            with mock.patch.object(scribe_bootstrap, "run_installer") as installer:
                report = self.run_bootstrap(root, classification="TENOR_INIT_SAME_PROJECT", memory_action="SCRIBE_MEMORY_ADOPT", project_changed=False)
            installer.assert_not_called()
            self.assertEqual(report.scribe_status, "adopted")
            self.assertEqual(report.errors, [])
            self.assertEqual(report.doctor_code, 0)
            drift_warnings = [w for w in report.warnings if "Bundle drift detected" in w]
            self.assertTrue(drift_warnings, "expected at least one drift warning")
            self.assertTrue(all("scribe install --force" in w for w in drift_warnings))
            self.assertEqual(agents.read_text(encoding="utf-8"), "SENTINEL-AGENTS-NO-TOUCH\n")

    def test_same_project_with_missing_or_corrupt_managed_files_is_still_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scribe_path = create_scribe_from_template(root)
            update_state_after_write(scribe_path, "existing-agent", "cli", "JOURNAL-000", ["JOURNAL-000"], "install")
            (root / "AGENTS.md").mkdir()
            with mock.patch.object(scribe_bootstrap, "run_installer") as installer:
                report = self.run_bootstrap(root, classification="TENOR_INIT_SAME_PROJECT", memory_action="SCRIBE_MEMORY_ADOPT", project_changed=False)
            installer.assert_not_called()
            self.assertEqual(report.scribe_status, "adopted")
            self.assertEqual(report.errors, [])
            self.assertEqual(report.doctor_code, 0)

    def test_drift_warning_references_supported_install_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "AGENTS.md"
            scribe_rule = root / ".agent" / "rules" / "scribe.md"
            graphify_ignore = root / ".graphifyignore"
            scribe_rule.parent.mkdir(parents=True, exist_ok=True)
            agents.write_text("SENTINEL-AGENTS\n", encoding="utf-8")
            scribe_rule.write_text("SENTINEL-SCRIBE-RULE\n", encoding="utf-8")
            graphify_ignore.write_text("SENTINEL-GRAPHIFYIGNORE\n", encoding="utf-8")
            scribe_path = create_scribe_from_template(root)
            update_state_after_write(scribe_path, "existing-agent", "cli", "JOURNAL-000", ["JOURNAL-000"], "install")
            with mock.patch.object(scribe_bootstrap, "run_installer"):
                report = self.run_bootstrap(root, classification="TENOR_INIT_SAME_PROJECT", memory_action="SCRIBE_MEMORY_ADOPT", project_changed=False)
            self.assertTrue(any("scribe install --force" in w for w in report.warnings))
            repo_launcher = Path(__file__).resolve().parent.parent / "scribe"
            repo_root = Path(__file__).resolve().parents[5]
            result = subprocess.run([sys.executable, str(repo_launcher), "install", "--help"], cwd=repo_root, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--force", result.stdout + result.stderr)


class AtomicTextWriteHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_atomic_text_write_uses_exclusive_sibling_temp(self) -> None:
        target = self.root / "GRAPH_REPORT.md"
        captured: dict[str, object] = {}
        real_mkstemp = tempfile.mkstemp

        def fake_mkstemp(*args, **kwargs):
            captured["dir"] = kwargs.get("dir")
            captured["prefix"] = kwargs.get("prefix")
            captured["suffix"] = kwargs.get("suffix")
            fd, name = real_mkstemp(*args, **kwargs)
            captured["tmp_name"] = name
            return fd, name

        content = "# Graph Report\n\nBootstrap placeholder.\n"
        with mock.patch.object(tempfile, "mkstemp", fake_mkstemp):
            scribe_bootstrap._atomic_text_write(target, content)
        self.assertEqual(captured["dir"], str(target.parent))
        self.assertTrue(str(captured["prefix"]).startswith(f".{target.name}."))
        self.assertEqual(captured["suffix"], ".tmp")
        self.assertNotIn(str(os.getpid()), captured["tmp_name"])
        self.assertNotIn(str(time.time_ns()), captured["tmp_name"])
        self.assertEqual(target.read_text(encoding="utf-8"), content)

    def test_atomic_text_write_concurrent_no_orphans(self) -> None:
        target = self.root / "graph.json"
        marker = "Q" * 4096

        def write(n: int) -> None:
            scribe_bootstrap._atomic_text_write(target, f'{{"n":{n},"marker":"{marker}"}}\n')

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(write, range(64)))
        text = target.read_text(encoding="utf-8")
        self.assertIn(marker, text)
        orphans = [p for p in target.parent.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(orphans, [])


if __name__ == "__main__":
    unittest.main()
