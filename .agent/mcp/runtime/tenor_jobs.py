from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import db


JOB_TABLE = "tenor_runtime_jobs_v1"
JOB_KINDS = frozenset({"changeset", "graphify_build"})
ACTIVE_STATUSES = frozenset({"queued", "recovering", "launching", "running"})
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
MAX_JOB_ATTEMPTS = 3
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_RESULT_BYTES = 512 * 1024
DEFAULT_MAX_WORKERS = 4
LAUNCH_STALE_SECONDS = 30
DEFAULT_LEASE_SECONDS = 15
MIN_LEASE_SECONDS = 3
MAX_LEASE_SECONDS = 300

_LAUNCH_LOCK = threading.RLock()


@dataclass(frozen=True)
class WorkerFence:
    job_id: str
    worker_instance_id: str
    fence_token: int
    lease_expires_at: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> int:
    return int(time.time())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "verdict": "TENOR_JOB_CORRUPT_JSON"}
    return value if isinstance(value, dict) else {"ok": False, "verdict": "TENOR_JOB_CORRUPT_JSON"}


def _fingerprint(kind: str, agent_id: str, task_id: str, payload: dict[str, Any]) -> str:
    material = {
        "kind": kind,
        "agent_id": agent_id,
        "task_id": task_id,
        "payload": payload,
    }
    return hashlib.sha256(_json(material).encode("utf-8")).hexdigest()


def _automatic_request_id(kind: str, task_id: str, fingerprint: str) -> str:
    prefix = task_id.strip() or kind
    return f"auto-{prefix[:48]}-{fingerprint[:32]}"


def _max_workers() -> int:
    raw = os.environ.get("AGENT_TENOR_MAX_JOB_WORKERS", "").strip()
    try:
        requested = int(raw) if raw else DEFAULT_MAX_WORKERS
    except ValueError:
        requested = DEFAULT_MAX_WORKERS
    return max(1, min(requested, 16))


def _lease_seconds() -> int:
    raw = os.environ.get("AGENT_TENOR_JOB_LEASE_SECONDS", "").strip()
    try:
        requested = int(raw) if raw else DEFAULT_LEASE_SECONDS
    except ValueError:
        requested = DEFAULT_LEASE_SECONDS
    return max(MIN_LEASE_SECONDS, min(requested, MAX_LEASE_SECONDS))


