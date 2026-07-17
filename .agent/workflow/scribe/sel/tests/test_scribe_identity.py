from __future__ import annotations

import contextlib
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from scribe_test_utils import load_script_module


scribe_identity = load_script_module("scribe_identity")
Presence = getattr(scribe_identity, "Presence")
active_presences = getattr(scribe_identity, "active_presences")
configured_presence_dir = getattr(scribe_identity, "configured_presence_dir")
generate_agent_id = getattr(scribe_identity, "generate_agent_id")
surface_conflicts = getattr(scribe_identity, "surface_conflicts")
utc_now = getattr(scribe_identity, "utc_now")
write_presence = getattr(scribe_identity, "write_presence")


class ScribeIdentityTests(unittest.TestCase):
    @contextlib.contextmanager
    def isolated_presence_dir(self, root: Path):
        old_presence_dir = os.environ.get("SCRIBE_PRESENCE_DIR")
        os.environ["SCRIBE_PRESENCE_DIR"] = str(root / "presence")
        try:
            yield
        finally:
            if old_presence_dir is None:
                os.environ.pop("SCRIBE_PRESENCE_DIR", None)
            else:
                os.environ["SCRIBE_PRESENCE_DIR"] = old_presence_dir

    def test_no_id_collision(self) -> None:
        ids = {generate_agent_id("codex") for _ in range(100)}

        self.assertEqual(len(ids), 100)

    def test_identity_reuses_canonical_process_probe(self) -> None:
        self.assertEqual(getattr(scribe_identity, "pid_exists").__module__, "scribe_lock")

    def test_write_presence_defaults_to_current_process_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_presence_dir(Path(tmp)):
            presence = write_presence("codex-current", "cli", "idle")

        self.assertEqual(presence.pid, os.getpid())

    def test_presence_cleanup_removes_dead_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_presence_dir(Path(tmp)):
            directory = configured_presence_dir()
            directory.mkdir(parents=True)
            stale = Presence(
                "codex-dead",
                "cli",
                "auth",
                0,
                utc_now() - timedelta(seconds=10),
                utc_now(),
                120,
                "active",
            )
            (directory / "codex-dead.json").write_text(json.dumps(stale.to_payload()), encoding="utf-8")

            active, removed = active_presences()

        self.assertEqual(active, [])
        self.assertEqual(len(removed), 1)
        self.assertIn("pid 0 is not running", removed[0])

    def test_idle_presence_does_not_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_presence_dir(Path(tmp)):
            write_presence("codex-a", "cli", "idle", pid=os.getpid(), status="idle")
            write_presence("codex-b", "cli", "idle", pid=os.getpid(), status="idle")

            conflicts = surface_conflicts("idle", "codex-c")

        self.assertEqual(conflicts, [])

    def test_surface_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.isolated_presence_dir(Path(tmp)):
            write_presence("codex-a", "cli", "auth", pid=os.getpid())
            write_presence("codex-b", "cli", "frontend", pid=os.getpid())

            conflicts = surface_conflicts("auth", "codex-c")

        self.assertEqual([item.agent_id for item in conflicts], ["codex-a"])


if __name__ == "__main__":
    unittest.main()
