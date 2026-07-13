from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import server_ext  # type: ignore


class FirstWriteDiscoveryGateTest(unittest.TestCase):
    def _status(self, **overrides):
        base = {
            "task_id": "task-first-write",
            "agent_id": "agent-first-write",
            "intent": "write",
            "resource": "src/new_feature.py",
            "status": "active",
            "scribe_done": True,
            "scribe_result_count": 0,
            "scribe_record_done": False,
            "scribe_record_path": "",
            "requires_graphify": True,
            "graphify_done": True,
        }
        base.update(overrides)
        return base

    def _write_record(self, root: Path, **overrides) -> str:
        records = root / ".agent" / "state" / "outputs" / "scribe-out" / "records"
        records.mkdir(parents=True)
        payload = {
            "task_id": "task-first-write",
            "agent_id": "agent-first-write",
            "record_type": "task_local_discovery",
            "memory_policy": "local_only",
            "canonical_memory_required": False,
            "verdict": "TASK_LOCAL_DISCOVERY_EVIDENCE",
            "resources": ["src/new_feature.py"],
            "touched_resources": [],
            "summary": "Observed the existing implementation and selected the smallest compatible extension.",
            "evidence": "Graphify identifies the real strategy registry, neighboring modules and affected tests.",
            "root_cause": "No historical SCRIBE entry exists because this exact resource is a first intervention.",
        }
        payload.update(overrides)
        target = records / "discovery.json"
        target.write_text(json.dumps(payload), encoding="utf-8")
        return str(target.relative_to(root))

    def test_relevant_scribe_history_uses_normal_path(self):
        status = self._status(scribe_result_count=2)
        with mock.patch.object(server_ext.task_context, "get_task_context", return_value=status):
            self.assertIsNone(
                server_ext._first_write_discovery_gate(
                    "agent-first-write", "task-first-write", "token", "src/new_feature.py"
                )
            )

    def test_empty_history_requires_local_discovery(self):
        status = self._status()
        with mock.patch.object(server_ext.task_context, "get_task_context", return_value=status):
            blocked = server_ext._first_write_discovery_gate(
                "agent-first-write", "task-first-write", "token", "src/new_feature.py"
            )
        self.assertEqual(blocked["verdict"], "FIRST_WRITE_DISCOVERY_REQUIRED")
        self.assertEqual(blocked["must_call"]["tool"], "scribe_record")
        self.assertEqual(blocked["must_call"]["args"]["memory_policy"], "local_only")

    def test_graphify_is_required_before_discovery_unlock(self):
        status = self._status(graphify_done=False)
        with mock.patch.object(server_ext.task_context, "get_task_context", return_value=status):
            blocked = server_ext._first_write_discovery_gate(
                "agent-first-write", "task-first-write", "token", "src/new_feature.py"
            )
        self.assertEqual(blocked["verdict"], "GRAPHIFY_CONTEXT_REQUIRED")

    def test_valid_task_local_discovery_unlocks_first_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_path = self._write_record(root)
            status = self._status(scribe_record_done=True, scribe_record_path=record_path)
            with (
                mock.patch.object(server_ext.server, "ROOT", root),
                mock.patch.object(server_ext.task_context, "get_task_context", return_value=status),
            ):
                self.assertIsNone(
                    server_ext._first_write_discovery_gate(
                        "agent-first-write", "task-first-write", "token", "src/new_feature.py"
                    )
                )

    def test_canonical_poisoning_cannot_unlock_first_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_path = self._write_record(
                root,
                memory_policy="canonical_required",
                canonical_memory_required=True,
            )
            status = self._status(scribe_record_done=True, scribe_record_path=record_path)
            with (
                mock.patch.object(server_ext.server, "ROOT", root),
                mock.patch.object(server_ext.task_context, "get_task_context", return_value=status),
            ):
                blocked = server_ext._first_write_discovery_gate(
                    "agent-first-write", "task-first-write", "token", "src/new_feature.py"
                )
        self.assertEqual(blocked["verdict"], "FIRST_WRITE_DISCOVERY_REQUIRED")

    def test_wrong_resource_evidence_cannot_unlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_path = self._write_record(root, resources=["src/other.py"])
            status = self._status(scribe_record_done=True, scribe_record_path=record_path)
            with (
                mock.patch.object(server_ext.server, "ROOT", root),
                mock.patch.object(server_ext.task_context, "get_task_context", return_value=status),
            ):
                blocked = server_ext._first_write_discovery_gate(
                    "agent-first-write", "task-first-write", "token", "src/new_feature.py"
                )
        self.assertEqual(blocked["verdict"], "FIRST_WRITE_DISCOVERY_REQUIRED")

    def test_irrelevant_query_becomes_honest_first_write_state(self):
        base_result = server_ext.server.ok({
            "ok": False,
            "verdict": "SCRIBE_CONTEXT_IRRELEVANT_FOR_WRITE",
            "result": {"returncode": 0, "stdout": "unrelated historical result"},
        })
        status = self._status()
        marked = {"memory_hash": "abc", "scribe_result_count": 0}
        with (
            mock.patch.object(server_ext, "_BASE_SCRIBE_QUERY", return_value=base_result),
            mock.patch.object(server_ext.task_context, "get_task_context", return_value=status),
            mock.patch.object(server_ext.task_context, "mark_scribe_done", return_value=marked) as marker,
        ):
            result = server_ext.scribe_query(
                query="src/new_feature.py",
                limit=5,
                agent_id="agent-first-write",
                task_id="task-first-write",
                context_token="token",
            )
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["verdict"], "SCRIBE_HISTORY_ABSENT_FIRST_WRITE_DISCOVERY_REQUIRED")
        self.assertFalse(payload["historical_scribe_context_found"])
        self.assertEqual(payload["task_context"]["scribe_result_count"], 0)
        marker.assert_called_once()


if __name__ == "__main__":
    unittest.main()
