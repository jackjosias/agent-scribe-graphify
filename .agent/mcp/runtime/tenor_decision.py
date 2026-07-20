from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from runtime import db, graphify_readiness


CAPSULE_TABLE = "tenor_decision_capsules_v1"
MAX_EVIDENCE_EXCERPT = 4096
MUTATING_INTENTS = {"write", "delete"}


def _now() -> int:
    return int(time.time())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return _sha256_bytes(path.read_bytes())


def _evidence(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    stdout = str(result.get("stdout") or payload.get("context") or "")
    return {
        "ok": payload.get("ok") is not False,
        "verdict": str(payload.get("verdict") or ""),
        "payload_hash": _sha256_bytes(_json(payload).encode("utf-8")),
        "context_excerpt": stdout[:MAX_EVIDENCE_EXCERPT],
        "context_truncated": len(stdout) > MAX_EVIDENCE_EXCERPT,
    }


def _snapshot_hashes(root: Path) -> dict[str, str]:
    return {
        "scribe_memory": _file_hash(root / "AGENT-MEMOIRE_PROJECT_STATUS.scribe"),
        "graphify_manifest": _file_hash(graphify_readiness.manifest_path(root)),
    }


def ensure_schema(project_root: Path) -> None:
    root = project_root.resolve()
    db.init_db(root)
    with db.connect(root) as con:
        con.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {CAPSULE_TABLE}(
              task_id TEXT PRIMARY KEY,
              agent_id TEXT NOT NULL,
              capsule_hash TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status TEXT NOT NULL,
              resolution_ref TEXT NOT NULL DEFAULT '',
              created_at INTEGER NOT NULL,
              resolved_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_{CAPSULE_TABLE}_agent_status
              ON {CAPSULE_TABLE}(agent_id,status,created_at);
            """
        )


def load_capsule(project_root: Path, task_id: str) -> dict[str, Any] | None:
    root = project_root.resolve()
    ensure_schema(root)
    with db.connect(root) as con:
        row = con.execute(f"SELECT * FROM {CAPSULE_TABLE} WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["payload"] = json.loads(data.pop("payload_json") or "{}")
    return data


def build_capsule(
    *,
    project_root: Path,
    task_id: str,
    agent_id: str,
    objective: str,
    intent: str,
    scope: str,
    resources: list[str],
    scribe_result: dict[str, Any],
    graphify_result: dict[str, Any],
    graphify_required: bool,
    refresh_existing: bool = False,
) -> dict[str, Any]:
    root = project_root.resolve()
    if not task_id or not agent_id or not objective.strip() or not resources:
        return {"ok": False, "verdict": "TENOR_DECISION_CAPSULE_INPUT_INVALID"}
    scribe = _evidence(scribe_result)
    graphify = _evidence(graphify_result)
    if not scribe["verdict"] or not scribe["ok"]:
        return {"ok": False, "verdict": "TENOR_DECISION_SCRIBE_EVIDENCE_REQUIRED", "task_id": task_id}
    if graphify_required and (not graphify["verdict"] or not graphify["ok"]):
        return {"ok": False, "verdict": "TENOR_DECISION_GRAPHIFY_EVIDENCE_REQUIRED", "task_id": task_id}
    normalized_resources = sorted(dict.fromkeys(str(item).strip().replace("\\", "/") for item in resources if str(item).strip()))
    payload = {
        "schema": "tenor_decision_capsule_v1",
        "task_id": task_id,
        "agent_id": agent_id,
        "objective": " ".join(objective.split()),
        "intent": intent,
        "scope": scope,
        "resources": normalized_resources,
        "scribe": scribe,
        "graphify": graphify,
        "graphify_required": bool(graphify_required),
        "snapshots": _snapshot_hashes(root),
    }
    capsule_hash = _sha256_bytes(_json(payload).encode("utf-8"))
    ensure_schema(root)
    with db.connect(root) as con:
        existing = con.execute(f"SELECT * FROM {CAPSULE_TABLE} WHERE task_id=?", (task_id,)).fetchone()
        if existing:
            if existing["agent_id"] != agent_id:
                return {"ok": False, "verdict": "TENOR_DECISION_OWNER_MISMATCH", "task_id": task_id}
            if existing["status"] != "active":
                return {"ok": False, "verdict": "TENOR_DECISION_CAPSULE_NOT_ACTIVE", "task_id": task_id}
            if refresh_existing:
                con.execute(
                    f"UPDATE {CAPSULE_TABLE} SET capsule_hash=?,payload_json=?,created_at=? WHERE task_id=?",
                    (capsule_hash, _json(payload), _now(), task_id),
                )
                db.add_event(
                    con,
                    "tenor.decision_capsule_refreshed",
                    {"task_id": task_id, "capsule_hash": capsule_hash, "resources": normalized_resources},
                    agent_id,
                )
                return {
                    "ok": True,
                    "verdict": "TENOR_DECISION_CAPSULE_REFRESHED",
                    "task_id": task_id,
                    "capsule_hash": capsule_hash,
                    "scribe": scribe,
                    "graphify": graphify,
                    "snapshots": payload["snapshots"],
                }
            if existing["capsule_hash"] != capsule_hash:
                return {"ok": False, "verdict": "TENOR_DECISION_CAPSULE_CONFLICT", "task_id": task_id}
            stored = json.loads(existing["payload_json"] or "{}")
            return {
                "ok": True,
                "verdict": "TENOR_DECISION_CAPSULE_EXISTS",
                "task_id": task_id,
                "capsule_hash": capsule_hash,
                "scribe": stored.get("scribe", {}),
                "graphify": stored.get("graphify", {}),
            }
        con.execute(
            f"INSERT INTO {CAPSULE_TABLE}(task_id,agent_id,capsule_hash,payload_json,status,created_at) VALUES(?,?,?,?,?,?)",
            (task_id, agent_id, capsule_hash, _json(payload), "active", _now()),
        )
        db.add_event(
            con,
            "tenor.decision_capsule_ready",
            {"task_id": task_id, "capsule_hash": capsule_hash, "resources": normalized_resources},
            agent_id,
        )
    return {
        "ok": True,
        "verdict": "TENOR_DECISION_CAPSULE_READY",
        "task_id": task_id,
        "capsule_hash": capsule_hash,
        "scribe": scribe,
        "graphify": graphify,
        "snapshots": payload["snapshots"],
    }


def verify_capsule(
    project_root: Path,
    task_id: str,
    agent_id: str,
    resources: list[str],
) -> dict[str, Any]:
    root = project_root.resolve()
    capsule = load_capsule(root, task_id)
    if not capsule:
        return {"ok": False, "verdict": "TENOR_DECISION_CAPSULE_REQUIRED", "task_id": task_id}
    if capsule["agent_id"] != agent_id:
        return {"ok": False, "verdict": "TENOR_DECISION_OWNER_MISMATCH", "task_id": task_id}
    if capsule["status"] != "active":
        return {"ok": False, "verdict": "TENOR_DECISION_CAPSULE_NOT_ACTIVE", "task_id": task_id}
    payload = capsule["payload"]
    normalized_resources = sorted(dict.fromkeys(str(item).strip().replace("\\", "/") for item in resources if str(item).strip()))
    if normalized_resources != payload.get("resources"):
        return {
            "ok": False,
            "verdict": "TENOR_DECISION_RESOURCE_MISMATCH",
            "task_id": task_id,
            "capsule_resources": payload.get("resources", []),
            "requested_resources": normalized_resources,
        }
    current = _snapshot_hashes(root)
    expected = payload.get("snapshots") or {}
    stale = sorted(name for name in ("scribe_memory", "graphify_manifest") if current.get(name, "") != expected.get(name, ""))
    if stale:
        return {
            "ok": False,
            "verdict": "TENOR_DECISION_CAPSULE_STALE",
            "task_id": task_id,
            "stale_components": stale,
            "expected_snapshots": expected,
            "current_snapshots": current,
            "next_action": "tenor_task_start:same_objective_refresh",
        }
    return {
        "ok": True,
        "verdict": "TENOR_DECISION_CAPSULE_VERIFIED",
        "task_id": task_id,
        "capsule_hash": capsule["capsule_hash"],
        "scribe": payload.get("scribe", {}),
        "graphify": payload.get("graphify", {}),
        "snapshots": expected,
    }


def resolve_capsule(project_root: Path, task_id: str, agent_id: str, resolution_ref: str) -> dict[str, Any]:
    root = project_root.resolve()
    ensure_schema(root)
    with db.connect(root) as con:
        row = con.execute(f"SELECT * FROM {CAPSULE_TABLE} WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            return {"ok": False, "verdict": "TENOR_DECISION_CAPSULE_REQUIRED", "task_id": task_id}
        if row["agent_id"] != agent_id:
            return {"ok": False, "verdict": "TENOR_DECISION_OWNER_MISMATCH", "task_id": task_id}
        if row["status"] != "active":
            return {"ok": False, "verdict": "TENOR_DECISION_CAPSULE_NOT_ACTIVE", "task_id": task_id}
        con.execute(
            f"UPDATE {CAPSULE_TABLE} SET status='resolved',resolution_ref=?,resolved_at=? WHERE task_id=?",
            (resolution_ref or "resolved", _now(), task_id),
        )
        db.add_event(con, "tenor.decision_capsule_resolved", {"task_id": task_id, "resolution_ref": resolution_ref}, agent_id)
    return {"ok": True, "verdict": "TENOR_DECISION_CAPSULE_RESOLVED", "task_id": task_id}