def ensure_schema(project_root: Path | str) -> None:
    root = Path(project_root).resolve()
    db.init_db(root)
    with db.connect(root) as con:
        con.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {JOB_TABLE}(
              job_id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              agent_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              request_id TEXT NOT NULL,
              request_fingerprint TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status TEXT NOT NULL,
              owner_pid INTEGER NOT NULL DEFAULT 0,
              worker_instance_id TEXT NOT NULL DEFAULT '',
              fence_token INTEGER NOT NULL DEFAULT 0,
              lease_expires_at INTEGER NOT NULL DEFAULT 0,
              heartbeat_at INTEGER NOT NULL DEFAULT 0,
              recovery_prepared INTEGER NOT NULL DEFAULT 0,
              attempt_count INTEGER NOT NULL DEFAULT 0,
              max_runtime_seconds INTEGER NOT NULL,
              result_json TEXT NOT NULL DEFAULT '{{}}',
              error_json TEXT NOT NULL DEFAULT '{{}}',
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              started_at INTEGER,
              finished_at INTEGER,
              UNIQUE(kind,agent_id,request_id)
            );
            CREATE INDEX IF NOT EXISTS idx_{JOB_TABLE}_status
              ON {JOB_TABLE}(status,created_at);
            CREATE INDEX IF NOT EXISTS idx_{JOB_TABLE}_task
              ON {JOB_TABLE}(task_id,status,updated_at);
            """
        )
        columns = {
            str(row["name"])
            for row in con.execute(f"PRAGMA table_info({JOB_TABLE})").fetchall()
        }
        migrations = {
            "worker_instance_id": "TEXT NOT NULL DEFAULT ''",
            "fence_token": "INTEGER NOT NULL DEFAULT 0",
            "lease_expires_at": "INTEGER NOT NULL DEFAULT 0",
            "heartbeat_at": "INTEGER NOT NULL DEFAULT 0",
            "recovery_prepared": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, declaration in migrations.items():
            if name not in columns:
                con.execute(
                    f"ALTER TABLE {JOB_TABLE} ADD COLUMN {name} {declaration}"
                )


def _public_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["result"] = _load_json(str(data.pop("result_json", "{}")))
    data["error"] = _load_json(str(data.pop("error_json", "{}")))
    data.pop("payload_json", None)
    data["overdue"] = bool(
        data.get("status") in ACTIVE_STATUSES
        and (
            (
                int(data.get("lease_expires_at") or 0) > 0
                and _now() >= int(data["lease_expires_at"])
            )
            or (
                data.get("started_at")
                and _now()
                > int(data["started_at"]) + int(data.get("max_runtime_seconds") or 0)
            )
        )
    )
    return data


def _fence_from_row(row: Any) -> WorkerFence:
    return WorkerFence(
        job_id=str(row["job_id"]),
        worker_instance_id=str(row["worker_instance_id"] or ""),
        fence_token=int(row["fence_token"] or 0),
        lease_expires_at=int(row["lease_expires_at"] or 0),
    )


def _fence_payload(row: Any) -> dict[str, Any]:
    return _fence_from_row(row).to_dict()


def _fence_matches(
    row: Any,
    worker_instance_id: str,
    fence_token: int,
    *,
    require_live_lease: bool = True,
) -> bool:
    if (
        str(row["worker_instance_id"] or "") != str(worker_instance_id or "")
        or int(row["fence_token"] or 0) != int(fence_token or 0)
    ):
        return False
    if require_live_lease and int(row["lease_expires_at"] or 0) <= _now():
        return False
    return True


def assert_worker_fence(
    project_root: Path | str,
    *,
    job_id: str,
    worker_instance_id: str,
    fence_token: int,
    allowed_statuses: frozenset[str] = frozenset({"launching", "running", "recovering"}),
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    ensure_schema(root)
    with db.connect(root) as con:
        row = con.execute(
            f"SELECT * FROM {JOB_TABLE} WHERE job_id=?",
            (job_id,),
        ).fetchone()
    if (
        not row
        or str(row["status"]) not in allowed_statuses
        or not _fence_matches(row, worker_instance_id, fence_token)
    ):
        return {
            "ok": False,
            "verdict": "TENOR_JOB_FENCE_LOST",
            "job_id": job_id,
        }
    return {
        "ok": True,
        "verdict": "TENOR_JOB_FENCE_VALID",
        "job_id": job_id,
        "worker_fence": _fence_payload(row),
    }


def submit_job(
    project_root: Path | str,
    *,
    kind: str,
    agent_id: str,
    task_id: str,
    request_id: str,
    payload: dict[str, Any],
    max_runtime_seconds: int,
    auto_launch: bool = True,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    normalized_kind = str(kind or "").strip()
    if normalized_kind not in JOB_KINDS:
        return {"ok": False, "verdict": "TENOR_JOB_KIND_INVALID", "kind": normalized_kind}
    if not isinstance(payload, dict):
        return {"ok": False, "verdict": "TENOR_JOB_PAYLOAD_INVALID"}
    payload_json = _json(payload)
    if len(payload_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        return {"ok": False, "verdict": "TENOR_JOB_PAYLOAD_TOO_LARGE", "maximum": MAX_PAYLOAD_BYTES}
    try:
        runtime_bound = int(max_runtime_seconds)
    except (TypeError, ValueError):
        runtime_bound = 0
    if runtime_bound < 1 or runtime_bound > 24 * 60 * 60:
        return {"ok": False, "verdict": "TENOR_JOB_RUNTIME_BOUND_INVALID"}

    fingerprint = _fingerprint(normalized_kind, agent_id, task_id, payload)
    stable_request_id = str(request_id or "").strip() or _automatic_request_id(
        normalized_kind,
        task_id,
        fingerprint,
    )
    if len(stable_request_id) > 200:
        return {"ok": False, "verdict": "TENOR_JOB_REQUEST_ID_INVALID"}

    ensure_schema(root)
    now = _now()
    job_id = f"job-{uuid.uuid4().hex[:24]}"
    with db.connect(root) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            existing = con.execute(
                f"SELECT * FROM {JOB_TABLE} WHERE kind=? AND agent_id=? AND request_id=?",
                (normalized_kind, agent_id, stable_request_id),
            ).fetchone()
            if existing:
                con.execute("COMMIT")
                current = _public_row(existing)
                if str(existing["request_fingerprint"]) != fingerprint:
                    return {
                        "ok": False,
                        "verdict": "TENOR_JOB_IDEMPOTENCY_CONFLICT",
                        "job_id": current["job_id"],
                        "request_id": stable_request_id,
                        "status": current["status"],
                    }
                result = {
                    "ok": True,
                    "verdict": "TENOR_JOB_ALREADY_ACCEPTED",
                    **current,
                }
                if auto_launch and current["status"] == "queued":
                    launch_queued_jobs(root)
                return result
            if normalized_kind == "graphify_build":
                active_graphify = con.execute(
                    f"""
                    SELECT * FROM {JOB_TABLE}
                    WHERE kind='graphify_build'
                      AND status IN ('queued','recovering','launching','running')
                    ORDER BY created_at,job_id
                    LIMIT 1
                    """
                ).fetchone()
                if active_graphify:
                    con.execute("COMMIT")
                    current = _public_row(active_graphify)
                    if auto_launch and current["status"] == "queued":
                        launch_queued_jobs(root)
                    return {
                        "ok": True,
                        "verdict": "TENOR_GRAPHIFY_REBUILD_ALREADY_PENDING",
                        **current,
                    }
            if task_id:
                active = con.execute(
                    f"""
                    SELECT * FROM {JOB_TABLE}
                    WHERE task_id=?
                      AND status IN ('queued','recovering','launching','running')
                    ORDER BY created_at
                    LIMIT 1
                    """,
                    (task_id,),
                ).fetchone()
                if active:
                    con.execute("COMMIT")
                    return {
                        "ok": False,
                        "verdict": "TENOR_TASK_JOB_ALREADY_ACTIVE",
                        "job": _public_row(active),
                    }
            con.execute(
                f"""
                INSERT INTO {JOB_TABLE}(
                  job_id,kind,agent_id,task_id,request_id,request_fingerprint,
                  payload_json,status,max_runtime_seconds,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,'queued',?,?,?)
                """,
                (
                    job_id,
                    normalized_kind,
                    agent_id,
                    task_id,
                    stable_request_id,
                    fingerprint,
                    payload_json,
                    runtime_bound,
                    now,
                    now,
                ),
            )
            db.add_event(
                con,
                "tenor.job_queued",
                {"job_id": job_id, "kind": normalized_kind, "task_id": task_id},
                agent_id or None,
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    if auto_launch:
        launch_queued_jobs(root)
    return {
        "ok": True,
        "verdict": "TENOR_JOB_ACCEPTED",
        "job_id": job_id,
        "kind": normalized_kind,
        "task_id": task_id,
        "request_id": stable_request_id,
        "status": "queued",
        "poll_after_ms": 250,
    }


def submit_graphify_rebuild(
    project_root: Path | str,
    *,
    timeout_seconds: int = 180,
    auto_launch: bool = True,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    try:
        from . import graphify_readiness

        workspace = graphify_readiness.workspace_fingerprint(root)
        if workspace["truncated"]:
            return {
                "ok": False,
                "verdict": "TENOR_GRAPHIFY_REBUILD_FINGERPRINT_TOO_LARGE",
            }
        fingerprint = str(workspace["fingerprint"])
    except Exception as exc:
        return {
            "ok": False,
            "verdict": "TENOR_GRAPHIFY_REBUILD_FINGERPRINT_FAILED",
            "reason": f"{type(exc).__name__}: {exc}",
        }
    return submit_job(
        root,
        kind="graphify_build",
        agent_id="",
        task_id="",
        request_id=f"graphify-rebuild-{fingerprint}",
        payload={"timeout_seconds": int(timeout_seconds)},
        max_runtime_seconds=max(60, int(timeout_seconds) + 60),
        auto_launch=auto_launch,
    )


def claim_job(
    project_root: Path | str,
    job_id: str,
    *,
    worker_instance_id: str = "",
    fence_token: int = 0,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    ensure_schema(root)
    if not worker_instance_id or int(fence_token or 0) <= 0:
        reserved = _reserve_launch(root, job_id)
        if reserved is None:
            snapshot = job_snapshot(root, job_id=job_id, limit=1)
            current = snapshot["jobs"][0] if snapshot["jobs"] else {}
            return {
                "ok": False,
                "verdict": "TENOR_JOB_NOT_CLAIMABLE",
                "job_id": job_id,
                "status": str(current.get("status") or "missing"),
            }
        worker_instance_id = reserved.worker_instance_id
        fence_token = reserved.fence_token
    now = _now()
    lease_expires_at = now + _lease_seconds()
    with db.connect(root) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute(f"SELECT * FROM {JOB_TABLE} WHERE job_id=?", (job_id,)).fetchone()
            if (
                not row
                or row["status"] != "launching"
                or not _fence_matches(row, worker_instance_id, fence_token)
            ):
                con.execute("COMMIT")
                return {
                    "ok": False,
                    "verdict": "TENOR_JOB_NOT_CLAIMABLE",
                    "job_id": job_id,
                    "status": str(row["status"]) if row else "missing",
                }
            updated = con.execute(
                f"""
                UPDATE {JOB_TABLE}
                SET status='running',owner_pid=?,started_at=COALESCE(started_at,?),
                    heartbeat_at=?,lease_expires_at=?,updated_at=?
                WHERE job_id=? AND status='launching'
                  AND worker_instance_id=? AND fence_token=?
                  AND lease_expires_at>?
                """,
                (
                    os.getpid(),
                    now,
                    now,
                    lease_expires_at,
                    now,
                    job_id,
                    worker_instance_id,
                    int(fence_token),
                    now,
                ),
            ).rowcount
            if not updated:
                con.execute("COMMIT")
                return {
                    "ok": False,
                    "verdict": "TENOR_JOB_FENCE_LOST",
                    "job_id": job_id,
                }
            db.add_event(
                con,
                "tenor.job_running",
                {
                    "job_id": job_id,
                    "kind": row["kind"],
                    "task_id": row["task_id"],
                    "pid": os.getpid(),
                    "worker_instance_id": worker_instance_id,
                    "fence_token": int(fence_token),
                },
                str(row["agent_id"] or "") or None,
            )
            claimed = con.execute(f"SELECT * FROM {JOB_TABLE} WHERE job_id=?", (job_id,)).fetchone()
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return {
        "ok": True,
        "verdict": "TENOR_JOB_CLAIMED",
        "worker_fence": _fence_payload(claimed),
        "job": {
            **_public_row(claimed),
            "payload": _load_json(str(claimed["payload_json"] or "{}")),
            "worker_fence": _fence_payload(claimed),
        },
    }


def heartbeat_job(
    project_root: Path | str,
    job_id: str,
    *,
    worker_instance_id: str,
    fence_token: int,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    ensure_schema(root)
    now = _now()
    lease_expires_at = now + _lease_seconds()
    with db.connect(root) as con:
        updated = con.execute(
            f"""
            UPDATE {JOB_TABLE}
            SET heartbeat_at=?,lease_expires_at=?,updated_at=?
            WHERE job_id=? AND status IN ('launching','running','recovering')
              AND worker_instance_id=? AND fence_token=?
              AND lease_expires_at>?
            """,
            (
                now,
                lease_expires_at,
                now,
                job_id,
                worker_instance_id,
                int(fence_token),
                now,
            ),
        ).rowcount
        row = con.execute(
            f"SELECT * FROM {JOB_TABLE} WHERE job_id=?",
            (job_id,),
        ).fetchone()
    if not updated or not row:
        return {"ok": False, "verdict": "TENOR_JOB_FENCE_LOST", "job_id": job_id}
    return {
        "ok": True,
        "verdict": "TENOR_JOB_HEARTBEAT",
        "job_id": job_id,
        "worker_fence": _fence_payload(row),
    }


def _finish_job(
    project_root: Path | str,
    job_id: str,
    *,
    worker_instance_id: str,
    fence_token: int,
    status: str,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in TERMINAL_STATUSES:
        raise ValueError("invalid terminal job status")
    root = Path(project_root).resolve()
    ensure_schema(root)
    result_json = _json(result or {})
    error_json = _json(error or {})
    if len(result_json.encode("utf-8")) > MAX_RESULT_BYTES:
        result_json = _json({"ok": False, "verdict": "TENOR_JOB_RESULT_TOO_LARGE"})
        status = "failed"
    if len(error_json.encode("utf-8")) > MAX_RESULT_BYTES:
        error_json = _json({"verdict": "TENOR_JOB_ERROR_TOO_LARGE"})
    now = _now()
    with db.connect(root) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute(f"SELECT * FROM {JOB_TABLE} WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                con.execute("COMMIT")
                return {"ok": False, "verdict": "TENOR_JOB_UNKNOWN", "job_id": job_id}
            updated = con.execute(
                f"""
                UPDATE {JOB_TABLE}
                SET status=?,owner_pid=0,worker_instance_id='',lease_expires_at=0,
                    heartbeat_at=0,recovery_prepared=0,payload_json='{{}}',
                    result_json=?,error_json=?,updated_at=?,finished_at=?
                WHERE job_id=? AND status IN ('launching','running','recovering')
                  AND worker_instance_id=? AND fence_token=?
                  AND lease_expires_at>?
                """,
                (
                    status,
                    result_json,
                    error_json,
                    now,
                    now,
                    job_id,
                    worker_instance_id,
                    int(fence_token),
                    now,
                ),
            ).rowcount
            if not updated:
                con.execute("COMMIT")
                return {
                    "ok": False,
                    "verdict": "TENOR_JOB_FENCE_LOST",
                    "job_id": job_id,
                }
            db.add_event(
                con,
                f"tenor.job_{status}",
                {"job_id": job_id, "kind": row["kind"], "task_id": row["task_id"]},
                str(row["agent_id"] or "") or None,
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    verdict = {
        "succeeded": "TENOR_JOB_SUCCEEDED",
        "failed": "TENOR_JOB_FAILED",
        "cancelled": "TENOR_JOB_CANCELLED",
    }[status]
    return {"ok": status == "succeeded", "verdict": verdict, "job_id": job_id, "status": status}


def complete_job(
    project_root: Path | str,
    job_id: str,
    result: dict[str, Any],
    *,
    worker_instance_id: str,
    fence_token: int,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    ensure_schema(root)
    with db.connect(root) as con:
        row = con.execute(
            f"SELECT kind FROM {JOB_TABLE} WHERE job_id=?",
            (job_id,),
        ).fetchone()
    finished = _finish_job(
        project_root,
        job_id,
        worker_instance_id=worker_instance_id,
        fence_token=fence_token,
        status="succeeded",
        result=result,
    )
    if (
        finished.get("ok")
        and row
        and row["kind"] == "changeset"
        and str(result.get("verdict") or "")
        in {
            "TENOR_CHANGESET_COMMITTED",
            "TENOR_CHANGESET_COMMITTED_MEMORY_DECISION_REQUIRED",
            "TENOR_CHANGESET_COMMITTED_TASK_FINISHED",
            "TENOR_CHANGESET_RECOVERED_TERMINAL_STATE",
        }
    ):
        finished["graphify_rebuild"] = submit_graphify_rebuild(
            root,
            auto_launch=False,
        )
    return finished


def fail_job(
    project_root: Path | str,
    job_id: str,
    error: dict[str, Any],
    *,
    worker_instance_id: str,
    fence_token: int,
) -> dict[str, Any]:
    return _finish_job(
        project_root,
        job_id,
        worker_instance_id=worker_instance_id,
        fence_token=fence_token,
        status="failed",
        error=error,
        result=error,
    )


def job_snapshot(
    project_root: Path | str,
    *,
    job_id: str = "",
    task_id: str = "",
    kind: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    ensure_schema(root)
    bound = max(1, min(int(limit or 100), 500))
    clauses: list[str] = []
    params: list[Any] = []
    if job_id:
        clauses.append("job_id=?")
        params.append(job_id)
    if task_id:
        clauses.append("task_id=?")
        params.append(task_id)
    if kind:
        clauses.append("kind=?")
        params.append(kind)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db.connect(root) as con:
        rows = con.execute(
            f"SELECT * FROM {JOB_TABLE} {where} ORDER BY created_at DESC,job_id DESC LIMIT ?",
            (*params, bound),
        ).fetchall()
    jobs = [_public_row(row) for row in rows]
    return {
        "ok": True,
        "verdict": "TENOR_JOB_SNAPSHOT",
        "jobs": jobs,
        "count": len(jobs),
        "active": sum(1 for job in jobs if job["status"] in ACTIVE_STATUSES),
    }


def active_job_for_task(project_root: Path | str, task_id: str) -> dict[str, Any] | None:
    snapshot = job_snapshot(project_root, task_id=task_id, limit=20)
    return next((job for job in snapshot["jobs"] if job["status"] in ACTIVE_STATUSES), None)


def _reserve_launch(project_root: Path, job_id: str) -> WorkerFence | None:
    now = _now()
    lease_expires_at = now + _lease_seconds()
    with db.connect(project_root) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute(
                f"SELECT * FROM {JOB_TABLE} WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if not row or row["status"] != "queued":
                con.execute("COMMIT")
                return None
            active = int(
                con.execute(
                    f"SELECT COUNT(*) AS count FROM {JOB_TABLE} WHERE status IN ('recovering','launching','running')",
                ).fetchone()["count"]
            )
            if active >= _max_workers():
                con.execute("COMMIT")
                return None
            if row["kind"] == "changeset":
                graphify_pending = con.execute(
                    f"""
                    SELECT 1 FROM {JOB_TABLE}
                    WHERE kind='graphify_build'
                      AND status IN ('queued','recovering','launching','running')
                    LIMIT 1
                    """
                ).fetchone()
                if graphify_pending:
                    con.execute("COMMIT")
                    return None
            if row["kind"] == "graphify_build":
                try:
                    transaction_active = con.execute(
                        """
                        SELECT 1 FROM tenor_changesets_v1
                        WHERE status IN ('staging','applying','validating','guarding','rollback_required')
                        LIMIT 1
                        """
                    ).fetchone()
                except Exception as exc:
                    if "no such table" in str(exc).lower():
                        transaction_active = None
                    else:
                        raise
                if transaction_active:
                    con.execute("COMMIT")
                    return None

            prepared = bool(
                int(row["recovery_prepared"] or 0)
                and str(row["worker_instance_id"] or "")
                and int(row["fence_token"] or 0) > 0
                and int(row["lease_expires_at"] or 0) > now
            )
            worker_instance_id = (
                str(row["worker_instance_id"])
                if prepared
                else f"worker-{uuid.uuid4().hex}"
            )
            fence_token = (
                int(row["fence_token"])
                if prepared
                else int(row["fence_token"] or 0) + 1
            )
            if fence_token > MAX_JOB_ATTEMPTS:
                con.execute("COMMIT")
                return None
            updated = con.execute(
                f"""
                UPDATE {JOB_TABLE}
                SET status='launching',owner_pid=?,worker_instance_id=?,
                    fence_token=?,lease_expires_at=?,heartbeat_at=?,
                    recovery_prepared=0,
                    attempt_count=CASE WHEN attempt_count=0 THEN 1 ELSE attempt_count END,
                    updated_at=?
                WHERE job_id=? AND status='queued' AND fence_token<=?
                """,
                (
                    os.getpid(),
                    worker_instance_id,
                    fence_token,
                    lease_expires_at,
                    now,
                    now,
                    job_id,
                    MAX_JOB_ATTEMPTS,
                ),
            ).rowcount
            reserved = con.execute(
                f"SELECT * FROM {JOB_TABLE} WHERE job_id=?",
                (job_id,),
            ).fetchone()
            con.execute("COMMIT")
            if not updated or not reserved:
                return None
            return _fence_from_row(reserved)
        except Exception:
            con.execute("ROLLBACK")
            raise


def _worker_command(
    project_root: Path,
    job_id: str,
    worker_fence: WorkerFence,
) -> list[str]:
    worker = Path(__file__).resolve().with_name("tenor_job_worker.py")
    return [
        sys.executable,
        str(worker),
        "--root",
        str(project_root),
        "--job-id",
        job_id,
        "--worker-instance-id",
        worker_fence.worker_instance_id,
        "--fence-token",
        str(worker_fence.fence_token),
    ]


def _secure_log(project_root: Path, job_id: str) -> tuple[int, Path]:
    directory = project_root / ".agent" / "state" / "runtime" / "tenor-jobs"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    path = directory / f"{job_id}.log"
    descriptor = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    return descriptor, path


def _reap_process(process: subprocess.Popen[bytes], project_root: Path) -> None:
    process.wait()
    try:
        recover_stale_jobs(project_root)
        launch_queued_jobs(project_root)
    except Exception:
        return


def _spawn_worker(
    project_root: Path,
    job_id: str,
    worker_fence: WorkerFence,
) -> dict[str, Any]:
    descriptor, log_path = _secure_log(project_root, job_id)
    env = os.environ.copy()
    env["AGENT_SCRIBE_GRAPHIFY_ROOT"] = str(project_root)
    env["AGENT_TENOR_JOB_WORKER"] = "1"
    kwargs: dict[str, Any] = {
        "cwd": str(project_root),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": descriptor,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
        kwargs["close_fds"] = False
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(
            _worker_command(project_root, job_id, worker_fence),
            **kwargs,
        )
    finally:
        os.close(descriptor)
    with db.connect(project_root) as con:
        now = _now()
        updated = con.execute(
            f"""
            UPDATE {JOB_TABLE} SET owner_pid=?,updated_at=?
            WHERE job_id=? AND status='launching'
              AND worker_instance_id=? AND fence_token=? AND lease_expires_at>?
            """,
            (
                process.pid,
                now,
                job_id,
                worker_fence.worker_instance_id,
                worker_fence.fence_token,
                now,
            ),
        ).rowcount
    if not updated:
        process.terminate()
        process.wait(timeout=5)
        raise RuntimeError("TENOR_JOB_FENCE_LOST_BEFORE_SPAWN_PUBLICATION")
    threading.Thread(
        target=_reap_process,
        args=(process, project_root),
        name=f"tenor-job-reaper-{job_id}",
        daemon=True,
    ).start()
    return {
        "job_id": job_id,
        "pid": process.pid,
        "log_path": str(log_path),
        "worker_instance_id": worker_fence.worker_instance_id,
        "fence_token": worker_fence.fence_token,
    }


def launch_queued_jobs(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    ensure_schema(root)
    launched: list[dict[str, Any]] = []
    with _LAUNCH_LOCK:
        with db.connect(root) as con:
            active = int(
                con.execute(
                    f"SELECT COUNT(*) AS count FROM {JOB_TABLE} WHERE status IN ('launching','running')",
                ).fetchone()["count"]
            )
            capacity = max(0, _max_workers() - active)
            rows = con.execute(
                f"""
                SELECT job_id FROM {JOB_TABLE}
                WHERE status='queued' AND fence_token<=?
                ORDER BY CASE WHEN kind='graphify_build' THEN 0 ELSE 1 END,
                         created_at,job_id
                LIMIT ?
                """,
                (MAX_JOB_ATTEMPTS, capacity),
            ).fetchall()
        for row in rows:
            job_id = str(row["job_id"])
            worker_fence = _reserve_launch(root, job_id)
            if worker_fence is None:
                continue
            try:
                launched.append(_spawn_worker(root, job_id, worker_fence))
            except Exception as exc:
                fail_job(
                    root,
                    job_id,
                    {"ok": False, "verdict": "TENOR_JOB_LAUNCH_FAILED", "reason": f"{type(exc).__name__}: {exc}"},
                    worker_instance_id=worker_fence.worker_instance_id,
                    fence_token=worker_fence.fence_token,
                )
    return {
        "ok": True,
        "verdict": "TENOR_JOBS_LAUNCHED",
        "launched": launched,
        "count": len(launched),
    }


def _task_terminal_result(project_root: Path, row: Any) -> dict[str, Any] | None:
    if not row["task_id"]:
        return None
    try:
        with db.connect(project_root) as con:
            activity = con.execute(
                "SELECT * FROM tenor_task_activity_v1 WHERE task_id=?",
                (row["task_id"],),
            ).fetchone()
    except Exception:
        return None
    if not activity or activity["status"] not in {"finished", "awaiting_memory"}:
        return None
    awaiting = activity["status"] == "awaiting_memory"
    return {
        "ok": True,
        "verdict": (
            "TENOR_CHANGESET_COMMITTED_MEMORY_DECISION_REQUIRED"
            if awaiting
            else "TENOR_CHANGESET_RECOVERED_TERMINAL_STATE"
        ),
        "task_id": row["task_id"],
        "changeset_id": activity["last_changeset_id"],
        "terminal": not awaiting,
        "next_action": "tenor_task_control:memory_promote_or_memory_skip" if awaiting else "READY_FOR_NEXT_TASK",
    }


def _begin_recovery(
    project_root: Path,
    job_id: str,
) -> tuple[WorkerFence, dict[str, Any]] | None:
    now = _now()
    with db.connect(project_root) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute(
                f"SELECT * FROM {JOB_TABLE} WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if (
                not row
                or row["status"] not in {"launching", "running", "recovering"}
                or int(row["lease_expires_at"] or 0) > now
            ):
                con.execute("COMMIT")
                return None
            current_token = int(row["fence_token"] or 0)
            worker_instance_id = f"recovery-{uuid.uuid4().hex}"
            fence_token = current_token + 1
            lease_expires_at = now + _lease_seconds()
            updated = con.execute(
                f"""
                UPDATE {JOB_TABLE}
                SET status='recovering',owner_pid=?,worker_instance_id=?,
                    fence_token=?,lease_expires_at=?,heartbeat_at=?,
                    recovery_prepared=0,updated_at=?
                WHERE job_id=? AND status IN ('launching','running','recovering')
                  AND fence_token=? AND lease_expires_at<=?
                """,
                (
                    os.getpid(),
                    worker_instance_id,
                    fence_token,
                    lease_expires_at,
                    now,
                    now,
                    job_id,
                    current_token,
                    now,
                ),
            ).rowcount
            claimed = con.execute(
                f"SELECT * FROM {JOB_TABLE} WHERE job_id=?",
                (job_id,),
            ).fetchone()
            con.execute("COMMIT")
            if not updated or not claimed:
                return None
            claimed_payload = dict(claimed)
            claimed_payload["_retry_exhausted"] = (
                current_token >= MAX_JOB_ATTEMPTS
            )
            return _fence_from_row(claimed), claimed_payload
        except Exception:
            con.execute("ROLLBACK")
            raise


def _finish_recovery(
    project_root: Path,
    worker_fence: WorkerFence,
) -> bool:
    now = _now()
    lease_expires_at = now + _lease_seconds()
    with db.connect(project_root) as con:
        updated = con.execute(
            f"""
            UPDATE {JOB_TABLE}
            SET status='queued',owner_pid=0,recovery_prepared=1,
                heartbeat_at=?,lease_expires_at=?,updated_at=?,started_at=NULL
            WHERE job_id=? AND status='recovering'
              AND worker_instance_id=? AND fence_token=?
              AND lease_expires_at>?
            """,
            (
                now,
                lease_expires_at,
                now,
                worker_fence.job_id,
                worker_fence.worker_instance_id,
                worker_fence.fence_token,
                now,
            ),
        ).rowcount
    return bool(updated)


def recover_stale_jobs(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    ensure_schema(root)
    now = _now()
    requeued: list[str] = []
    failed: list[str] = []
    reconciled: list[str] = []
    with db.connect(root) as con:
        rows = con.execute(
            f"""
            SELECT * FROM {JOB_TABLE}
            WHERE status IN ('launching','running','recovering')
              AND lease_expires_at<=?
            ORDER BY created_at
            """,
            (now,),
        ).fetchall()
    for row in rows:
        recovery = _begin_recovery(root, str(row["job_id"]))
        if recovery is None:
            with db.connect(root) as con:
                current = con.execute(
                    f"SELECT status FROM {JOB_TABLE} WHERE job_id=?",
                    (row["job_id"],),
                ).fetchone()
            if current and current["status"] == "failed":
                failed.append(str(row["job_id"]))
            continue
        worker_fence, recovery_row = recovery
        terminal = _task_terminal_result(root, recovery_row)
        if terminal:
            completed = complete_job(
                root,
                str(row["job_id"]),
                terminal,
                worker_instance_id=worker_fence.worker_instance_id,
                fence_token=worker_fence.fence_token,
            )
            if completed.get("ok"):
                reconciled.append(str(row["job_id"]))
            continue
        if row["kind"] == "changeset":
            try:
                from . import tenor_changeset

                recovered_changesets = tenor_changeset.recover_incomplete(
                    root,
                    recovery_fence=tenor_changeset.ExecutionFence(
                        job_id=worker_fence.job_id,
                        worker_instance_id=worker_fence.worker_instance_id,
                        fence_token=worker_fence.fence_token,
                    ),
                )
                if not recovered_changesets.get("ok"):
                    raise RuntimeError(str(recovered_changesets))
            except Exception as exc:
                fail_job(
                    root,
                    str(row["job_id"]),
                    {"ok": False, "verdict": "TENOR_JOB_RECOVERY_FAILED", "reason": f"{type(exc).__name__}: {exc}"},
                    worker_instance_id=worker_fence.worker_instance_id,
                    fence_token=worker_fence.fence_token,
                )
                failed.append(str(row["job_id"]))
                continue
        if recovery_row.get("_retry_exhausted"):
            exhausted = {
                "ok": False,
                "verdict": "TENOR_JOB_RETRY_EXHAUSTED",
                "attempts": int(recovery_row.get("attempt_count") or 0),
                "fence_token": worker_fence.fence_token,
            }
            terminal_failure = fail_job(
                root,
                str(row["job_id"]),
                exhausted,
                worker_instance_id=worker_fence.worker_instance_id,
                fence_token=worker_fence.fence_token,
            )
            if terminal_failure.get("status") == "failed":
                failed.append(str(row["job_id"]))
            continue
        if not _finish_recovery(root, worker_fence):
            failed.append(str(row["job_id"]))
            continue
        requeued.append(str(row["job_id"]))
    return {
        "ok": True,
        "verdict": "TENOR_JOB_RECOVERY_COMPLETE",
        "requeued": requeued,
        "failed": failed,
        "reconciled": reconciled,
    }


def recover_and_launch(project_root: Path | str) -> dict[str, Any]:
    recovered = recover_stale_jobs(project_root)
    launched = launch_queued_jobs(project_root)
    return {
        "ok": True,
        "verdict": "TENOR_JOB_RUNTIME_READY",
        "recovery": recovered,
        "launch": launched,
    }
