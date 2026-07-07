from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / ".agent" / "scripts" / "agent_runtime_sync.py"
spec = importlib.util.spec_from_file_location("agent_runtime_sync", MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("agent_runtime_sync module cannot be loaded")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_source(root: Path) -> None:
    write(root / ".agent" / "mcp" / "server.py", "candidate-mcp\n")
    write(root / ".agent" / "scripts" / "tool.py", "candidate-script\n")
    write(root / ".agent" / "rules" / "scribe.md", "candidate-rule\n")
    write(root / ".agent" / "skills" / "init-tenor" / "SKILL.md", "candidate-skill\n")
    write(root / ".agent" / "docs" / "hosts" / "codex.md", "candidate-doc\n")
    write(root / ".agent" / "host_adapter" / "adapter.py", "candidate-adapter\n")
    write(root / ".agent" / "workflow" / "scribe" / "rag" / "engine.py", "candidate-rag\n")
    write(root / ".agent" / "workflow" / "scribe" / "sel" / "engine.py", "candidate-sel\n")
    write(root / ".agent" / "workflow" / "scribe" / "scribe", "#!/bin/sh\necho scribe\n")
    write(root / ".agent" / "workflow" / "scribe" / "scribe-rag", "#!/bin/sh\necho rag\n")
    write(root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe", "source-memory-must-not-copy\n")
    write(root / ".agent" / "agent.json", '{"source":"must-not-copy"}\n')
    write(root / ".agent" / "state" / "runtime" / "coordination.sqlite", "source-db-must-not-copy\n")


def make_target(root: Path) -> None:
    write(root / ".agent" / "mcp" / "server.py", "stale-mcp\n")
    write(root / ".agent" / "scripts" / "tool.py", "stale-script\n")
    write(root / ".agent" / "rules" / "scribe.md", "stale-rule\n")
    write(root / ".agent" / "skills" / "init-tenor" / "SKILL.md", "stale-skill\n")
    write(root / ".agent" / "docs" / "hosts" / "codex.md", "stale-doc\n")
    write(root / ".agent" / "host_adapter" / "adapter.py", "stale-adapter\n")
    write(root / ".agent" / "workflow" / "scribe" / "rag" / "engine.py", "stale-rag\n")
    write(root / ".agent" / "workflow" / "scribe" / "sel" / "engine.py", "stale-sel\n")
    write(root / ".agent" / "workflow" / "scribe" / "scribe", "stale-scribe\n")
    write(root / ".agent" / "workflow" / "scribe" / "scribe-rag", "stale-rag-bin\n")
    write(root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe", "target-memory\n")
    write(root / ".agent" / "agent.json", '{"target":"local"}\n')
    write(root / ".agent" / "mcp_config.json", '{"local":"config"}\n')
    write(root / ".agent" / "state" / "runtime" / "coordination.sqlite", "target-db\n")
    write(root / ".agent" / "state" / "outputs" / "graphify-out" / "graph.json", "target-state-output\n")
    write(root / ".agent" / "workflow" / "scribe" / "sel" / "state" / "coordination.json", '{"coord":"target-sel-state"}\n')
    write(root / ".agent" / "workflow" / "scribe" / "sel" / "archive" / "old-scribe.md", "target-sel-archive\n")
    write(root / "graphify-out" / "graph.json", "target-root-graph\n")
    write(root / "scribe-out" / "out.txt", "target-root-scribe\n")


class AgentRuntimeSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.tmp.name) / "source"
        self.target = Path(self.tmp.name) / "target"
        self.source.mkdir()
        self.target.mkdir()
        make_source(self.source)
        make_target(self.target)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_01_dry_run_builds_plan_without_modifying_target(self) -> None:
        plan = sync.build_plan(self.source, self.target)
        self.assertEqual(plan["verdict"], sync.SKIP_MANIFEST)
        self.assertGreaterEqual(plan["summary"]["replace"], 1)
        self.assertEqual((self.target / ".agent" / "mcp" / "server.py").read_text(encoding="utf-8"), "stale-mcp\n")
        self.assertFalse((self.target / ".agent" / "state" / "runtime-sync" / "last-sync.json").exists())

    def test_02_apply_updates_runtime_only_and_preserves_project_state(self) -> None:
        result = sync.apply_plan(self.source, self.target, sync.build_plan(self.source, self.target))
        self.assertEqual(result["verdict"], sync.APPLY_MANIFEST)
        self.assertEqual((self.target / ".agent" / "mcp" / "server.py").read_text(encoding="utf-8"), "candidate-mcp\n")
        self.assertEqual((self.target / ".agent" / "scripts" / "tool.py").read_text(encoding="utf-8"), "candidate-script\n")
        self.assertEqual((self.target / ".agent" / "workflow" / "scribe" / "sel" / "engine.py").read_text(encoding="utf-8"), "candidate-sel\n")
        self.assertEqual((self.target / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").read_text(encoding="utf-8"), "target-memory\n")
        self.assertEqual((self.target / ".agent" / "agent.json").read_text(encoding="utf-8"), '{"target":"local"}\n')
        self.assertEqual((self.target / ".agent" / "mcp_config.json").read_text(encoding="utf-8"), '{"local":"config"}\n')
        self.assertEqual((self.target / ".agent" / "state" / "runtime" / "coordination.sqlite").read_text(encoding="utf-8"), "target-db\n")
        self.assertEqual((self.target / "graphify-out" / "graph.json").read_text(encoding="utf-8"), "target-root-graph\n")
        self.assertEqual((self.target / "scribe-out" / "out.txt").read_text(encoding="utf-8"), "target-root-scribe\n")
        self.assertEqual((self.target / ".agent" / "workflow" / "scribe" / "sel" / "engine.py").read_text(encoding="utf-8"), "candidate-sel\n")
        self.assertEqual((self.target / ".agent" / "workflow" / "scribe" / "sel" / "state" / "coordination.json").read_text(encoding="utf-8"), '{"coord":"target-sel-state"}\n')
        self.assertEqual((self.target / ".agent" / "workflow" / "scribe" / "sel" / "archive" / "old-scribe.md").read_text(encoding="utf-8"), "target-sel-archive\n")

    def test_03_apply_writes_manifest_and_backups_replaced_runtime(self) -> None:
        result = sync.apply_plan(self.source, self.target, sync.build_plan(self.source, self.target))
        manifest = Path(result["manifest_path"])
        self.assertTrue(manifest.is_file())
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data["verdict"], sync.APPLY_MANIFEST)
        backup_root = Path(result["backup_root"])
        self.assertEqual((backup_root / ".agent" / "mcp" / "server.py").read_text(encoding="utf-8"), "stale-mcp\n")

    def test_04_target_without_agent_is_refused(self) -> None:
        no_agent = Path(self.tmp.name) / "no-agent"
        no_agent.mkdir()
        with self.assertRaises(sync.SyncError) as ctx:
            sync.build_plan(self.source, no_agent)
        self.assertEqual(ctx.exception.reason, "target_agent_missing")

    def test_05_same_source_and_target_is_refused(self) -> None:
        with self.assertRaises(sync.SyncError) as ctx:
            sync.build_plan(self.source, self.source)
        self.assertEqual(ctx.exception.reason, "source_target_same_refused")

    def test_06_target_symlinked_runtime_path_is_refused(self) -> None:
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        target_mcp = self.target / ".agent" / "mcp"
        import shutil
        shutil.rmtree(target_mcp)
        target_mcp.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(sync.SyncError) as ctx:
            sync.build_plan(self.source, self.target)
        self.assertEqual(ctx.exception.reason, "target_symlink_refused")

    def test_07_source_missing_optional_path_is_reported_not_applied(self) -> None:
        import shutil
        shutil.rmtree(self.source / ".agent" / "host_adapter")
        plan = sync.build_plan(self.source, self.target)
        missing = [item for item in plan["actions"] if item["path"] == ".agent/host_adapter"]
        self.assertEqual(missing[0]["action"], "source_missing")
        sync.apply_plan(self.source, self.target, plan)
        self.assertEqual((self.target / ".agent" / "host_adapter" / "adapter.py").read_text(encoding="utf-8"), "stale-adapter\n")

    def test_08_cli_dry_run_outputs_json_without_manifest(self) -> None:
        rc = sync.main(["--source", str(self.source), "--target", str(self.target)])
        self.assertEqual(rc, 0)
        self.assertFalse((self.target / ".agent" / "state" / "runtime-sync" / "last-sync.json").exists())


if __name__ == "__main__":
    unittest.main()
