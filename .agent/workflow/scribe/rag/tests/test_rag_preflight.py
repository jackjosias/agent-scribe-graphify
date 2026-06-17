from __future__ import annotations

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import sys
import unittest
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = RAG_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from rag_preflight import preflight
from rag_test_fixtures import build_agent_protocol_test_index


def load_scribe_rag_cli():
    loader = importlib.machinery.SourceFileLoader("_scribe_rag_cli_test", str(RAG_ROOT / "scribe-rag"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise ImportError("Cannot load scribe-rag CLI")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class RagPreflightTests(unittest.TestCase):
    def test_preflight_runs_without_error(self) -> None:
        result = preflight(
            build_agent_protocol_test_index(),
            tier="READ_ONLY",
            plan=None,
            module="bundle",
            limit=5,
            strict=False,
        )
        output = "\n".join(result.lines)

        self.assertEqual(result.code, 0)
        self.assertIn("SCRIBE-RAG PREFLIGHT", output)
        self.assertIn("SCRIBE_RAG_PROOF", output)

    def test_preflight_detects_missing_scribe(self) -> None:
        cli = load_scribe_rag_cli()
        old_current_index = cli.current_index

        def raise_missing_scribe(**_kwargs):
            raise FileNotFoundError("AGENT-MEMOIRE_PROJECT_STATUS.scribe")

        args = argparse.Namespace(force=False, with_embeddings=None, tier="STANDARD", plan=None, module=None, limit=5, strict=True)
        stdout = io.StringIO()
        try:
            cli.current_index = raise_missing_scribe
            with contextlib.redirect_stdout(stdout):
                code = cli.cmd_preflight(args)
        finally:
            cli.current_index = old_current_index

        output = stdout.getvalue()
        self.assertEqual(code, 5)
        self.assertIn("SCRIBE absent", output)
        self.assertIn("scribe bootstrap", output)


if __name__ == "__main__":
    unittest.main()
