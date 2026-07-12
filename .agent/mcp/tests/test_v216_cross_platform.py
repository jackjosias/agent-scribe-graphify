from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
AGENT_DIR = MCP_DIR.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from host_adapter.instructions import install_host_instructions
from host_adapter.launcher import HostLaunchConfig, build_guarded_environment
from runtime import graphify_readiness, installation_state, tenor_init_orchestrator


def make_project(root: Path, *, memory: str | None = None) -> None:
    (root / ".agent" / "mcp").mkdir(parents=True)
    (root / ".agent" / "mcp" / "server_entry.py").write_text("# portable marker\n", encoding="utf-8")
    (root / "README.md").write_text("portable project\n", encoding="utf-8")
    if memory is not None:
        (root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").write_text(memory, encoding="utf-8")


class V216CrossPlatformTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="tenor-v216-portability-")
        self.base = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_unicode_space_project_without_git_is_supported(self) -> None:
        root = self.base / "Projet été Ω avec espaces"
        root.mkdir()
        make_project(root, memory="project: unicode\n")
        plan = tenor_init_orchestrator.prepare_tenor_init(root)
        self.assertTrue(plan.ok)
        self.assertEqual(plan.classification, tenor_init_orchestrator.TENOR_INIT_NEW_INSTALLATION)
        self.assertEqual(plan.memory_action, tenor_init_orchestrator.SCRIBE_MEMORY_ADOPT)
        self.assertTrue(tenor_init_orchestrator.finalize_tenor_init(root)["ok"])
        manifest = json.loads((root / installation_state.INSTALL_MANIFEST_RELATIVE).read_text(encoding="utf-8"))
        self.assertEqual(manifest["project_root"], str(root.resolve()))
        self.assertEqual(manifest["git_root"], "")

    def test_manifest_finalization_is_atomic_under_concurrency(self) -> None:
        root = self.base / "atomic-finalize"
        root.mkdir()
        make_project(root)
        tenor_init_orchestrator.prepare_tenor_init(root)
        barrier = threading.Barrier(8)

        def finalize(_: int) -> bool:
            barrier.wait(timeout=10)
            return bool(tenor_init_orchestrator.finalize_tenor_init(root)["ok"])

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(finalize, range(8)))
        self.assertEqual(results, [True] * 8)
        manifest_path = root / installation_state.INSTALL_MANIFEST_RELATIVE
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(data["init_status"], installation_state.INSTALL_STATUS_READY)
        self.assertEqual(list(manifest_path.parent.glob(f".{manifest_path.name}.*.tmp")), [])

    def test_relocation_purges_only_copied_state_and_preserves_target_memory(self) -> None:
        source = self.base / "source"
        source.mkdir()
        make_project(source, memory="source-memory\n")
        tenor_init_orchestrator.prepare_tenor_init(source)
        tenor_init_orchestrator.finalize_tenor_init(source)
        runtime = source / ".agent" / "state" / "runtime"
        runtime.mkdir(parents=True)
        (runtime / "old-agent.txt").write_text("old\n", encoding="utf-8")

        target = self.base / "target déplacé"
        target.mkdir()
        make_project(target, memory="target-memory-must-survive\n")
        shutil.rmtree(target / ".agent")
        shutil.copytree(source / ".agent", target / ".agent")
        plan = tenor_init_orchestrator.prepare_tenor_init(target)
        self.assertEqual(plan.classification, tenor_init_orchestrator.TENOR_INIT_RELOCATED_PROJECT)
        self.assertTrue(plan.purge_executed)
        self.assertEqual((target / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").read_text(encoding="utf-8"), "target-memory-must-survive\n")
        self.assertFalse((target / ".agent" / "state" / "runtime" / "old-agent.txt").exists())

    def test_six_concurrent_lock_owners_never_overlap(self) -> None:
        root = self.base / "six terminals"
        root.mkdir()
        make_project(root)
        active = 0
        peak = 0
        mutex = threading.Lock()
        barrier = threading.Barrier(6)

        def enter(index: int) -> str:
            nonlocal active, peak
            barrier.wait(timeout=10)
            with tenor_init_orchestrator.tenor_init_lock(root, wait_timeout_seconds=15) as lock:
                lock = tenor_init_orchestrator.refresh_tenor_init_lock(lock, stage=f"agent-{index}")
                with mutex:
                    active += 1
                    peak = max(peak, active)
                try:
                    return str(lock.payload["stage"])
                finally:
                    with mutex:
                        active -= 1

        with ThreadPoolExecutor(max_workers=6) as executor:
            stages = sorted(executor.map(enter, range(6)))
        self.assertEqual(stages, [f"agent-{index}" for index in range(6)])
        self.assertEqual(peak, 1)
        self.assertFalse((root / tenor_init_orchestrator.LOCK_RELATIVE).exists())

    def test_empty_project_graph_is_bound_without_application_sources(self) -> None:
        root = self.base / "empty graph"
        root.mkdir()
        make_project(root)
        output = graphify_readiness.canonical_output_dir(root)
        output.mkdir(parents=True, exist_ok=True)
        (output / "graph.json").write_text('{"nodes":[],"edges":[]}\n', encoding="utf-8")
        (output / "GRAPH_REPORT.md").write_text(
            "# Graph Report\n\nBootstrap placeholder: no application graph has been built yet.\n",
            encoding="utf-8",
        )
        (output / "graph.html").write_text("<html></html>\n", encoding="utf-8")
        written = graphify_readiness.write_graphify_manifest(root, kind="empty_project", purpose="cross_platform_test")
        self.assertTrue(written["ok"])
        ready = graphify_readiness.inspect_graphify_readiness(root)
        self.assertTrue(ready.ok)
        self.assertEqual(ready.verdict, graphify_readiness.GRAPHIFY_EMPTY_PROJECT_READY)

    def test_host_instruction_write_is_atomic_and_unicode_safe(self) -> None:
        root = self.base / "instructions Ω"
        root.mkdir()
        target = root / "AGENTS.md"
        target.write_text("Préambule humain.\n", encoding="utf-8")
        first = install_host_instructions(target, "opencode", root)
        second = install_host_instructions(target, "opencode", root)
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertFalse(second["changed"])
        content = target.read_text(encoding="utf-8")
        self.assertIn("Préambule humain.", content)
        self.assertEqual(content.count("auto-guard:start"), 1)
        self.assertEqual(list(root.glob(".AGENTS.md.*.tmp")), [])

    def test_guarded_environment_uses_native_path_and_preserves_unicode(self) -> None:
        root = self.base / "racine Ω espace"
        root.mkdir()
        config = HostLaunchConfig(agent_id="agent-Ω", host_type="cli", workspace_root=root)
        env = build_guarded_environment(config)
        self.assertEqual(env["AGENT_SCRIBE_GRAPHIFY_ROOT"], str(root.resolve()))
        self.assertEqual(env["AGENT_ID"], "agent-Ω")


if __name__ == "__main__":
    unittest.main()
