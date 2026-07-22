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

from runtime import graphify_build, tenor_jobs


def _run_changeset(root: Path, job: dict[str, Any]) -> dict[str, Any]:
    import server_ext as mcp
    from runtime import tenor_public_api

    agent_id = str(job.get("agent_id") or "")
    mcp.server.ROOT = root
    mcp.server.AGENT_DIR = root / ".agent"
    mcp.server._MCP_BOUND_AGENT_ID = agent_id
    payload = dict(job.get("payload") or {})
    return tenor_public_api.execute_changeset_sync(agent_id=agent_id, **payload)


def _run_graphify(root: Path, job: dict[str, Any]) -> dict[str, Any]:
    payload = dict(job.get("payload") or {})
    return graphify_build.build_project_graph(
        root,
        timeout_seconds=int(payload.get("timeout_seconds") or graphify_build.DEFAULT_TIMEOUT_SECONDS),
    )


def execute(root: Path, job_id: str) -> dict[str, Any]:
    claimed = tenor_jobs.claim_job(root, job_id)
    if not claimed.get("ok"):
        return claimed
    job = dict(claimed["job"])
    kind = str(job.get("kind") or "")
    max_runtime = max(1, int(job.get("max_runtime_seconds") or 1))
    watchdog = threading.Timer(max_runtime, lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    try:
        try:
            if kind == "changeset":
                result = _run_changeset(root, job)
            elif kind == "graphify_build":
                result = _run_graphify(root, job)
            else:
                result = {"ok": False, "verdict": "TENOR_JOB_KIND_INVALID", "kind": kind}
        except BaseException as exc:
            result = {
                "ok": False,
                "verdict": "TENOR_JOB_WORKER_CRASH",
                "reason": f"{type(exc).__name__}: {exc}",
            }
        if result.get("ok"):
            return tenor_jobs.complete_job(root, job_id, result)
        return tenor_jobs.fail_job(root, job_id, result)
    finally:
        watchdog.cancel()


def main() -> int:
    parser = argparse.ArgumentParser(description="Durable TENOR background job worker")
    parser.add_argument("--root", required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    expected = os.environ.get("AGENT_SCRIBE_GRAPHIFY_ROOT", "").strip()
    if expected and Path(expected).resolve() != root:
        return 78
    result = execute(root, args.job_id)
    try:
        tenor_jobs.recover_and_launch(root)
    except Exception:
        pass
    return 0 if result.get("ok") or result.get("verdict") == "TENOR_JOB_NOT_CLAIMABLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
