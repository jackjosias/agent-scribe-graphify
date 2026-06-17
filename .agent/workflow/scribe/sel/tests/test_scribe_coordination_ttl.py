from __future__ import annotations

import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from scribe_test_utils import load_script_module


scribe_coordination = load_script_module("scribe_coordination")
active_claims = getattr(scribe_coordination, "active_claims")
cmd_main = getattr(scribe_coordination, "main")
claims_dir = getattr(scribe_coordination, "claims_dir")


class ScribeCoordinationTtlTests(unittest.TestCase):
    @contextlib.contextmanager
    def isolated_coordination_dir(self, root: Path):
        old_dir = os.environ.get("SCRIBE_COORDINATION_DIR")
        os.environ["SCRIBE_COORDINATION_DIR"] = str(root / "coordination")
        try:
            yield root / "coordination"
        finally:
            if old_dir is None:
                os.environ.pop("SCRIBE_COORDINATION_DIR", None)
            else:
                os.environ["SCRIBE_COORDINATION_DIR"] = old_dir

    def run_cli(self, *args: str) -> tuple[int, str]:
        import contextlib as ctx
        import io
        import sys

        stdout = io.StringIO()
        old_argv = sys.argv[:]
        sys.argv = ["scribe coordination", *args]
        try:
            with ctx.redirect_stdout(stdout):
                code = cmd_main()
        finally:
            sys.argv = old_argv
        return code, stdout.getvalue()

    def write_claim(self, coord_root: Path, name: str, payload: dict[str, object]) -> Path:
        path = claims_dir(coord_root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_claim_has_ttl_and_expires_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_coordination_dir(Path(tmp)) as coord_root:
            code, output = self.run_cli("claim", "--agent", "agent-a", "--claim", "test:ttl", "--task", "TTL")
            payloads = [json.loads(path.read_text(encoding="utf-8")) for path in claims_dir(coord_root).glob("*.json")]

        self.assertEqual(code, 0, output)
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["ttl_seconds"], 1800)
        self.assertIn("expires_at", payloads[0])
        self.assertIn("expires_at", output)

    def test_expired_claim_not_in_active_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_coordination_dir(Path(tmp)) as coord_root:
            self.write_claim(coord_root, "expired.json", {
                "agent": "dead-agent",
                "semantic_claim": "test:expired",
                "task": "expired",
                "expected_files": ["test.ts"],
                "status": "working",
                "started_at": "2026-01-01T00:00:00Z",
                "ttl_seconds": 1800,
                "expires_at": "2026-01-01T00:30:00Z",
            })

            claims = active_claims(coord_root)

        self.assertEqual(claims, [])

    def test_stale_claim_cleaned_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_coordination_dir(Path(tmp)) as coord_root:
            path = self.write_claim(coord_root, "expired.json", {
                "agent": "dead-agent",
                "semantic_claim": "test:expired",
                "task": "expired",
                "expected_files": ["test.ts"],
                "status": "working",
                "started_at": "2026-01-01T00:00:00Z",
                "ttl_seconds": 1800,
                "expires_at": "2026-01-01T00:30:00Z",
            })

            active_claims(coord_root)

        self.assertFalse(path.exists())

    def test_claim_without_ttl_treated_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_coordination_dir(Path(tmp)) as coord_root:
            path = self.write_claim(coord_root, "legacy.json", {
                "agent": "legacy-agent",
                "semantic_claim": "test:legacy",
                "task": "legacy",
                "expected_files": ["test.ts"],
                "status": "working",
                "started_at": "2026-01-01T00:00:00Z",
            })

            claims = active_claims(coord_root)

        self.assertEqual(claims, [])
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
