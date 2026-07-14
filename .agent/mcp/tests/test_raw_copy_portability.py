from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
AGENT_DIR = MCP_DIR.parent
REPO_ROOT = AGENT_DIR.parent
SEL_SCRIPTS = AGENT_DIR / "workflow" / "scribe" / "sel" / "scripts"
for path in (MCP_DIR, AGENT_DIR, SEL_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from host_adapter import host_config
from proof_signer import consume_agent_proof, issue_proof
from runtime import graphify_readiness, installation_state, tenor_init_orchestrator


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project_markers(root: Path, *, memory: str | None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("portable terrain fixture\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("terrain instructions\n", encoding="utf-8")
    if memory is not None:
        (root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").write_text(memory, encoding="utf-8")


def _copy_complete_agent(source: Path, target: Path) -> None:
    destination = target / ".agent"
    if destination.exists():
        shutil.rmtree(destination)

    source_agent = (source / ".agent").resolve()

    def copy_ignore(directory: str, names: list[str]) -> set[str]:
        ignored = set(shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")(directory, names))
        if Path(directory).resolve() == source_agent / "state":
            ignored.add("smoke")
        return ignored

    shutil.copytree(
        source / ".agent",
        destination,
        ignore=copy_ignore,
    )


class RawCopyPortabilityAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="raw-copy-portability-")
        self.base = Path(self.tmp.name)
        self.source = self.base / "source agent Ω"
        _project_markers(self.source, memory="source-memory-must-not-be-imported\n")
        _copy_complete_agent(REPO_ROOT, self.source)
        prepared = tenor_init_orchestrator.prepare_tenor_init(self.source)
        self.assertTrue(prepared.ok)
        self.assertTrue(tenor_init_orchestrator.finalize_tenor_init(self.source)["ok"])
        self.assertTrue(graphify_readiness.write_smoke_fixture(self.source)["ok"])
        stale_runtime = self.source / ".agent" / "state" / "runtime" / "source-agent.txt"
        stale_runtime.parent.mkdir(parents=True, exist_ok=True)
        stale_runtime.write_text("must be purged on relocation\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_full_raw_copy_relocates_purges_and_adopts_destination_memory(self) -> None:
        target = self.base / "destination project été"
        destination_memory = "destination-memory-must-survive-byte-for-byte\n"
        _project_markers(target, memory=destination_memory)
        graph_source = self.source / ".agent" / "state" / "outputs" / "graphify-out" / "graph.json"
        graph_digest = _digest(graph_source)
        _copy_complete_agent(self.source, target)

        plan = tenor_init_orchestrator.prepare_tenor_init(target)

        self.assertEqual(plan.classification, tenor_init_orchestrator.TENOR_INIT_RELOCATED_PROJECT)
        self.assertTrue(plan.relocated)
        self.assertTrue(plan.purge_executed)
        self.assertEqual(plan.memory_action, tenor_init_orchestrator.SCRIBE_MEMORY_ADOPT)
        self.assertEqual(
            (target / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").read_text(encoding="utf-8"),
            destination_memory,
        )
        self.assertFalse((target / ".agent" / "state" / "runtime" / "source-agent.txt").exists())
        graph_target = target / ".agent" / "state" / "outputs" / "graphify-out" / "graph.json"
        self.assertEqual(_digest(graph_target), graph_digest)
        readiness = graphify_readiness.inspect_graphify_readiness(target, allow_fixture=False)
        self.assertFalse(readiness.ok)
        self.assertIn(readiness.verdict, {"GRAPHIFY_STALE_ROOT", "GRAPHIFY_STALE_WORKSPACE", "GRAPHIFY_SMOKE_FIXTURE_FORBIDDEN"})

    def test_full_raw_copy_without_destination_memory_selects_create(self) -> None:
        target = self.base / "new project without memory"
        _project_markers(target, memory=None)
        _copy_complete_agent(self.source, target)

        plan = tenor_init_orchestrator.prepare_tenor_init(target)

        self.assertEqual(plan.classification, tenor_init_orchestrator.TENOR_INIT_RELOCATED_PROJECT)
        self.assertEqual(plan.memory_action, tenor_init_orchestrator.SCRIBE_MEMORY_CREATE)
        self.assertFalse((target / "AGENT-MEMOIRE_PROJECT_STATUS.scribe").exists())

    def test_opencode_configuration_preserves_comments_and_unrelated_servers(self) -> None:
        target = self.base / "opencode project"
        _project_markers(target, memory="memory\n")
        _copy_complete_agent(self.source, target)
        tenor_init_orchestrator.prepare_tenor_init(target)
        config = target / "opencode.jsonc"
        config.write_text(
            "{\n"
            "  // human comment must survive\n"
            '  "model": "team\'s/model",\n'
            '  "mcp": {"other-server": {"type": "remote", "url": "https://example.invalid"}},\n'
            '  "permission": {"read": "allow"}\n'
            "}\n",
            encoding="utf-8",
        )

        first = host_config.configure_host(target, explicit="opencode")
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["verdict"], host_config.HOST_CONFIG_UPDATED_RESTART_REQUIRED)
        content = config.read_text(encoding="utf-8")
        self.assertIn("// human comment must survive", content)
        parsed = host_config._load_jsonc(content)
        self.assertEqual(parsed["model"], "team's/model")
        self.assertEqual(parsed["mcp"]["other-server"]["url"], "https://example.invalid")
        server = parsed["mcp"][host_config.SERVER_NAME]
        self.assertEqual(server["command"], ["python" if sys.platform == "win32" else "python3", ".agent/mcp/server_entry.py"])
        self.assertEqual(server["cwd"], ".")
        self.assertEqual(parsed["permission"]["edit"], "deny")
        self.assertEqual(parsed["permission"]["bash"]["*"], "deny")
        self.assertEqual(
            parsed["permission"]["bash"][".agent/workflow/scribe/scribe tenor-init --type cli --host opencode"],
            "allow",
        )

        second = host_config.configure_host(target, explicit="opencode")
        self.assertEqual(second["verdict"], host_config.HOST_CONFIG_READY)
        self.assertFalse(second["restart_required"])
        self.assertEqual(config.read_text(encoding="utf-8"), content)

        binding = host_config.verify_host_process_binding(
            target,
            environ=server["environment"],
            claimed_host="opencode",
        )
        self.assertTrue(binding["ok"], binding)

    def test_host_detection_fails_closed_when_ambiguous(self) -> None:
        target = self.base / "multi-host"
        target.mkdir()
        (target / "opencode.jsonc").write_text("{}\n", encoding="utf-8")
        (target / ".codex").mkdir()
        (target / ".codex" / "config.toml").write_text("model = \"gpt-5\"\n", encoding="utf-8")
        detection = host_config.detect_host(target)
        self.assertFalse(detection["ok"])
        self.assertEqual(detection["verdict"], host_config.HOST_DETECTION_AMBIGUOUS)
        self.assertEqual(set(detection["candidates"]), {"opencode", "codex-cli"})

    def test_claude_project_configuration_preserves_unrelated_servers(self) -> None:
        target = self.base / "claude project"
        _project_markers(target, memory="memory\n")
        _copy_complete_agent(self.source, target)
        config = target / ".mcp.json"
        config.write_text(
            json.dumps({"mcpServers": {"other": {"command": "other-server"}}, "projectSetting": True}),
            encoding="utf-8",
        )

        first = host_config.configure_host(target, explicit="claude-code")
        self.assertTrue(first["ok"], first)
        self.assertTrue(first["restart_required"])
        value = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(value["mcpServers"]["other"]["command"], "other-server")
        self.assertTrue(value["projectSetting"])
        server = value["mcpServers"][host_config.SERVER_NAME]
        self.assertEqual(server["args"], [".agent/mcp/server_entry.py"])
        self.assertTrue(
            host_config.verify_host_process_binding(
                target, environ=server["env"], claimed_host="claude-code"
            )["ok"]
        )
        self.assertEqual(
            host_config.configure_host(target, explicit="claude-code")["verdict"],
            host_config.HOST_CONFIG_READY,
        )

    def test_codex_project_configuration_is_delimited_and_idempotent(self) -> None:
        target = self.base / "codex project"
        _project_markers(target, memory="memory\n")
        _copy_complete_agent(self.source, target)
        config = target / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text('model = "gpt-5"\n', encoding="utf-8")

        first = host_config.configure_host(target, explicit="codex-cli")
        self.assertTrue(first["ok"], first)
        content = config.read_text(encoding="utf-8")
        self.assertIn('model = "gpt-5"', content)
        self.assertEqual(content.count("agent-scribe-graphify:host-config:start"), 1)
        self.assertEqual(content.count("agent-scribe-graphify:host-config:end"), 1)
        binding = json.loads(
            (target / host_config.BINDING_RELATIVE).read_text(encoding="utf-8")
        )
        environment = {
            "AGENT_MCP_HOST": "codex-cli",
            "AGENT_MCP_BINDING_ID": binding["binding_id"],
            "AGENT_SCRIBE_GRAPHIFY_ROOT": ".",
        }
        self.assertTrue(
            host_config.verify_host_process_binding(
                target, environ=environment, claimed_host="codex-cli"
            )["ok"]
        )
        second = host_config.configure_host(target, explicit="codex-cli")
        self.assertEqual(second["verdict"], host_config.HOST_CONFIG_READY)
        self.assertEqual(config.read_text(encoding="utf-8"), content)

    def test_shell_without_binding_never_counts_as_host_visibility(self) -> None:
        target = self.base / "unbound shell"
        _project_markers(target, memory="memory\n")
        _copy_complete_agent(self.source, target)
        report = host_config.verify_host_process_binding(target, environ={}, claimed_host="opencode")
        self.assertFalse(report["ok"])
        self.assertEqual(report["verdict"], host_config.HOST_MCP_UNBOUND)

    def test_six_concurrent_server_proofs_are_not_lost_and_are_single_use(self) -> None:
        target = self.base / "six proof terminals"
        _project_markers(target, memory="memory\n")
        _copy_complete_agent(self.source, target)
        agents = [f"terminal-{index}" for index in range(6)]

        with ThreadPoolExecutor(max_workers=6) as executor:
            tokens = list(executor.map(lambda agent: issue_proof(target, agent), agents))
        self.assertEqual(len(set(tokens)), 6)
        store_path = target / ".agent" / "state" / "outputs" / "scribe-out" / "proof_store.json"
        store = json.loads(store_path.read_text(encoding="utf-8"))
        self.assertEqual({entry["agent_id"] for entry in store.values()}, set(agents))

        with ThreadPoolExecutor(max_workers=6) as executor:
            consumed = list(executor.map(lambda agent: consume_agent_proof(target, agent), agents))
        self.assertTrue(all(result.get("ok") for result in consumed), consumed)
        replay = consume_agent_proof(target, agents[0])
        self.assertFalse(replay["ok"])
        self.assertEqual(replay["verdict"], "PROOF_NOT_AVAILABLE")
        self.assertFalse(store_path.with_name(f".{store_path.name}.lock").exists())

    def test_installation_gate_exposes_current_interpreter_argv(self) -> None:
        target = self.base / "portable action"
        _project_markers(target, memory="memory\n")
        _copy_complete_agent(self.source, target)
        gate = installation_state.inspect_installation_state(target)
        self.assertEqual(gate["next_action_argv"][0], sys.executable)
        self.assertNotEqual(gate["next_action"].split()[0], "python")

    def test_corrupt_proof_store_fails_closed_without_overwrite(self) -> None:
        target = self.base / "corrupt proof store"
        _project_markers(target, memory="memory\n")
        _copy_complete_agent(self.source, target)
        store = target / ".agent" / "state" / "outputs" / "scribe-out" / "proof_store.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        original = b'{"broken":'
        store.write_bytes(original)

        with self.assertRaisesRegex(RuntimeError, "proof_store.json is unreadable"):
            issue_proof(target, "terminal-corrupt")

        self.assertEqual(store.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
