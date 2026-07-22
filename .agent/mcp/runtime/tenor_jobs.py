from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import db


JOB_TABLE = "tenor_runtime_jobs_v1"
JOB_KINDS = frozenset({"changeset", "graphify_build"})
ACTIVE_STATUSES = frozenset({"queued", "launching", "running"})
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
MAX_JOB_ATTEMPTS = 3
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_RESULT_BYTES = 512 * 1024
DEFAULT_MAX_WORKERS = 4
LAUNCH_STALE_SECONDS = 30

_LAUNCH_LOCK = threading.RLock()


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


def _public_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["result"] = _load_json(str(data.pop("result_json", "{}")))
    data["error"] = _load_json(str(data.pop("error_json", "{}")))
    data.pop("payload_json", None)
    data["overdue"] = bool(
        data.get("status") in ACTIVE_STATUSES
        and data.get("started_at")
        and _now() > int(data["started_at"]) + int(data.get("max_runtime_seconds") or 0)
    )
    return data


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
            if task_id:
                active = con.execute(
                    f"SELECT * FROM {JOB_TABLE} WHERE task_id=? AND status IN ('queued','launching','running') ORDER BY created_at LIMIT 1",
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


def claim_job(project_root: Path | str, job_id: str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    ensure_schema(root)
    now = _now()
    with db.connect(root) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute(f"SELECT * FROM {JOB_TABLE} WHERE job_id=?", (job_id,)).fetchone()
            if not row or row["status"] not in {"queued", "launching"}:
                con.execute("COMMIT")
                return {
                    "ok": False,
                    "verdict": "TENOR_JOB_NOT_CLAIMABLE",
                    "job_id": job_id,
                    "status": str(row["status"]) if row else "missing",
                }
            increment = 1 if row["status"] == "queued" else 0
            con.execute(
                f"UPDATE {JOB_TABLE} SET status='running',owner_pid=?,attempt_count=attempt_count+?,started_at=COALESCE(started_at,?),updated_at=? WHERE job_id=?",
                (os.getpid(), increment, now, now, job_id),
            )
            db.add_event(
                con,
                "tenor.job_running",
                {"job_id": job_id, "kind": row["kind"], "task_id": row["task_id"], "pid": os.getpid()},
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
        "job": {**_public_row(claimed), "payload": _load_json(str(claimed["payload_json"] or "{}"))},
    }


def _finish_job(
    project_root: Path | str,
    job_id: str,
    *,
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
            con.execute(
                f"UPDATE {JOB_TABLE} SET status=?,owner_pid=0,payload_json='{{}}',result_json=?,error_json=?,updated_at=?,finished_at=? WHERE job_id=?",
                (status, result_json, error_json, now, now, job_id),
            )
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


def complete_job(project_root: Path | str, job_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return _finish_job(project_root, job_id, status="succeeded", result=result)


def fail_job(project_root: Path | str, job_id: str, error: dict[str, Any]) -> dict[str, Any]:
    return _finish_job(project_root, job_id, status="failed", error=error, result=error)


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


def _reserve_launch(project_root: Path, job_id: str) -> bool:
    now = _now()
    with db.connect(project_root) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            active = int(
                con.execute(
                    f"SELECT COUNT(*) AS count FROM {JOB_TABLE} WHERE status IN ('launching','running')",
                ).fetchone()["count"]
            )
            if active >= _max_workers():
                con.execute("COMMIT")
                return False
            updated = con.execute(
                f"UPDATE {JOB_TABLE} SET status='launching',owner_pid=?,attempt_count=attempt_count+1,updated_at=? WHERE job_id=? AND status='queued' AND attempt_count<?",
                (os.getpid(), now, job_id, MAX_JOB_ATTEMPTS),
            ).rowcount
            con.execute("COMMIT")
            return bool(updated)
        except Exception:
            con.execute("ROLLBACK")
            raise


def _worker_command(project_root: Path, job_id: str) -> list[str]:
    worker = Path(__file__).resolve().with_name("tenor_job_worker.py")
    return [sys.executable, str(worker), "--root", str(project_root), "--job-id", job_id]


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


def _spawn_worker(project_root: Path, job_id: str) -> dict[str, Any]:
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
        process = subprocess.Popen(_worker_command(project_root, job_id), **kwargs)
    finally:
        os.close(descriptor)
    with db.connect(project_root) as con:
        con.execute(
            f"UPDATE {JOB_TABLE} SET owner_pid=?,updated_at=? WHERE job_id=? AND status='launching'",
            (process.pid, _now(), job_id),
        )
    threading.Thread(
        target=_reap_process,
        args=(process, project_root),
        name=f"tenor-job-reaper-{job_id}",
        daemon=True,
    ).start()
    return {"job_id": job_id, "pid": process.pid, "log_path": str(log_path)}


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
                f"SELECT job_id FROM {JOB_TABLE} WHERE status='queued' AND attempt_count<? ORDER BY created_at,job_id LIMIT ?",
                (MAX_JOB_ATTEMPTS, capacity),
            ).fetchall()
        for row in rows:
            job_id = str(row["job_id"])
            if not _reserve_launch(root, job_id):
                continue
            try:
                launched.append(_spawn_worker(root, job_id))
            except Exception as exc:
                fail_job(
                    root,
                    job_id,
                    {"ok": False, "verdict": "TENOR_JOB_LAUNCH_FAILED", "reason": f"{type(exc).__name__}: {exc}"},
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


def recover_stale_jobs(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    ensure_schema(root)
    now = _now()
    requeued: list[str] = []
    failed: list[str] = []
    reconciled: list[str] = []
    with db.connect(root) as con:
        rows = con.execute(
            f"SELECT * FROM {JOB_TABLE} WHERE status IN ('launching','running') ORDER BY created_at",
        ).fetchall()
    for row in rows:
        pid = int(row["owner_pid"] or 0)
        stale_launch = row["status"] == "launching" and now - int(row["updated_at"] or 0) >= LAUNCH_STALE_SECONDS
        if not stale_launch and db.process_is_alive(pid):
            continue
        terminal = _task_terminal_result(root, row)
        if terminal:
            complete_job(root, str(row["job_id"]), terminal)
            reconciled.append(str(row["job_id"]))
            continue
        if row["kind"] == "changeset":
            try:
                from . import tenor_changeset

                tenor_changeset.recover_incomplete(root)
            except Exception as exc:
                fail_job(
                    root,
                    str(row["job_id"]),
                    {"ok": False, "verdict": "TENOR_JOB_RECOVERY_FAILED", "reason": f"{type(exc).__name__}: {exc}"},
                )
                failed.append(str(row["job_id"]))
                continue
        if int(row["attempt_count"] or 0) >= MAX_JOB_ATTEMPTS:
            fail_job(
                root,
                str(row["job_id"]),
                {"ok": False, "verdict": "TENOR_JOB_RETRY_EXHAUSTED", "attempts": int(row["attempt_count"] or 0)},
            )
            failed.append(str(row["job_id"]))
            continue
        with db.connect(root) as con:
            con.execute(
                f"UPDATE {JOB_TABLE} SET status='queued',owner_pid=0,updated_at=?,started_at=NULL WHERE job_id=? AND status IN ('launching','running')",
                (now, row["job_id"]),
            )
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
