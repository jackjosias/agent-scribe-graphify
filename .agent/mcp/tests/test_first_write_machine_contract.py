from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / ".agent" / "rules" / "first-write-discovery-v1.json"


class FirstWriteMachineContractTest(unittest.TestCase):
    def test_contract_is_machine_readable_and_fail_closed(self) -> None:
        data = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "FIRST_WRITE_DISCOVERY_V1")
        self.assertEqual(data["parent_contract"], ".agent/rules/tenor-init-v2.json")
        self.assertTrue(data["activation"]["scribe_unavailable_is_not_a_miss"])
        self.assertTrue(data["activation"]["query_text_is_not_relevance_evidence"])

        # This remains the fail-closed internal protocol. The public host API
        # executes it server-side instead of exposing the choreography.
        required = set(data["required_tools"])
        for tool in (
            "scope_task_resource",
            "scribe_query",
            "graphify_query",
            "file_hash",
            "record_task_discovery",
            "pre_action_guard",
            "resource_lock_claim",
            "claim_resource",
            "propose_patch",
            "apply_patch",
            "workspace_audit",
            "finish_task",
        ):
            self.assertIn(tool, required)

        sequence = data["sequence"]
        self.assertLess(sequence.index("obtain_fresh_file_hash"), sequence.index("record_task_local_discovery"))
        self.assertLess(sequence.index("record_task_local_discovery"), sequence.index("continue_normal_guard_lock_claim_patch_audit_finish"))

        binding = data["discovery_binding"]
        self.assertTrue(binding["task_id"])
        self.assertTrue(binding["agent_id"])
        self.assertTrue(binding["exact_project_relative_resource"])
        self.assertTrue(binding["fresh_base_hash"])
        self.assertEqual(binding["invalidate_on_hash_change"], "TASK_DISCOVERY_BASE_STALE")

        memory = data["canonical_memory"]
        self.assertFalse(memory["automatic_seeding_for_gate"])
        self.assertFalse(memory["task_local_discovery_is_canonical_memory"])
        self.assertFalse(memory["promotion_before_validated_result"])
        self.assertTrue(memory["finish_memory_decision_remains_required"])

        fail_closed = set(data["fail_closed"])
        self.assertIn("SCRIBE_UNAVAILABLE", fail_closed)
        self.assertIn("TASK_DISCOVERY_BASE_STALE", fail_closed)
        self.assertIn("TASK_DISCOVERY_RESOURCE_EVIDENCE_REQUIRED", fail_closed)

        forbidden = set(data["forbidden_bypasses"])
        self.assertIn("direct_host_edit", forbidden)
        self.assertIn("manual_patch_application", forbidden)
        self.assertIn("canonical_scribe_poisoning", forbidden)


if __name__ == "__main__":
    unittest.main()
