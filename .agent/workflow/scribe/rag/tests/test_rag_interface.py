from __future__ import annotations

import json
import shutil
import tempfile
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from rag_interface import SEL_CLI, export_scribe, export_scribe_cli, export_scribe_index, export_scribe_native
from rag_whoami import format_whoami

class RagInterfaceTests(unittest.TestCase):
    def test_sel_cli_resolves_to_canonical_path(self) -> None:
        self.assertTrue(SEL_CLI.exists())
        self.assertEqual(SEL_CLI.name, "scribe")
        self.assertIn(".agent/workflow/scribe/scribe", str(SEL_CLI))
        self.assertNotIn("scribe-engineering-local-causal-retrieval", str(SEL_CLI))

    def test_sel_cli_not_from_path(self) -> None:
        path_scribe = shutil.which("scribe")
        if path_scribe is not None:
            self.assertNotEqual(str(SEL_CLI), path_scribe)

    def test_export_json_contract(self) -> None:
        payload = export_scribe(include_values=True)
        self.assertIn("entities", payload)
        self.assertIsInstance(payload["entities"], list)
        self.assertTrue(all(isinstance(item, dict) and item.get("id") for item in payload["entities"]))

    def test_native_export_matches_cli_contract(self) -> None:
        native = export_scribe_native(include_values=True)
        cli = export_scribe_cli(include_values=True)
        self.assertEqual(native["summary"], cli["summary"])
        self.assertEqual(native["tiers"], cli["tiers"])
        self.assertEqual(
            [(item.get("id"), item.get("collection"), item.get("tier"), item.get("status")) for item in native["entities"]],
            [(item.get("id"), item.get("collection"), item.get("tier"), item.get("status")) for item in cli["entities"]],
        )

    def test_index_export_matches_cli_entity_contract(self) -> None:
        index_export = export_scribe_index(include_values=True)
        cli = export_scribe_cli(include_values=True)
        self.assertEqual(index_export["tiers"], cli["tiers"])
        self.assertEqual(index_export["summary"]["entities"], cli["summary"]["entities"])
        self.assertEqual(index_export["summary"]["ids"], cli["summary"]["ids"])
        self.assertEqual(
            [(item.get("id"), item.get("collection"), item.get("tier"), item.get("status")) for item in index_export["entities"]],
            [(item.get("id"), item.get("collection"), item.get("tier"), item.get("status")) for item in cli["entities"]],
        )

    def test_whoami_reports_state_lock_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "state.json"
            lock_path = root / "locks" / "scribe.lock"
            index_path = root / "rag-index.json"
            lock_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps({"writer": {"agent": "agent-a"}, "last_session": "JOURNAL-123"}),
                encoding="utf-8",
            )
            lock_path.write_text(
                json.dumps({"agent": "agent-b", "surface": "auth", "acquired_at": "2026-05-26T10:00:00Z"}),
                encoding="utf-8",
            )
            index_path.write_text(json.dumps({"mode": "bm25"}), encoding="utf-8")

            output = format_whoami(state_path=state_path, lock_path=lock_path, index_path=index_path)

            self.assertIn("Last writer : agent-a", output)
            self.assertIn("Last session: JOURNAL-123", output)
            self.assertIn("Lock status : locked", output)
            self.assertIn("Lock owner  : agent-b", output)
            self.assertIn("Lock surface: auth", output)
            self.assertIn("Index mode  : bm25", output)


if __name__ == "__main__":
    unittest.main()
