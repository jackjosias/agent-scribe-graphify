from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest

from scribe_test_utils import load_script_module


scribe_benchmark = load_script_module("scribe_benchmark")
benchmark_main = getattr(scribe_benchmark, "main")


class ScribeBenchmarkTests(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        old_argv = sys.argv[:]
        sys.argv = ["scribe benchmark", *args]
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = benchmark_main()
        finally:
            sys.argv = old_argv
        return code, stdout.getvalue(), stderr.getvalue()

    def test_benchmark_json_reports_load_and_query_timings(self) -> None:
        code, output, error = self.run_cli("--entities", "128", "--queries", "3", "--json")

        self.assertEqual(code, 0, error)
        payload = json.loads(output)
        result = payload["results"][0]
        self.assertEqual(result["entities"], 128)
        self.assertGreater(result["bytes"], 0)
        self.assertIn("load_ms", result)
        self.assertIn("query_p95_ms", result)


if __name__ == "__main__":
    unittest.main()
