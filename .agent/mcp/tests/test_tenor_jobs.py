#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
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

    def tearDown(self) -> None:
        remove_tree_strict(self.tmp.name)
        self.tmp.cleanup()

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
        completed = tenor_jobs.complete_job(
            self.root,
            job_id,
            {"ok": True, "verdict": "PROBE_COMPLETE", "secret_free": True},
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

    def test_dead_running_worker_is_requeued(self) -> None:
        submitted = self.submit()
        job_id = str(submitted["job_id"])
        claimed = tenor_jobs.claim_job(self.root, job_id)
        self.assertTrue(claimed["ok"], claimed)
        with db.connect(self.root) as con:
            con.execute(
                f"UPDATE {tenor_jobs.JOB_TABLE} SET owner_pid=?,updated_at=? WHERE job_id=?",
                (999_999_999, int(time.time()) - 60, job_id),
            )

        recovered = tenor_jobs.recover_stale_jobs(self.root)
        snapshot = tenor_jobs.job_snapshot(self.root, job_id=job_id)

        self.assertIn(job_id, recovered["requeued"])
        self.assertEqual(snapshot["jobs"][0]["status"], "queued")
        self.assertGreaterEqual(snapshot["jobs"][0]["attempt_count"], 1)

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
        script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
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
        deadline = time.monotonic() + 10
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
        self.assertEqual(current.get("attempt_count"), tenor_jobs.MAX_JOB_ATTEMPTS, current)
        error = current.get("error") or {}
        self.assertEqual(error.get("verdict"), "TENOR_JOB_RETRY_EXHAUSTED", current)


if __name__ == "__main__":
    unittest.main(verbosity=2)
