from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import six_host_rendezvous as subject


class SixHostRendezvousTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "rendezvous.sqlite"
        subject.initialize(
            self.database,
            run_id="run-1",
            root="/project",
            commit_sha="a" * 40,
            tree_sha="b" * 40,
            model="gpt-5.6-terra",
            cli_version="0.145.0",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def register(self, participant: int) -> None:
        subject.register_bridge(
            self.database,
            run_id="run-1",
            participant_id=participant,
            agent_session_id=f"cli-{participant}",
            mcp_pid=1000 + participant,
            host_pid=2000 + participant,
            model="gpt-5.6-terra",
        )

    def test_initialization_is_idempotent_only_for_identical_metadata(self) -> None:
        subject.initialize(
            self.database,
            run_id="run-1",
            root="/project",
            commit_sha="a" * 40,
            tree_sha="b" * 40,
            model="gpt-5.6-terra",
            cli_version="0.145.0",
        )
        with self.assertRaises(subject.RendezvousError):
            subject.initialize(
                self.database,
                run_id="run-2",
                root="/project",
                commit_sha="a" * 40,
                tree_sha="b" * 40,
                model="gpt-5.6-terra",
                cli_version="0.145.0",
            )

    def test_exactly_six_participants_are_supported(self) -> None:
        for participant in range(1, 7):
            self.register(participant)
        snapshot = subject.snapshot(self.database, run_id="run-1")
        self.assertEqual(snapshot["participant_count"], 6)
        with self.assertRaises(subject.RendezvousError):
            self.register(7)

    def test_bridge_identity_collision_fails_closed(self) -> None:
        self.register(1)
        with self.assertRaises(subject.RendezvousError):
            subject.register_bridge(
                self.database,
                run_id="run-1",
                participant_id=1,
                agent_session_id="replacement",
                mcp_pid=4000,
                host_pid=5000,
                model="gpt-5.6-terra",
            )

    def test_activity_requires_a_registered_bridge(self) -> None:
        with self.assertRaises(subject.RendezvousError):
            subject.record_activity(
                self.database,
                run_id="run-1",
                participant_id=1,
                agent_session_id="cli-1",
                phase="ready",
                sequence=1,
                timeout_seconds=1,
            )

    def test_phase_and_sequence_are_strict(self) -> None:
        self.register(1)
        with self.assertRaises(subject.RendezvousError):
            subject.record_activity(
                self.database,
                run_id="run-1",
                participant_id=1,
                agent_session_id="cli-1",
                phase="observed",
                sequence=1,
                timeout_seconds=1,
            )

    def test_duplicate_or_skipped_sequence_fails_closed(self) -> None:
        for participant in range(1, 7):
            self.register(participant)
        threads = [
            threading.Thread(
                target=subject.record_activity,
                kwargs={
                    "database": self.database,
                    "run_id": "run-1",
                    "participant_id": participant,
                    "agent_session_id": f"cli-{participant}",
                    "phase": "ready",
                    "sequence": 1,
                    "timeout_seconds": 5,
                },
            )
            for participant in range(1, 7)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        with self.assertRaises(subject.RendezvousError):
            subject.record_activity(
                self.database,
                run_id="run-1",
                participant_id=1,
                agent_session_id="cli-1",
                phase="ready",
                sequence=1,
                timeout_seconds=1,
            )
        with self.assertRaises(subject.RendezvousError):
            subject.record_activity(
                self.database,
                run_id="run-1",
                participant_id=1,
                agent_session_id="cli-1",
                phase="ready",
                sequence=3,
                timeout_seconds=1,
            )

    def test_ready_phase_times_out_without_six_hosts(self) -> None:
        self.register(1)
        with self.assertRaisesRegex(subject.RendezvousError, "READY_TIMEOUT"):
            subject.record_activity(
                self.database,
                run_id="run-1",
                participant_id=1,
                agent_session_id="cli-1",
                phase="ready",
                sequence=1,
                timeout_seconds=1,
            )

    def test_two_phase_concurrent_rendezvous_reaches_exact_terminal_state(self) -> None:
        for participant in range(1, 7):
            self.register(participant)
        errors: list[BaseException] = []

        def participant_flow(participant: int) -> None:
            try:
                for sequence in range(1, 9):
                    subject.record_activity(
                        self.database,
                        run_id="run-1",
                        participant_id=participant,
                        agent_session_id=f"cli-{participant}",
                        phase="ready" if sequence <= 4 else "observed",
                        sequence=sequence,
                        timeout_seconds=10,
                    )
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=participant_flow, args=(participant,))
            for participant in range(1, 7)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertFalse(errors, errors)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        snapshot = subject.snapshot(self.database, run_id="run-1")
        self.assertEqual(snapshot["ready_count"], 6)
        self.assertEqual(snapshot["observed_count"], 6)
        self.assertEqual(snapshot["activity_call_count"], 48)

    def test_database_integrity_is_preserved(self) -> None:
        self.assertEqual(
            subject.integrity_check(self.database),
            {"quick_check": "ok", "integrity_check": "ok"},
        )


if __name__ == "__main__":
    unittest.main()
