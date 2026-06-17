from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scribe_test_utils import load_script_module, write_fixture


scribe_store = load_script_module("scribe_store")
load_scribe = getattr(scribe_store, "load_scribe")
edge_type_counts = getattr(scribe_store, "edge_type_counts")
tokenize = getattr(scribe_store, "tokenize")

scribe_bundle_graph = load_script_module("scribe_bundle_graph")
should_skip_bundle_graph_path = getattr(scribe_bundle_graph, "should_skip_bundle_graph_path")

scribe_install = load_script_module("scribe_install")
replace_managed_block = getattr(scribe_install, "replace_managed_block")
scribe_install_templates = load_script_module("scribe_install_templates")


class ScribeStoreTests(unittest.TestCase):
    def test_load_scribe_builds_indexes_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = load_scribe(write_fixture(Path(tmp)))

        self.assertEqual(store.findings, [])
        entity = store.by_id("VAC-100")
        if entity is None:
            self.fail("VAC-100 was not indexed")
        self.assertEqual(entity.collection, "vaccins")
        self.assertEqual([entity.id for entity in store.hot_entities()], ["VAC-100", "PAT-100"])
        self.assertEqual(len(store.index.text_index), 4)
        self.assertEqual(sum(len(targets) for targets in store.index.causal_edges.values()), 4)
        self.assertEqual(edge_type_counts(store.index.edge_types), {"causal": 1, "evidence": 0, "consultation": 2, "journal": 1})

    def test_related_returns_forward_and_reverse_causal_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = load_scribe(write_fixture(Path(tmp)))

        outgoing, incoming = store.related("VAC-100")

        self.assertEqual([entity.id for entity in outgoing], ["PAT-100"])
        self.assertEqual([entity.id for entity in incoming], ["JOURNAL-100"])

    def test_search_scores_hot_matching_memory_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = load_scribe(write_fixture(Path(tmp)))

        results = store.search("portable bundle", limit=2)

        self.assertGreaterEqual(results[0][0], results[-1][0])
        self.assertEqual(results[0][1].entity.id, "VAC-100")


    def test_search_expands_french_synonyms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = load_scribe(write_fixture(Path(tmp)))

        results = store.search("recherche memoire", limit=2)

        self.assertTrue(results)
        self.assertEqual(results[0][1].entity.id, "PAT-100")

    def test_search_tolerates_french_typos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = load_scribe(write_fixture(Path(tmp)))

        results = store.search("rechreche causla", limit=2)

        self.assertTrue(results)
        self.assertEqual(results[0][1].entity.id, "PAT-100")

    def test_tokenize_keeps_ids_and_long_terms(self) -> None:
        self.assertEqual(tokenize("VAC-100 Pyrefly IO"), {"vac-100", "pyrefly"})

    def test_bundle_graph_skips_vendor_and_minified_assets(self) -> None:
        self.assertTrue(should_skip_bundle_graph_path(Path("adapters/root/scribe")))
        self.assertTrue(should_skip_bundle_graph_path(Path("vendor/echarts/echarts.min.js")))
        self.assertTrue(should_skip_bundle_graph_path(Path("dashboard.min.js")))
        self.assertFalse(should_skip_bundle_graph_path(Path("scripts/scribe_search.py")))

    def test_managed_block_update_is_idempotent_with_suffix_newline(self) -> None:
        existing = "<start>\nold\n<end>\n\nsuffix\n"
        block = "<start>\nnew\n<end>\n"

        updated = replace_managed_block(existing, "<start>", "<end>", block)

        self.assertEqual(updated, replace_managed_block(updated, "<start>", "<end>", block))

    def test_managed_block_replaces_legacy_bundle_markers(self) -> None:
        start = getattr(scribe_install_templates, "AGENTS_START")
        end = getattr(scribe_install_templates, "AGENTS_END")
        legacy_start = getattr(scribe_install_templates, "LEGACY_AGENTS_START")
        legacy_end = getattr(scribe_install_templates, "LEGACY_AGENTS_END")
        existing = f"{legacy_start}\nold\n{legacy_end}\n"
        block = f"{start}\nnew\n{end}\n"

        updated = replace_managed_block(existing, start, end, block, ((legacy_start, legacy_end),))

        self.assertIn(start, updated)
        self.assertNotIn(legacy_start, updated)


if __name__ == "__main__":
    unittest.main()
