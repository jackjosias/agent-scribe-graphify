#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path
from typing import Any


MCP_DIR = Path(__file__).resolve().parent.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import bounded_process, graphify_build, tenor_changeset, tenor_jobs


def _watchdog_exit() -> None:
    bounded_process.terminate_all_bounded_processes()
    os._exit(124)


class WorkerLeaseHeartbeat:
    def __init__(
        self,
        root: Path,
        worker_fence: tenor_jobs.WorkerFence,
    ) -> None:
        self.root = root
        self.worker_fence = worker_fence
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"tenor-heartbeat-{worker_fence.job_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, tenor_jobs._lease_seconds()))

    def _run(self) -> None:
        interval = max(1.0, tenor_jobs._lease_seconds() / 3)
        while not self._stop.wait(interval):
            result = tenor_jobs.heartbeat_job(
                self.root,
                self.worker_fence.job_id,
                worker_instance_id=self.worker_fence.worker_instance_id,
                fence_token=self.worker_fence.fence_token,
            )
            if not result.get("ok"):
                self._lost.set()
                return
            try:
                tenor_changeset.heartbeat_execution_locks(
                    self.root,
                    tenor_changeset.ExecutionFence(
                        job_id=self.worker_fence.job_id,
                        worker_instance_id=self.worker_fence.worker_instance_id,
                        fence_token=self.worker_fence.fence_token,
                    ),
                )
            except Exception:
                self._lost.set()
                return

    def assert_live(self) -> None:
        if self._lost.is_set():
            raise RuntimeError("TENOR_JOB_FENCE_LOST")
        proof = tenor_jobs.assert_worker_fence(
            self.root,
            job_id=self.worker_fence.job_id,
            worker_instance_id=self.worker_fence.worker_instance_id,
            fence_token=self.worker_fence.fence_token,
            allowed_statuses=frozenset({"running"}),
        )
        if not proof.get("ok"):
            self._lost.set()
            raise RuntimeError("TENOR_JOB_FENCE_LOST")


def _run_changeset(
    root: Path,
    job: dict[str, Any],
    worker_fence: tenor_jobs.WorkerFence,
) -> dict[str, Any]:
    import server_ext as mcp
    from runtime import tenor_public_api

    agent_id = str(job.get("agent_id") or "")
    mcp.server.ROOT = root
    mcp.server.AGENT_DIR = root / ".agent"
    mcp.server._MCP_BOUND_AGENT_ID = agent_id
    payload = dict(job.get("payload") or {})
    return tenor_public_api.execute_changeset_sync(
        agent_id=agent_id,
        execution_fence=tenor_changeset.ExecutionFence(
            job_id=worker_fence.job_id,
            worker_instance_id=worker_fence.worker_instance_id,
            fence_token=worker_fence.fence_token,
        ),
        **payload,
    )


def _run_graphify(
    root: Path,
    job: dict[str, Any],
    heartbeat: WorkerLeaseHeartbeat,
) -> dict[str, Any]:
    payload = dict(job.get("payload") or {})
    return graphify_build.build_project_graph(
        root,
        timeout_seconds=int(payload.get("timeout_seconds") or graphify_build.DEFAULT_TIMEOUT_SECONDS),
        execution_guard=heartbeat.assert_live,
    )


def execute(
    root: Path,
    job_id: str,
    *,
    worker_instance_id: str,
    fence_token: int,
) -> dict[str, Any]:
    claimed = tenor_jobs.claim_job(
        root,
        job_id,
        worker_instance_id=worker_instance_id,
        fence_token=fence_token,
    )
    if not claimed.get("ok"):
        return claimed
    job = dict(claimed["job"])
    fence_payload = dict(claimed["worker_fence"])
    worker_fence = tenor_jobs.WorkerFence(
        job_id=str(fence_payload["job_id"]),
        worker_instance_id=str(fence_payload["worker_instance_id"]),
        fence_token=int(fence_payload["fence_token"]),
        lease_expires_at=int(fence_payload["lease_expires_at"]),
    )
    kind = str(job.get("kind") or "")
    max_runtime = max(1, int(job.get("max_runtime_seconds") or 1))
    watchdog = threading.Timer(max_runtime, _watchdog_exit)
    watchdog.daemon = True
    heartbeat = WorkerLeaseHeartbeat(root, worker_fence)
    heartbeat.start()
    watchdog.start()
    try:
        try:
            if kind == "changeset":
                result = _run_changeset(root, job, worker_fence)
            elif kind == "graphify_build":
                result = _run_graphify(root, job, heartbeat)
            else:
                result = {"ok": False, "verdict": "TENOR_JOB_KIND_INVALID", "kind": kind}
        except BaseException as exc:
            result = {
                "ok": False,
                "verdict": "TENOR_JOB_WORKER_CRASH",
                "reason": f"{type(exc).__name__}: {exc}",
            }
        try:
            heartbeat.assert_live()
        except RuntimeError:
            return {
                "ok": False,
                "verdict": "TENOR_JOB_FENCE_LOST",
                "job_id": job_id,
            }
        if result.get("ok"):
            return tenor_jobs.complete_job(
                root,
                job_id,
                result,
                worker_instance_id=worker_fence.worker_instance_id,
                fence_token=worker_fence.fence_token,
            )
        return tenor_jobs.fail_job(
            root,
            job_id,
            result,
            worker_instance_id=worker_fence.worker_instance_id,
            fence_token=worker_fence.fence_token,
        )
    finally:
        watchdog.cancel()
        heartbeat.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Durable TENOR background job worker")
    parser.add_argument("--root", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--worker-instance-id", required=True)
    parser.add_argument("--fence-token", required=True, type=int)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    expected = os.environ.get("AGENT_SCRIBE_GRAPHIFY_ROOT", "").strip()
    if expected and Path(expected).resolve() != root:
        return 78
    result = execute(
        root,
        args.job_id,
        worker_instance_id=args.worker_instance_id,
        fence_token=args.fence_token,
    )
    try:
        tenor_jobs.recover_and_launch(root)
    except Exception:
        pass
    return 0 if result.get("ok") or result.get("verdict") == "TENOR_JOB_NOT_CLAIMABLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
