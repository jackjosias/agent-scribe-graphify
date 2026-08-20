#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = ROOT / ".agent" / "mcp"
if str(MCP_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(MCP_DIR))

from runtime import db, graphify_readiness, tenor_jobs
from _strict_cleanup import remove_tree_strict


class TenorJobsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        (self.root / ".agent" / "state" / "runtime").mkdir(parents=True)
        db.init_db(self.root)
        self.previous_lease = os.environ.get("AGENT_TENOR_JOB_LEASE_SECONDS")
        os.environ["AGENT_TENOR_JOB_LEASE_SECONDS"] = "3"
        self.previous_debounce = os.environ.get(
            "AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS"
        )
        os.environ["AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS"] = "0"

    def tearDown(self) -> None:
        if self.previous_lease is None:
            os.environ.pop("AGENT_TENOR_JOB_LEASE_SECONDS", None)
        else:
            os.environ["AGENT_TENOR_JOB_LEASE_SECONDS"] = self.previous_lease
        if self.previous_debounce is None:
            os.environ.pop("AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS", None)
        else:
            os.environ["AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS"] = self.previous_debounce
        remove_tree_strict(self.tmp.name)
        self.tmp.cleanup()

    @staticmethod
    def fence(claimed: dict[str, object]) -> tuple[str, int]:
        payload = claimed["worker_fence"]
        assert isinstance(payload, dict)
        return str(payload["worker_instance_id"]), int(payload["fence_token"])

    def submit(
        self,
        *,
        request_id: str = "request-1",
        payload: dict[str, object] | None = None,
        auto_launch: bool = False,
    ) -> dict[str, object]:
        return tenor_jobs.submit_job(
            self.root,
            kind="changeset",
            agent_id="agent-a",
            task_id="task-a",
            request_id=request_id,
            payload=payload or {"task_id": "task-a", "changes": [], "validators": []},
            max_runtime_seconds=30,
            auto_launch=auto_launch,
        )

    def test_submit_is_idempotent_and_payload_bound(self) -> None:
        first = self.submit()
        repeated = self.submit()
        conflict = self.submit(payload={"task_id": "task-a", "changes": [{"different": True}], "validators": []})

        self.assertTrue(first["ok"], first)
        self.assertEqual(first["verdict"], "TENOR_JOB_ACCEPTED")
        self.assertEqual(repeated["verdict"], "TENOR_JOB_ALREADY_ACCEPTED")
        self.assertEqual(repeated["job_id"], first["job_id"])
        self.assertFalse(conflict["ok"], conflict)
        self.assertEqual(conflict["verdict"], "TENOR_JOB_IDEMPOTENCY_CONFLICT")

    def test_only_one_worker_can_claim_a_job(self) -> None:
        submitted = self.submit()
        first = tenor_jobs.claim_job(self.root, str(submitted["job_id"]))
        second = tenor_jobs.claim_job(self.root, str(submitted["job_id"]))

        self.assertTrue(first["ok"], first)
        self.assertEqual(first["verdict"], "TENOR_JOB_CLAIMED")
        self.assertFalse(second["ok"], second)
        self.assertEqual(second["verdict"], "TENOR_JOB_NOT_CLAIMABLE")

    def test_terminal_result_is_visible_and_payload_is_redacted(self) -> None:
        submitted = self.submit()
        job_id = str(submitted["job_id"])
        claimed = tenor_jobs.claim_job(self.root, job_id)
        self.assertTrue(claimed["ok"], claimed)
        worker_instance_id, fence_token = self.fence(claimed)
        completed = tenor_jobs.complete_job(
            self.root,
            job_id,
            {"ok": True, "verdict": "PROBE_COMPLETE", "secret_free": True},
            worker_instance_id=worker_instance_id,
            fence_token=fence_token,
        )
        snapshot = tenor_jobs.job_snapshot(self.root, job_id=job_id)

        self.assertEqual(completed["verdict"], "TENOR_JOB_SUCCEEDED")
        self.assertEqual(snapshot["jobs"][0]["status"], "succeeded")
        self.assertEqual(snapshot["jobs"][0]["result"]["verdict"], "PROBE_COMPLETE")
        with db.connect(self.root) as con:
            row = con.execute(
                f"SELECT payload_json FROM {tenor_jobs.JOB_TABLE} WHERE job_id=?",
                (job_id,),
            ).fetchone()
        self.assertEqual(json.loads(row["payload_json"]), {})

    def test_expired_worker_lease_is_requeued_without_pid_authority(self) -> None:
        submitted = self.submit()
        job_id = str(submitted["job_id"])
        claimed = tenor_jobs.claim_job(self.root, job_id)
        self.assertTrue(claimed["ok"], claimed)
        with db.connect(self.root) as con:
            con.execute(
                f"""
                UPDATE {tenor_jobs.JOB_TABLE}
                SET owner_pid=?,lease_expires_at=?,updated_at=?
                WHERE job_id=?
                """,
                (999_999_999, int(time.time()) - 1, int(time.time()) - 60, job_id),
            )

        recovered = tenor_jobs.recover_stale_jobs(self.root)
        snapshot = tenor_jobs.job_snapshot(self.root, job_id=job_id)

        self.assertIn(job_id, recovered["requeued"])
        self.assertEqual(snapshot["jobs"][0]["status"], "queued")
        self.assertEqual(snapshot["jobs"][0]["attempt_count"], 1)
        self.assertEqual(snapshot["jobs"][0]["fence_token"], 2)

    def test_live_lease_remains_authoritative_when_pid_looks_dead(self) -> None:
        submitted = self.submit()
        job_id = str(submitted["job_id"])
        claimed = tenor_jobs.claim_job(self.root, job_id)
        self.assertTrue(claimed["ok"], claimed)
        with db.connect(self.root) as con:
            con.execute(
                f"""
                UPDATE {tenor_jobs.JOB_TABLE}
                SET owner_pid=?,updated_at=?
                WHERE job_id=?
                """,
                (999_999_999, int(time.time()) - 600, job_id),
            )
        recovered = tenor_jobs.recover_stale_jobs(self.root)
        snapshot = tenor_jobs.job_snapshot(self.root, job_id=job_id)["jobs"][0]
        self.assertEqual(recovered["requeued"], [])
        self.assertEqual(snapshot["status"], "running")
        self.assertEqual(snapshot["fence_token"], 1)

    def test_heartbeat_and_terminal_publish_require_current_live_fence(self) -> None:
        submitted = self.submit()
        job_id = str(submitted["job_id"])
        claimed = tenor_jobs.claim_job(self.root, job_id)
        old_instance, old_token = self.fence(claimed)
        heartbeat = tenor_jobs.heartbeat_job(
            self.root,
            job_id,
            worker_instance_id=old_instance,
            fence_token=old_token,
        )
        self.assertTrue(heartbeat["ok"], heartbeat)
        with db.connect(self.root) as con:
            con.execute(
                f"UPDATE {tenor_jobs.JOB_TABLE} SET lease_expires_at=? WHERE job_id=?",
                (int(time.time()) - 1, job_id),
            )
        expired_heartbeat = tenor_jobs.heartbeat_job(
            self.root,
            job_id,
            worker_instance_id=old_instance,
            fence_token=old_token,
        )
        expired_finish = tenor_jobs.complete_job(
            self.root,
            job_id,
            {"ok": True, "verdict": "STALE_WORKER"},
            worker_instance_id=old_instance,
            fence_token=old_token,
        )
        self.assertEqual(expired_heartbeat["verdict"], "TENOR_JOB_FENCE_LOST")
        self.assertEqual(expired_finish["verdict"], "TENOR_JOB_FENCE_LOST")

        recovered = tenor_jobs.recover_stale_jobs(self.root)
        self.assertEqual(recovered["requeued"], [job_id])
        replacement = tenor_jobs.claim_job(self.root, job_id)
        new_instance, new_token = self.fence(replacement)
        self.assertNotEqual(new_instance, old_instance)
        self.assertEqual((old_token, new_token), (1, 2))
        rejected_old = tenor_jobs.complete_job(
            self.root,
            job_id,
            {"ok": True, "verdict": "STALE_WORKER"},
            worker_instance_id=old_instance,
            fence_token=old_token,
        )
        accepted_new = tenor_jobs.complete_job(
            self.root,
            job_id,
            {"ok": True, "verdict": "CURRENT_WORKER"},
            worker_instance_id=new_instance,
            fence_token=new_token,
        )
        self.assertEqual(rejected_old["verdict"], "TENOR_JOB_FENCE_LOST")
        self.assertEqual(accepted_new["verdict"], "TENOR_JOB_SUCCEEDED")
        snapshot = tenor_jobs.job_snapshot(self.root, job_id=job_id)["jobs"][0]
        self.assertEqual(snapshot["attempt_count"], 1)
        self.assertEqual(snapshot["result"]["verdict"], "CURRENT_WORKER")

    def test_concurrent_recovery_transfers_fence_once(self) -> None:
        submitted = self.submit()
        job_id = str(submitted["job_id"])
        claimed = tenor_jobs.claim_job(self.root, job_id)
        self.assertTrue(claimed["ok"], claimed)
        with db.connect(self.root) as con:
            con.execute(
                f"UPDATE {tenor_jobs.JOB_TABLE} SET lease_expires_at=? WHERE job_id=?",
                (int(time.time()) - 1, job_id),
            )
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(
                executor.map(
                    lambda _index: tenor_jobs.recover_stale_jobs(self.root),
                    range(16),
                )
            )
        self.assertEqual(
            sum(job_id in result["requeued"] for result in results),
            1,
            results,
        )
        snapshot = tenor_jobs.job_snapshot(self.root, job_id=job_id)["jobs"][0]
        self.assertEqual(snapshot["status"], "queued")
        self.assertEqual(snapshot["fence_token"], 2)
        self.assertEqual(snapshot["attempt_count"], 1)

    def test_concurrent_graphify_requests_converge_to_one_job(self) -> None:
        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(
                executor.map(
                    lambda _index: tenor_jobs.submit_graphify_rebuild(
                        self.root,
                        auto_launch=False,
                    ),
                    range(24),
                )
            )
        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertEqual(len({result["job_id"] for result in results}), 1, results)
        snapshot = tenor_jobs.job_snapshot(
            self.root,
            kind="graphify_build",
        )
        self.assertEqual(snapshot["count"], 1, snapshot)
        self.assertEqual(snapshot["jobs"][0]["status"], "queued")

    def test_running_graphify_rebuild_fences_new_changeset_launch(self) -> None:
        changeset = self.submit(request_id="changeset-before-running-rebuild")
        graphify = tenor_jobs.submit_graphify_rebuild(
            self.root,
            auto_launch=False,
        )
        claimed_graphify = tenor_jobs.claim_job(
            self.root,
            str(graphify["job_id"]),
        )
        self.assertTrue(claimed_graphify["ok"], claimed_graphify)
        blocked = tenor_jobs.claim_job(self.root, str(changeset["job_id"]))
        self.assertEqual(blocked["verdict"], "TENOR_JOB_NOT_CLAIMABLE", blocked)

    def test_queued_graphify_rebuild_does_not_fence_changeset_launch(self) -> None:
        changeset = self.submit(request_id="changeset-during-debounce")
        graphify = tenor_jobs.submit_graphify_rebuild(
            self.root,
            auto_launch=False,
        )
        self.assertEqual(graphify["status"], "queued", graphify)
        claimed = tenor_jobs.claim_job(self.root, str(changeset["job_id"]))
        self.assertTrue(claimed["ok"], claimed)

    def test_rebuild_coalescing_extends_debounce_window(self) -> None:
        previous = os.environ.get("AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS")
        os.environ["AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS"] = "5"
        try:
            first = tenor_jobs.submit_job(
                self.root,
                kind="graphify_build",
                agent_id="",
                task_id="",
                request_id="graphify-debounce-window",
                payload={},
                max_runtime_seconds=30,
                auto_launch=False,
            )
            with db.connect(self.root) as con:
                first_created = con.execute(
                    f"SELECT created_at FROM {tenor_jobs.JOB_TABLE} WHERE job_id=?",
                    (str(first["job_id"]),),
                ).fetchone()["created_at"]
            time.sleep(1.1)
            second = tenor_jobs.submit_job(
                self.root,
                kind="graphify_build",
                agent_id="",
                task_id="",
                request_id="graphify-debounce-window-bis",
                payload={},
                max_runtime_seconds=30,
                auto_launch=False,
            )
            self.assertEqual(
                second["verdict"],
                "TENOR_GRAPHIFY_REBUILD_ALREADY_PENDING",
                second,
            )
            self.assertEqual(second["job_id"], first["job_id"])
            with db.connect(self.root) as con:
                bumped = con.execute(
                    f"SELECT created_at FROM {tenor_jobs.JOB_TABLE} WHERE job_id=?",
                    (str(first["job_id"]),),
                ).fetchone()["created_at"]
            self.assertGreaterEqual(
                int(bumped),
                int(first_created) + 1,
                "coalescing must extend the debounce window",
            )
        finally:
            if previous is None:
                os.environ.pop("AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS", None)
            else:
                os.environ["AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS"] = previous

    def test_graphify_launch_gated_by_debounce_window(self) -> None:
        previous = os.environ.get("AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS")
        os.environ["AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS"] = "3"
        try:
            submitted = tenor_jobs.submit_graphify_rebuild(
                self.root,
                auto_launch=True,
            )
            snapshot = tenor_jobs.job_snapshot(
                self.root,
                job_id=str(submitted["job_id"]),
                limit=1,
            )
            self.assertEqual(snapshot["jobs"][0]["status"], "queued", snapshot)
        finally:
            if previous is None:
                os.environ.pop("AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS", None)
            else:
                os.environ["AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS"] = previous

    def test_explicit_graphify_build_ignores_debounce_gate(self) -> None:
        previous = os.environ.get("AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS")
        os.environ["AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS"] = "3"
        try:
            explicit = tenor_jobs.submit_job(
                self.root,
                kind="graphify_build",
                agent_id="",
                task_id="",
                request_id="graphify-explicit-request",
                payload={"timeout_seconds": 30},
                max_runtime_seconds=30,
                auto_launch=True,
            )
            self.assertEqual(explicit["verdict"], "TENOR_JOB_ACCEPTED", explicit)
            snapshot = tenor_jobs.job_snapshot(
                self.root,
                job_id=str(explicit["job_id"]),
                limit=1,
            )
            self.assertNotEqual(
                snapshot["jobs"][0]["status"],
                "queued",
                "an explicit graphify build must not be debounced",
            )
        finally:
            if previous is None:
                os.environ.pop("AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS", None)
            else:
                os.environ["AGENT_TENOR_GRAPHIFY_REBUILD_DEBOUNCE_SECONDS"] = previous

    def test_snapshot_reports_blocked_by_and_queue_position(self) -> None:
        graphify = tenor_jobs.submit_job(
            self.root,
            kind="graphify_build",
            agent_id="",
            task_id="",
            request_id="graphify-snapshot-observability",
            payload={},
            max_runtime_seconds=30,
            auto_launch=False,
        )
        first = self.submit(request_id="changeset-snap-first")
        second = tenor_jobs.submit_job(
            self.root,
            kind="changeset",
            agent_id="agent-a",
            task_id="task-b",
            request_id="changeset-snap-second",
            payload={"task_id": "task-b", "changes": [], "validators": []},
            max_runtime_seconds=30,
            auto_launch=False,
        )
        claimed = tenor_jobs.claim_job(self.root, str(graphify["job_id"]))
        self.assertTrue(claimed["ok"], claimed)
        snapshot = tenor_jobs.job_snapshot(self.root, limit=10)
        by_id = {str(job["job_id"]): job for job in snapshot["jobs"]}
        first_row = by_id[str(first["job_id"])]
        second_row = by_id[str(second["job_id"])]
        self.assertEqual(first_row["status"], "queued")
        self.assertTrue(
            str(first_row.get("blocked_by") or "").startswith("graphify_build:"),
            first_row,
        )
        self.assertEqual(first_row["queue_position"], 1, first_row)
        self.assertEqual(second_row["queue_position"], 2, second_row)

    def test_committed_changeset_schedules_one_graphify_rebuild(self) -> None:
        submitted = self.submit(request_id="committed-with-rebuild")
        claimed = tenor_jobs.claim_job(self.root, str(submitted["job_id"]))
        worker_instance_id, fence_token = self.fence(claimed)
        completed = tenor_jobs.complete_job(
            self.root,
            str(submitted["job_id"]),
            {
                "ok": True,
                "verdict": "TENOR_CHANGESET_COMMITTED_TASK_FINISHED",
            },
            worker_instance_id=worker_instance_id,
            fence_token=fence_token,
        )
        self.assertEqual(completed["verdict"], "TENOR_JOB_SUCCEEDED", completed)
        rebuild = completed["graphify_rebuild"]
        self.assertTrue(rebuild["ok"], rebuild)
        snapshot = tenor_jobs.job_snapshot(
            self.root,
            kind="graphify_build",
        )
        self.assertEqual(snapshot["count"], 1, snapshot)
        self.assertEqual(snapshot["jobs"][0]["status"], "queued")

    def test_graphify_worker_runs_outside_request_and_publishes_result(self) -> None:
        previous_fixture = os.environ.get(graphify_readiness.FIXTURE_ENV)
        os.environ[graphify_readiness.FIXTURE_ENV] = "1"
        try:
            graphify_readiness.write_smoke_fixture(self.root)
            submitted = tenor_jobs.submit_job(
                self.root,
                kind="graphify_build",
                agent_id="",
                task_id="",
                request_id="graph-ready",
                payload={"timeout_seconds": 30},
                max_runtime_seconds=60,
                auto_launch=True,
            )
            self.assertIn(
                submitted["verdict"],
                {"TENOR_JOB_ACCEPTED", "TENOR_JOB_ALREADY_ACCEPTED"},
            )
            deadline = time.monotonic() + 15
            latest: dict[str, object] = {}
            while time.monotonic() < deadline:
                latest = tenor_jobs.job_snapshot(
                    self.root,
                    job_id=str(submitted["job_id"]),
                )["jobs"][0]
                if latest["status"] in tenor_jobs.TERMINAL_STATUSES:
                    break
                time.sleep(0.05)
            self.assertEqual(latest.get("status"), "succeeded", latest)
            result = latest.get("result") or {}
            self.assertEqual(result.get("verdict"), "GRAPHIFY_ALREADY_READY")
        finally:
            if previous_fixture is None:
                os.environ.pop(graphify_readiness.FIXTURE_ENV, None)
            else:
                os.environ[graphify_readiness.FIXTURE_ENV] = previous_fixture

    def test_worker_watchdog_bounds_hung_job_and_exhausts_retries(self) -> None:
        script = self.root / ".agent" / "workflow" / "scribe" / "scribe"
        script.parent.mkdir(parents=True)
        script.write_text(
            "import os,time\n"
            "with open('graphify-worker-pids.txt','a',encoding='utf-8') as handle:\n"
            "    handle.write(str(os.getpid()) + '\\n')\n"
            "    handle.flush()\n"
            "    os.fsync(handle.fileno())\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        submitted = tenor_jobs.submit_job(
            self.root,
            kind="graphify_build",
            agent_id="",
            task_id="",
            request_id="hung-graphify",
            payload={"timeout_seconds": 30},
            max_runtime_seconds=1,
            auto_launch=True,
        )
        deadline = time.monotonic() + 30
        current: dict[str, object] = {}
        while time.monotonic() < deadline:
            current = tenor_jobs.job_snapshot(
                self.root,
                job_id=str(submitted["job_id"]),
                limit=1,
            )["jobs"][0]
            if current["status"] in tenor_jobs.TERMINAL_STATUSES:
                break
            tenor_jobs.recover_and_launch(self.root)
            time.sleep(0.1)
        self.assertEqual(current.get("status"), "failed", current)
        self.assertEqual(current.get("attempt_count"), 1, current)
        self.assertEqual(
            current.get("fence_token"),
            tenor_jobs.MAX_JOB_ATTEMPTS + 1,
            current,
        )
        error = current.get("error") or {}
        self.assertEqual(error.get("verdict"), "TENOR_JOB_RETRY_EXHAUSTED", current)
        child_pid_sequence = [
            int(value)
            for value in (self.root / "graphify-worker-pids.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if value.strip()
        ]
        self.assertGreaterEqual(len(child_pid_sequence), 1, child_pid_sequence)
        child_pids = set(child_pid_sequence)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(
            db.process_is_alive(pid) for pid in child_pids
        ):
            time.sleep(0.05)
        self.assertFalse(
            any(db.process_is_alive(pid) for pid in child_pids),
            child_pids,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
