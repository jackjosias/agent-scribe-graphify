from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from . import patch_queue
except Exception:
    import patch_queue  # type: ignore


def required_confirmation(resource: str) -> str:
    return f"DELETE {resource}"


def delete_resource(
    agent_id: str,
    resource: str,
    base_hash: str,
    confirm_phrase: str = "",
    reason: str = "",
) -> dict[str, Any]:
    patch_queue.ensure_schema()
    if not agent_id:
        raise patch_queue.PatchQueueError("agent_id is required")
    safe = patch_queue.safe_resource(resource)
    expected = required_confirmation(safe)

    if confirm_phrase != expected:
        return {
            "ok": False,
            "verdict": "DELETE_CONFIRMATION_REQUIRED",
            "resource": safe,
            "required_confirmation": expected,
            "reason": "Explicit user permission is required before deleting this resource.",
        }

    if not base_hash:
        raise patch_queue.PatchQueueError("base_hash is required")

    current = patch_queue.file_hash(safe)
    if not current.get("exists"):
        raise patch_queue.PatchQueueError("resource does not exist")
    if current["hash"] != base_hash:
        return {
            "ok": False,
            "verdict": "DELETE_REFUSED_BASE_HASH_MISMATCH",
            "resource": safe,
            "provided_base_hash": base_hash,
            "current_hash": current["hash"],
        }

    target = patch_queue.resolve_project_path(patch_queue.root() / safe)
    if not target.is_file():
        raise patch_queue.PatchQueueError("only regular files can be deleted by delete_resource")

    patch_id = f"delete-{patch_queue.now_ts()}-{hashlib.sha256((agent_id + safe + base_hash).encode()).hexdigest()[:10]}"
    with patch_queue.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            patch_queue.require_claim(con, agent_id, safe)
            pending = con.execute(
                "SELECT patch_id,status FROM patches_v2 WHERE target_path=? AND status IN ('proposed','conflict')",
                (safe,),
            ).fetchall()
            if pending:
                raise patch_queue.PatchQueueError("cannot delete resource with pending proposed/conflict patches")

            current_locked = patch_queue.file_hash(safe)
            if current_locked["hash"] != base_hash:
                raise patch_queue.PatchQueueError("resource changed before deletion")

            target.unlink()
            ts = patch_queue.now_ts()
            metadata = {
                "operation": "delete_resource",
                "deleted_hash": base_hash,
                "deleted_size_bytes": current_locked.get("size_bytes", 0),
                "permission": confirm_phrase,
            }
            if reason:
                metadata["reason"] = reason
            con.execute(
                "INSERT INTO patches_v2(patch_id,agent_id,target_path,base_hash,diff_text,changed_ranges_json,status,created_at,updated_at,reason,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (patch_id, agent_id, safe, base_hash, "", "[]", "deleted", ts, ts, reason or "deleted via MCP delete_resource", json.dumps(metadata, ensure_ascii=False, sort_keys=True)),
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    return {
        "ok": True,
        "verdict": "RESOURCE_DELETED",
        "patch_id": patch_id,
        "resource": safe,
        "deleted_hash": base_hash,
    }
