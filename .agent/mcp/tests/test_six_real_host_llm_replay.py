from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


replay = load(
    "six_real_host_llm_replay",
    ROOT / ".agent" / "scripts" / "six_real_host_llm_replay.py",
)
hook = load(
    "six_host_pre_tool_hook",
    ROOT / ".agent" / "scripts" / "six_host_pre_tool_hook.py",
)
proxy = load(
    "six_host_mcp_proxy",
    ROOT / ".agent" / "scripts" / "six_host_mcp_proxy.py",
)


class SixRealHostReplayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.environment = {
            "TENOR_REPLAY_AGENT_SESSION_ID": "cli-1",
            "TENOR_REPLAY_HOOK_LOG": str(self.base / "hook.jsonl"),
            "TENOR_REPLAY_MODEL": "gpt-5.6-terra",
            "TENOR_REPLAY_PARTICIPANT_ID": "1",
            "TENOR_REPLAY_RUN_ID": "run-1",
            "TENOR_REPLAY_SERVER_NAME": replay.PROXY_SERVER,
        }
        self.patch = mock.patch.dict(os.environ, self.environment, clear=False)
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.temporary.cleanup()

    def bridge_payload(self) -> dict[str, object]:
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": (
                f"mcp__{replay.PROXY_SERVER}__tenor_init_bridge"
            ),
            "tool_input": {
                "agent_session_id": "cli-1",
                "host_tool": "codex",
                "model_name": "gpt-5.6-terra",
            },
        }

    def activity_payload(self, sequence: int = 1) -> dict[str, object]:
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": f"{replay.PROXY_SERVER}.tenor_activity",
            "tool_input": {
                "run_id": "run-1",
                "participant_id": 1,
                "agent_session_id": "cli-1",
                "phase": "ready" if sequence <= 4 else "observed",
                "sequence": sequence,
            },
        }

    def valid_calls(self) -> list[dict[str, object]]:
        calls: list[dict[str, object]] = [
            {
                "server": replay.PROXY_SERVER,
                "tool": "tenor_init_bridge",
                "arguments": {
                    "agent_session_id": "cli-1",
                    "host_tool": "codex",
                    "model_name": "gpt-5.6-terra",
                },
            }
        ]
        for sequence in range(1, 9):
            calls.append(
                {
                    "server": replay.PROXY_SERVER,
                    "tool": "tenor_activity",
                    "arguments": {
                        "run_id": "run-1",
                        "participant_id": 1,
                        "agent_session_id": "cli-1",
                        "phase": "ready" if sequence <= 4 else "observed",
                        "sequence": sequence,
                    },
                }
            )
        return calls

    def test_hook_allowlist_is_finite_and_proxy_scoped(self) -> None:
        accepted = hook.accepted_tool_names(replay.PROXY_SERVER)
        self.assertEqual(len(accepted), 6)
        self.assertEqual(set(accepted.values()), set(hook.TOOLS))

    def test_hook_allows_exact_bridge(self) -> None:
        self.assertEqual(
            hook.evaluate(self.bridge_payload())[:2],
            (True, "HOOK_ALLOWLIST_OK"),
        )

    def test_hook_allows_exact_activity(self) -> None:
        self.assertEqual(
            hook.evaluate(self.activity_payload(8))[:2],
            (True, "HOOK_ALLOWLIST_OK"),
        )

    def test_hook_rejects_bash_before_effect(self) -> None:
        payload = self.bridge_payload()
        payload["tool_name"] = "Bash"
        allowed, reason, _ = hook.evaluate(payload)
        self.assertFalse(allowed)
        self.assertIn("HOOK_TOOL_NOT_ALLOWED", reason)

    def test_hook_rejects_apply_patch_before_effect(self) -> None:
        payload = self.bridge_payload()
        payload["tool_name"] = "apply_patch"
        self.assertFalse(hook.evaluate(payload)[0])

    def test_hook_rejects_normal_tenor_server(self) -> None:
        payload = self.bridge_payload()
        payload["tool_name"] = (
            "mcp__agent-scribe-graphify__tenor_init_bridge"
        )
        self.assertFalse(hook.evaluate(payload)[0])

    def test_hook_rejects_extra_argument(self) -> None:
        payload = self.bridge_payload()
        payload["tool_input"]["unexpected"] = True  # type: ignore[index]
        self.assertFalse(hook.evaluate(payload)[0])

    def test_hook_rejects_falsified_model(self) -> None:
        payload = self.bridge_payload()
        payload["tool_input"]["model_name"] = "GPT-5"  # type: ignore[index]
        self.assertFalse(hook.evaluate(payload)[0])

    def test_hook_rejects_phase_sequence_mismatch(self) -> None:
        payload = self.activity_payload(5)
        payload["tool_input"]["phase"] = "ready"  # type: ignore[index]
        self.assertFalse(hook.evaluate(payload)[0])

    def test_fresh_init_retry_is_exactly_bounded(self) -> None:
        valid = (
            "HOST_RECONNECT_REQUIRED\n"
            "HOST_CONFIG_UPDATED_RESTART_REQUIRED\n"
        )
        self.assertTrue(replay.should_retry_fresh_init(76, valid))
        self.assertFalse(replay.should_retry_fresh_init(0, valid))
        self.assertFalse(
            replay.should_retry_fresh_init(76, "HOST_RECONNECT_REQUIRED")
        )

    def test_native_elf_header_is_required(self) -> None:
        wrapper = self.base / "codex"
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o700)
        with self.assertRaisesRegex(
            replay.ReplayFailure,
            "NOT_NATIVE_ELF",
        ):
            replay.inspect_elf(wrapper)

    def test_symlink_binary_identity_is_rejected(self) -> None:
        target = self.base / "native"
        target.write_bytes(b"\x7fELF" + b"\x02\x01" + b"\0" * 12 + b"\x3e\0")
        target.chmod(0o700)
        link = self.base / "codex"
        link.symlink_to(target)
        with self.assertRaises(replay.ReplayFailure):
            replay.inspect_elf(link)

    def test_minimal_native_elf_header_is_accepted(self) -> None:
        target = self.base / "native"
        target.write_bytes(b"\x7fELF" + b"\x02\x01" + b"\0" * 12 + b"\x3e\0")
        target.chmod(0o700)
        elf_class, machine = replay.inspect_elf(target)
        self.assertEqual((elf_class, machine), (2, 62))

    def test_exact_nine_call_sequence_passes(self) -> None:
        replay.validate_call_sequence(
            self.valid_calls(),
            run_id="run-1",
            participant_id=1,
            session_id="cli-1",
            model="gpt-5.6-terra",
        )

    def test_missing_activity_call_is_rejected(self) -> None:
        with self.assertRaisesRegex(replay.ReplayFailure, "CALL_COUNT"):
            replay.validate_call_sequence(
                self.valid_calls()[:-1],
                run_id="run-1",
                participant_id=1,
                session_id="cli-1",
                model="gpt-5.6-terra",
            )

    def test_extra_mcp_call_is_rejected(self) -> None:
        calls = self.valid_calls()
        calls.append(
            {
                "server": replay.PROXY_SERVER,
                "tool": "tenor_apply_changeset",
                "arguments": {},
            }
        )
        with self.assertRaises(replay.ReplayFailure):
            replay.validate_call_sequence(
                calls,
                run_id="run-1",
                participant_id=1,
                session_id="cli-1",
                model="gpt-5.6-terra",
            )

    def test_command_execution_event_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            replay.ReplayFailure,
            "ITEM_TYPE_NOT_ALLOWED",
        ):
            replay._summarize_event(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": "touch product.txt",
                    },
                },
                completed_calls=[],
                thread_ids=[],
            )

    def test_exact_hook_trust_notice_is_admitted(self) -> None:
        summary = replay._summarize_event(
            {
                "type": "item.completed",
                "item": {
                    "type": "error",
                    "message": replay.HOOK_TRUST_NOTICE,
                },
            },
            completed_calls=[],
            thread_ids=[],
        )
        self.assertTrue(summary["expected_hook_trust_notice"])

    def test_other_error_item_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            replay.ReplayFailure,
            "UNEXPECTED_ERROR_ITEM",
        ):
            replay._summarize_event(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "error",
                        "message": "MCP startup failed",
                    },
                },
                completed_calls=[],
                thread_ids=[],
            )

    def test_unknown_top_level_event_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            replay.ReplayFailure,
            "EVENT_TYPE_NOT_ALLOWED",
        ):
            replay._summarize_event(
                {"type": "future.event"},
                completed_calls=[],
                thread_ids=[],
            )

    def test_proxy_lists_exactly_two_tools(self) -> None:
        self.assertEqual(
            [item["name"] for item in proxy._tools()],
            ["tenor_init_bridge", "tenor_activity"],
        )

    def test_proxy_silently_ignores_initialized_notification(self) -> None:
        self.assertIsNone(
            proxy.handle(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                }
            )
        )

    def test_proxy_tool_call_accepts_standard_protocol_metadata(self) -> None:
        with mock.patch.object(proxy, "_bridge", return_value={"ok": True}):
            response = proxy.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "tenor_init_bridge",
                        "arguments": {},
                        "_meta": {"progressToken": "opaque"},
                    },
                }
            )
        self.assertIsNotNone(response)

    def test_artifact_redaction_rejects_auth_material(self) -> None:
        with self.assertRaisesRegex(
            replay.ReplayFailure,
            "ARTIFACT_REDACTION_FAILED",
        ):
            replay._artifact_safe(
                {"path": "/tmp/private/auth.json"},
                ["/tmp/private"],
            )

    def test_atomic_manifest_covers_every_json_sidecar(self) -> None:
        replay._atomic_json(self.base / "a.json", {"ok": True})
        replay._atomic_json(self.base / "b.json", {"ok": True})
        manifest = replay._manifest(self.base)
        self.assertEqual(manifest["file_count"], 2)
        self.assertEqual(
            {item["name"] for item in manifest["files"]},
            {"a.json", "b.json"},
        )

    def test_git_identity_rejects_dirty_worktree(self) -> None:
        root = self.base / "repository"
        root.mkdir()
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=root,
            check=True,
        )
        (root / "README.md").write_text("clean\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "fixture"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", "https://example.invalid/repo.git"],
            cwd=root,
            check=True,
        )
        replay.git_identity(root)
        (root / "README.md").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(replay.ReplayFailure, "NOT_CLEAN"):
            replay.git_identity(root)

    def test_prompt_declares_exact_call_cardinality(self) -> None:
        prompt = replay._prompt(
            run_id="run-1",
            participant_id=1,
            session_id="cli-1",
            model="gpt-5.6-terra",
        )
        self.assertEqual(
            prompt.count(
                "mcp__agent_scribe_graphify_replay__tenor_activity("
            ),
            8,
        )
        self.assertEqual(
            prompt.count(
                "mcp__agent_scribe_graphify_replay__tenor_init_bridge("
            ),
            1,
        )
        self.assertIn("Invoke Code Mode exactly once", prompt)
        self.assertIn("any external tool other than", prompt)


if __name__ == "__main__":
    unittest.main()
