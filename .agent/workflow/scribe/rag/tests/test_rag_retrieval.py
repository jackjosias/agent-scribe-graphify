from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from rag_challenge import challenge
from rag_compact import format_results
from rag_context import context
from rag_eval import run_eval
from rag_gate import gate
from rag_preflight import preflight
from rag_test_fixtures import build_agent_protocol_test_index, build_auth_test_index
from rag_index import is_fresh, requested_index_mode
from rag_scoring import retrieve

class RagRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = build_auth_test_index()

    def ids(self, query: str):
        return [result.entity.get("id") for result in retrieve(query, self.index, top_k=5)]

    def test_semantic_token_client_finds_auth_memory(self) -> None:
        self.assertIn("GHOST-005", self.ids("stocker token côté client"))

    def test_refresh_bug_finds_scar(self) -> None:
        self.assertIn("SCAR-003", self.ids("bug concurrent refresh"))

    def test_localstorage_negative_memory_is_retrieved(self) -> None:
        ids = self.ids("ne jamais utiliser localStorage")
        self.assertTrue("GHOST-005" in ids or "DEBT-003" in ids)

    def test_challenge_blocks_localstorage_jwt(self) -> None:
        verdict, _ = challenge("mettre JWT en localStorage", self.index)
        self.assertEqual(verdict, "STOP")

    def test_challenge_allows_httponly_cookie(self) -> None:
        verdict, _ = challenge("mettre JWT en cookie HttpOnly", self.index)
        self.assertEqual(verdict, "PROCEED")

    def test_challenge_allows_approved_cookie_refresh_rotation(self) -> None:
        verdict, _ = challenge("cookies HttpOnly refresh rotation", self.index)
        self.assertEqual(verdict, "PROCEED")

    def test_negative_memory_does_not_block_generic_empty_project_overlap(self) -> None:
        verdict, _ = challenge("corriger bootstrap projet vide pour BM25 canonique", self.index)
        self.assertNotEqual(verdict, "STOP")

    def test_negative_memory_allows_scriberag_interface_with_sel_engine(self) -> None:
        verdict, _ = challenge("utiliser scribe-rag comme interface agent et garder SEL comme moteur interne", self.index)
        self.assertEqual(verdict, "PROCEED")

    def test_compact_query_line_budget(self) -> None:
        output = format_results("SCRIBE-RAG QUERY: auth jwt", retrieve("auth jwt", self.index, top_k=5))
        self.assertLessEqual(len(output.splitlines()), 15)

    def test_context_line_budget(self) -> None:
        self.assertLessEqual(len(context(self.index).splitlines()), 35)


class RagIndexModeTests(unittest.TestCase):
    def test_default_mode_requests_bm25(self) -> None:
        self.assertEqual(requested_index_mode(None), "bm25")
        self.assertEqual(requested_index_mode(False), "bm25")
        self.assertEqual(requested_index_mode(True), "hybrid")

    def test_hybrid_index_is_not_fresh_for_default_bm25_request(self) -> None:
        snapshot = {"sha256": "sha256:test"}
        index = {"version": 2, "source_sha256": "sha256:test", "mode": "hybrid"}
        self.assertFalse(is_fresh(index, snapshot, requested_index_mode(None)))
        self.assertTrue(is_fresh(index, snapshot, requested_index_mode(True)))


class RagProtocolPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = build_agent_protocol_test_index()

    def test_eval_scores_scriberag_protocol_memory(self) -> None:
        checks = run_eval(self.index)
        self.assertTrue(all(check.passed for check in checks), [check for check in checks if not check.passed])

    def test_gate_passes_only_on_full_protocol_eval(self) -> None:
        result = gate(self.index)
        output = "\n".join(result.lines)
        self.assertEqual(result.code, 0)
        self.assertIn("SCRIBE-RAG GATE: PASS eval 8/8", output)

    def test_gate_fails_when_protocol_eval_is_incomplete(self) -> None:
        result = gate(build_auth_test_index())
        output = "\n".join(result.lines)
        self.assertEqual(result.code, 1)
        self.assertIn("SCRIBE-RAG GATE: FAIL", output)

    def test_challenge_blocks_sel_direct_retrieval(self) -> None:
        verdict, _ = challenge("appeler SEL direct context query pour retrieval agent", self.index)
        self.assertEqual(verdict, "STOP")

    def test_challenge_allows_preventing_sel_direct_retrieval(self) -> None:
        verdict, _ = challenge("interdire SEL direct context query pour retrieval agent", self.index)
        self.assertEqual(verdict, "PROCEED")

    def test_preflight_outputs_proof_surface(self) -> None:
        result = preflight(
            self.index,
            tier="STANDARD",
            plan="utiliser scribe-rag comme interface agent et garder SEL comme moteur interne",
            module="bundle",
            limit=5,
            strict=True,
        )
        output = "\n".join(result.lines)
        self.assertEqual(result.code, 0)
        self.assertIn("SCRIBE-RAG PREFLIGHT", output)
        self.assertIn("SCRIBE_RAG_PROOF: preflight STANDARD | eval 8/8 PASS | challenge PROCEED", output)
        self.assertIn("eval 8/8 PASS", output)
        self.assertIn("challenge PROCEED", output)

if __name__ == "__main__":
    unittest.main()
