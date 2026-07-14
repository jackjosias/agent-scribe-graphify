from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import installation_state, root_hygiene

ENGINE_DIRS = ("mcp", "skills", "workflow", "scripts", "docs", "tests", "rules", "host_adapter")


def make_project(root: Path) -> None:
    (root / ".agent").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("agents\n", encoding="utf-8")
    (root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").write_text("version: 1\n", encoding="utf-8")
    (root / ".agent" / "agent.json").write_text('{"schema_version":"2.14","project_name":"portable"}\n', encoding="utf-8")
    for name in ENGINE_DIRS:
        path = root / ".agent" / name
        path.mkdir(parents=True, exist_ok=True)
        (path / ".keep").write_text("keep\n", encoding="utf-8")
    (root / ".agent" / "mcp" / "server_entry.py").write_text("# marker\n", encoding="utf-8")


def make_legacy_state(root: Path) -> None:
    runtime = root / ".agent" / "state" / "runtime"
    proof = root / ".agent" / "state" / "proof"
    locks = root / ".agent" / "state" / "locks"
    runtime.mkdir(parents=True, exist_ok=True)
    proof.mkdir(parents=True, exist_ok=True)
    locks.mkdir(parents=True, exist_ok=True)
    (runtime / "coordination.sqlite").write_bytes(b"old-db")
    (proof / "proof.json").write_text('{"token":"old"}\n', encoding="utf-8")
    (locks / "active.lock").write_text("old-lock\n", encoding="utf-8")


class InstallationStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        make_project(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def manifest_path(self) -> Path:
        return self.root / ".agent" / "state" / "install" / "agent-installation.json"

    def test_01_first_init_clean_project_creates_manifest(self) -> None:
        result = installation_state.ensure_fresh_installation_state(self.root)
        self.assertEqual(result["verdict"], installation_state.AGENT_INSTALLATION_MANIFEST_CREATED)
        self.assertTrue(self.manifest_path().is_file())
        self.assertEqual(result["manifest"]["init_status"], installation_state.INSTALL_STATUS_PREPARING)

    def test_02_second_init_same_project_does_not_purge_state(self) -> None:
        installation_state.ensure_fresh_installation_state(self.root)
        sentinel = self.root / ".agent" / "state" / "runtime" / "sentinel.txt"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("keep\n", encoding="utf-8")
        result = installation_state.ensure_fresh_installation_state(self.root)
        self.assertEqual(result["verdict"], installation_state.AGENT_INSTALLATION_CURRENT)
        self.assertTrue(sentinel.exists())

    def test_03_manifest_stores_project_root_only_inside_state_install(self) -> None:
        installation_state.ensure_fresh_installation_state(self.root)
        data = json.loads(self.manifest_path().read_text(encoding="utf-8"))
        self.assertEqual(data["project_root"], str(self.root.resolve()))
        self.assertIn(".agent/state/install", str(self.manifest_path()))

    def test_04_agent_json_still_does_not_contain_workspace_root(self) -> None:
        installation_state.ensure_fresh_installation_state(self.root)
        data = json.loads((self.root / ".agent" / "agent.json").read_text(encoding="utf-8"))
        self.assertNotIn("workspace_root", data)
        self.assertNotIn(str(self.root.resolve()), json.dumps(data))

    def test_05_copying_old_state_to_new_project_triggers_relocation(self) -> None:
        installation_state.ensure_fresh_installation_state(self.root)
        installation_state.finalize_installation_state(self.root)
        make_legacy_state(self.root)
        new_root = Path(self.tmp.name) / "new-project"
        new_root.mkdir()
        make_project(new_root)
        self._copy_agent(self.root, new_root)
        result = installation_state.ensure_fresh_installation_state(new_root)
        self.assertEqual(result["verdict"], installation_state.AGENT_BUNDLE_RELOCATION_DETECTED)

    def test_06_relocation_purge_deletes_only_state(self) -> None:
        installation_state.ensure_fresh_installation_state(self.root)
        installation_state.finalize_installation_state(self.root)
        make_legacy_state(self.root)
        new_root = Path(self.tmp.name) / "relocated"
        new_root.mkdir()
        make_project(new_root)
        self._copy_agent(self.root, new_root)
        installation_state.ensure_fresh_installation_state(new_root)
        self.assertFalse((new_root / ".agent" / "state" / "runtime" / "coordination.sqlite").exists())
        self.assertTrue((new_root / ".agent" / "mcp" / "server_entry.py").exists())

    def test_07_preserves_mcp(self) -> None:
        self._assert_preserved_after_relocation("mcp")

    def test_08_preserves_skills(self) -> None:
        self._assert_preserved_after_relocation("skills")

    def test_09_preserves_workflow(self) -> None:
        self._assert_preserved_after_relocation("workflow")

    def test_10_preserves_scripts(self) -> None:
        self._assert_preserved_after_relocation("scripts")

    def test_11_preserves_docs(self) -> None:
        self._assert_preserved_after_relocation("docs")

    def test_12_preserves_tests(self) -> None:
        self._assert_preserved_after_relocation("tests")

    def test_13_preserves_rules(self) -> None:
        self._assert_preserved_after_relocation("rules")

    def test_14_preserves_host_adapter(self) -> None:
        self._assert_preserved_after_relocation("host_adapter")

    def test_15_relocation_does_not_delete_agent_json(self) -> None:
        self._assert_preserved_after_relocation("agent.json")

    def test_16_legacy_state_without_manifest_is_purged(self) -> None:
        make_legacy_state(self.root)
        result = installation_state.ensure_fresh_installation_state(self.root)
        self.assertEqual(result["verdict"], installation_state.LEGACY_STATE_WITHOUT_INSTALL_MANIFEST_PURGED)
        self.assertFalse((self.root / ".agent" / "state" / "runtime" / "coordination.sqlite").exists())

    def test_17_empty_state_without_manifest_creates_manifest_without_purge(self) -> None:
        (self.root / ".agent" / "state").mkdir(parents=True)
        result = installation_state.ensure_fresh_installation_state(self.root)
        self.assertEqual(result["verdict"], installation_state.AGENT_INSTALLATION_MANIFEST_CREATED)
        self.assertTrue(self.manifest_path().exists())

    def test_18_corrupt_manifest_is_purged_and_recreated(self) -> None:
        self.manifest_path().parent.mkdir(parents=True)
        self.manifest_path().write_text("{not-json", encoding="utf-8")
        result = installation_state.ensure_fresh_installation_state(self.root)
        self.assertEqual(result["verdict"], installation_state.CORRUPT_INSTALLATION_MANIFEST_PURGED)
        self.assertEqual(json.loads(self.manifest_path().read_text(encoding="utf-8"))["schema"], installation_state.INSTALL_SCHEMA)

    def test_19_purge_refuses_if_server_marker_missing(self) -> None:
        (self.root / ".agent" / "mcp" / "server_entry.py").unlink()
        result = installation_state.purge_project_bound_state(self.root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], installation_state.PROJECT_BOUND_STATE_PURGE_REFUSED)

    def test_20_purge_refuses_symlink_state_pointing_outside_agent(self) -> None:
        outside = Path(self.tmp.name) / "outside-state"
        outside.mkdir()
        state = self.root / ".agent" / "state"
        if state.exists():
            state.rmdir()
        state.symlink_to(outside, target_is_directory=True)
        result = installation_state.purge_project_bound_state(self.root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], installation_state.PROJECT_BOUND_STATE_PURGE_REFUSED)
        self.assertTrue(outside.exists())

    def test_21_after_relocation_old_agents_are_gone(self) -> None:
        self._old_sqlite_state()
        new_root = self._relocate_from_current()
        installation_state.ensure_fresh_installation_state(new_root)
        self.assertFalse((new_root / ".agent" / "state" / "runtime" / "coordination.sqlite").exists())

    def test_22_after_relocation_active_locks_are_gone(self) -> None:
        make_legacy_state(self.root)
        new_root = self._relocate_from_current()
        installation_state.ensure_fresh_installation_state(new_root)
        self.assertFalse((new_root / ".agent" / "state" / "locks" / "active.lock").exists())

    def test_23_after_relocation_old_proof_tokens_are_unusable(self) -> None:
        make_legacy_state(self.root)
        new_root = self._relocate_from_current()
        installation_state.ensure_fresh_installation_state(new_root)
        self.assertFalse((new_root / ".agent" / "state" / "proof" / "proof.json").exists())

    def test_24_tenor_init_fresh_runtime_ready_verdict_is_reported(self) -> None:
        result = installation_state.ensure_fresh_installation_state(self.root)
        self.assertEqual(result["runtime_verdict"], installation_state.TENOR_INIT_FRESH_RUNTIME_READY)

    def test_25_root_hygiene_remains_ok_after_purge(self) -> None:
        make_legacy_state(self.root)
        installation_state.ensure_fresh_installation_state(self.root)
        report = root_hygiene.inspect_root_hygiene(self.root, max_parent_depth=0)
        self.assertIn(report["verdict"], {root_hygiene.ROOT_HYGIENE_OK, root_hygiene.ROOT_HYGIENE_WARN})

    def test_26_server_gate_is_read_only_and_requires_tenor_init(self) -> None:
        state = self.root / ".agent" / "state"
        self.assertFalse(state.exists())
        gate = installation_state.inspect_installation_state(self.root)
        self.assertFalse(gate["ready"])
        self.assertEqual(gate["verdict"], installation_state.TENOR_INIT_REQUIRED)
        self.assertFalse(state.exists(), "read-only gate must not create state")

    def test_27_preparing_manifest_does_not_enable_server(self) -> None:
        installation_state.ensure_fresh_installation_state(self.root)
        gate = installation_state.inspect_installation_state(self.root)
        self.assertFalse(gate["ready"])
        self.assertEqual(gate["detection"]["verdict"], installation_state.AGENT_INSTALLATION_INIT_INCOMPLETE)

    def test_28_finalize_enables_server_gate(self) -> None:
        installation_state.ensure_fresh_installation_state(self.root)
        final = installation_state.finalize_installation_state(self.root)
        self.assertTrue(final["ok"])
        self.assertTrue(installation_state.inspect_installation_state(self.root)["ready"])

    def test_29_relocation_inspection_never_purges_state(self) -> None:
        installation_state.ensure_fresh_installation_state(self.root)
        installation_state.finalize_installation_state(self.root)
        new_root = self._relocate_from_current()
        old = new_root / ".agent" / "state" / "runtime" / "must-survive-inspection.txt"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("old\n", encoding="utf-8")
        gate = installation_state.inspect_installation_state(new_root)
        self.assertFalse(gate["ready"])
        self.assertTrue(old.exists())

    def test_30_finalize_refreshes_fingerprint_after_memory_creation(self) -> None:
        (self.root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").unlink()
        installation_state.ensure_fresh_installation_state(self.root)
        (self.root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").write_text("created later\n", encoding="utf-8")
        final = installation_state.finalize_installation_state(self.root)
        self.assertTrue(final["ok"])
        self.assertEqual(
            json.loads(self.manifest_path().read_text(encoding="utf-8"))["project_markers"]["AGENT-MEMOIRE_PROJECT_STATUS.scribe"],
            True,
        )

    def test_31_next_action_uses_current_interpreter_not_bare_python(self) -> None:
        gate = installation_state.inspect_installation_state(self.root)
        argv = gate["next_action_argv"]
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1:], [".agent/workflow/scribe/scribe", "tenor-init", "--type", "cli"])
        self.assertIn(sys.executable, gate["next_action"])

    def _copy_agent(self, old_root: Path, new_root: Path) -> None:
        if (new_root / ".agent").exists():
            import shutil
            shutil.rmtree(new_root / ".agent")
        import shutil
        shutil.copytree(old_root / ".agent", new_root / ".agent")

    def _relocate_from_current(self) -> Path:
        installation_state.ensure_fresh_installation_state(self.root)
        installation_state.finalize_installation_state(self.root)
        new_root = Path(self.tmp.name) / f"relocated-{len(list(Path(self.tmp.name).iterdir()))}"
        new_root.mkdir()
        make_project(new_root)
        self._copy_agent(self.root, new_root)
        return new_root

    def _assert_preserved_after_relocation(self, name: str) -> None:
        make_legacy_state(self.root)
        new_root = self._relocate_from_current()
        installation_state.ensure_fresh_installation_state(new_root)
        target = new_root / ".agent" / name
        self.assertTrue(target.exists(), f"{name} should be preserved")

    def _old_sqlite_state(self) -> None:
        installation_state.ensure_fresh_installation_state(self.root)
        installation_state.finalize_installation_state(self.root)
        runtime = self.root / ".agent" / "state" / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(runtime / "coordination.sqlite")
        try:
            con.execute("CREATE TABLE agents(agent_id TEXT, status TEXT)")
            con.execute("INSERT INTO agents(agent_id,status) VALUES('old-agent','active')")
            con.commit()
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
