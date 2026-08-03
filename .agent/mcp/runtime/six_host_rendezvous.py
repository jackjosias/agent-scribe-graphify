from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any


EXPECTED_HOSTS = 6
EXPECTED_ACTIVITY_CALLS = 8
READY_PHASE = "ready"
OBSERVED_PHASE = "observed"
_SCHEMA_VERSION = 1


class RendezvousError(RuntimeError):
    pass


def _connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        str(database),
        timeout=30,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA synchronous=FULL")
    return connection


def initialize(
    database: Path,
    *,
    run_id: str,
    root: str,
    commit_sha: str,
    tree_sha: str,
    model: str,
    cli_version: str,
    expected_hosts: int = EXPECTED_HOSTS,
) -> None:
    if expected_hosts != EXPECTED_HOSTS:
        raise RendezvousError("RENDEZVOUS_EXPECTED_HOSTS_MUST_BE_SIX")
    database.parent.mkdir(parents=True, exist_ok=True)
    with _connect(database) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS replay_meta (
              singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
              schema_version INTEGER NOT NULL,
              run_id TEXT NOT NULL UNIQUE,
              root TEXT NOT NULL,
              commit_sha TEXT NOT NULL,
              tree_sha TEXT NOT NULL,
              model TEXT NOT NULL,
              cli_version TEXT NOT NULL,
              expected_hosts INTEGER NOT NULL,
              created_at_ns INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS replay_participants (
              participant_id INTEGER PRIMARY KEY,
              run_id TEXT NOT NULL,
              agent_session_id TEXT NOT NULL UNIQUE,
              mcp_pid INTEGER NOT NULL UNIQUE,
              host_pid INTEGER NOT NULL UNIQUE,
              model TEXT NOT NULL,
              bridged_at_ns INTEGER NOT NULL,
              ready_at_ns INTEGER,
              observed_at_ns INTEGER,
              FOREIGN KEY (run_id) REFERENCES replay_meta(run_id)
            );
            CREATE TABLE IF NOT EXISTS replay_calls (
              participant_id INTEGER NOT NULL,
              sequence INTEGER NOT NULL,
              phase TEXT NOT NULL CHECK (phase IN ('ready', 'observed')),
              called_at_ns INTEGER NOT NULL,
              PRIMARY KEY (participant_id, sequence),
              FOREIGN KEY (participant_id)
                REFERENCES replay_participants(participant_id)
            );
            """
        )
        row = connection.execute(
            "SELECT * FROM replay_meta WHERE singleton=1"
        ).fetchone()
        expected = {
            "schema_version": _SCHEMA_VERSION,
            "run_id": run_id,
            "root": root,
            "commit_sha": commit_sha,
            "tree_sha": tree_sha,
            "model": model,
            "cli_version": cli_version,
            "expected_hosts": expected_hosts,
        }
        if row is None:
            connection.execute(
                """
                INSERT INTO replay_meta(
                  singleton,schema_version,run_id,root,commit_sha,tree_sha,
                  model,cli_version,expected_hosts,created_at_ns
                ) VALUES(1,?,?,?,?,?,?,?,?,?)
                """,
                (
                    _SCHEMA_VERSION,
                    run_id,
                    root,
                    commit_sha,
                    tree_sha,
                    model,
                    cli_version,
                    expected_hosts,
                    time.time_ns(),
                ),
            )
        else:
            actual = {key: row[key] for key in expected}
            if actual != expected:
                raise RendezvousError(
                    f"RENDEZVOUS_META_MISMATCH expected={expected!r} actual={actual!r}"
                )


def register_bridge(
    database: Path,
    *,
    run_id: str,
    participant_id: int,
    agent_session_id: str,
    mcp_pid: int,
    host_pid: int,
    model: str,
) -> dict[str, Any]:
    if not 1 <= participant_id <= EXPECTED_HOSTS:
        raise RendezvousError("RENDEZVOUS_PARTICIPANT_INVALID")
    if not agent_session_id:
        raise RendezvousError("RENDEZVOUS_AGENT_SESSION_REQUIRED")
    with _connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        meta = connection.execute(
            "SELECT run_id,model,expected_hosts FROM replay_meta WHERE singleton=1"
        ).fetchone()
        if meta is None or meta["run_id"] != run_id:
            raise RendezvousError("RENDEZVOUS_RUN_ID_MISMATCH")
        if meta["model"] != model:
            raise RendezvousError("RENDEZVOUS_MODEL_MISMATCH")
        existing = connection.execute(
            "SELECT * FROM replay_participants WHERE participant_id=?",
            (participant_id,),
        ).fetchone()
        values = (
            run_id,
            agent_session_id,
            int(mcp_pid),
            int(host_pid),
            model,
        )
        if existing is None:
            connection.execute(
                """
                INSERT INTO replay_participants(
                  participant_id,run_id,agent_session_id,mcp_pid,host_pid,
                  model,bridged_at_ns
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (participant_id, *values, time.time_ns()),
            )
        else:
            actual = tuple(
                existing[key]
                for key in (
                    "run_id",
                    "agent_session_id",
                    "mcp_pid",
                    "host_pid",
                    "model",
                )
            )
            if actual != values:
                raise RendezvousError("RENDEZVOUS_BRIDGE_IDENTITY_CONFLICT")
        connection.commit()
    return snapshot(database, run_id=run_id)


def _expected_phase(sequence: int) -> str:
    if not 1 <= sequence <= EXPECTED_ACTIVITY_CALLS:
        raise RendezvousError("RENDEZVOUS_SEQUENCE_INVALID")
    return READY_PHASE if sequence <= 4 else OBSERVED_PHASE


def _wait_for_quorum(
    database: Path,
    *,
    run_id: str,
    phase: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        current = snapshot(database, run_id=run_id)
        if current[f"{phase}_count"] == current["expected_hosts"]:
            return current
        if time.monotonic() >= deadline:
            raise RendezvousError(
                f"RENDEZVOUS_{phase.upper()}_TIMEOUT "
                f"count={current[f'{phase}_count']}"
            )
        time.sleep(0.05)


def record_activity(
    database: Path,
    *,
    run_id: str,
    participant_id: int,
    agent_session_id: str,
    phase: str,
    sequence: int,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    expected_phase = _expected_phase(sequence)
    if phase != expected_phase:
        raise RendezvousError(
            f"RENDEZVOUS_PHASE_SEQUENCE_MISMATCH "
            f"sequence={sequence} expected={expected_phase} actual={phase}"
        )
    if not 1.0 <= float(timeout_seconds) <= 300.0:
        raise RendezvousError("RENDEZVOUS_TIMEOUT_INVALID")
    now = time.time_ns()
    with _connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        participant = connection.execute(
            """
            SELECT * FROM replay_participants
            WHERE participant_id=? AND run_id=?
            """,
            (participant_id, run_id),
        ).fetchone()
        if participant is None:
            raise RendezvousError("RENDEZVOUS_BRIDGE_REQUIRED")
        if participant["agent_session_id"] != agent_session_id:
            raise RendezvousError("RENDEZVOUS_AGENT_SESSION_MISMATCH")
        previous = connection.execute(
            "SELECT COALESCE(MAX(sequence),0) FROM replay_calls WHERE participant_id=?",
            (participant_id,),
        ).fetchone()[0]
        if int(previous) + 1 != sequence:
            raise RendezvousError(
                f"RENDEZVOUS_NON_MONOTONE_SEQUENCE "
                f"previous={previous} requested={sequence}"
            )
        if sequence == 5:
            ready_count = connection.execute(
                "SELECT COUNT(*) FROM replay_participants WHERE ready_at_ns IS NOT NULL"
            ).fetchone()[0]
            expected_hosts = connection.execute(
                "SELECT expected_hosts FROM replay_meta WHERE singleton=1"
            ).fetchone()[0]
            if int(ready_count) != int(expected_hosts):
                raise RendezvousError("RENDEZVOUS_OBSERVED_BEFORE_READY_QUORUM")
        connection.execute(
            """
            INSERT INTO replay_calls(participant_id,sequence,phase,called_at_ns)
            VALUES(?,?,?,?)
            """,
            (participant_id, sequence, phase, now),
        )
        if sequence == 1:
            connection.execute(
                "UPDATE replay_participants SET ready_at_ns=? WHERE participant_id=?",
                (now, participant_id),
            )
        elif sequence == 5:
            connection.execute(
                "UPDATE replay_participants SET observed_at_ns=? WHERE participant_id=?",
                (now, participant_id),
            )
        connection.commit()
    current = snapshot(database, run_id=run_id)
    if sequence in {1, 5}:
        current = _wait_for_quorum(
            database,
            run_id=run_id,
            phase=phase,
            timeout_seconds=float(timeout_seconds),
        )
    return {
        "ok": True,
        "verdict": "TENOR_SIX_HOST_RENDEZVOUS",
        "run_id": run_id,
        "participant_id": participant_id,
        "phase": phase,
        "sequence": sequence,
        "ready": current["ready_count"] == current["expected_hosts"],
        "observed": current["observed_count"] == current["expected_hosts"],
        "ready_count": current["ready_count"],
        "observed_count": current["observed_count"],
        "expected_hosts": current["expected_hosts"],
        "activity_call_count": current["activity_call_count"],
    }


def snapshot(database: Path, *, run_id: str) -> dict[str, Any]:
    with _connect(database) as connection:
        meta = connection.execute(
            "SELECT * FROM replay_meta WHERE singleton=1"
        ).fetchone()
        if meta is None or meta["run_id"] != run_id:
            raise RendezvousError("RENDEZVOUS_RUN_ID_MISMATCH")
        participants = connection.execute(
            "SELECT * FROM replay_participants ORDER BY participant_id"
        ).fetchall()
        calls = connection.execute(
            """
            SELECT participant_id,sequence,phase,called_at_ns
            FROM replay_calls ORDER BY participant_id,sequence
            """
        ).fetchall()
        return {
            "run_id": run_id,
            "expected_hosts": int(meta["expected_hosts"]),
            "participant_count": len(participants),
            "ready_count": sum(row["ready_at_ns"] is not None for row in participants),
            "observed_count": sum(
                row["observed_at_ns"] is not None for row in participants
            ),
            "activity_call_count": len(calls),
            "participants": [dict(row) for row in participants],
            "calls": [dict(row) for row in calls],
        }


def integrity_check(database: Path) -> dict[str, str]:
    with _connect(database) as connection:
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    return {"quick_check": quick, "integrity_check": integrity}
