from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from rag_gate import gate
from rag_test_fixtures import build_agent_protocol_test_index, build_auth_test_index


class RagGateTests(unittest.TestCase):
    def test_gate_passes_when_eval_8_8(self) -> None:
        result = gate(build_agent_protocol_test_index())
        output = "\n".join(result.lines)

        self.assertEqual(result.code, 0)
        self.assertIn("SCRIBE-RAG GATE: PASS eval 8/8", output)

    def test_gate_fails_when_eval_below_8(self) -> None:
        result = gate(build_auth_test_index())
        output = "\n".join(result.lines)

        self.assertEqual(result.code, 1)
        self.assertIn("SCRIBE-RAG GATE: FAIL", output)
        self.assertNotIn("PASS eval 8/8", output)

    def test_gate_message_contains_fix_instruction(self) -> None:
        result = gate(build_auth_test_index())
        output = "\n".join(result.lines)

        self.assertIn("scribe-rag preflight", output)


if __name__ == "__main__":
    unittest.main()
