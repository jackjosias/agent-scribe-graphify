from __future__ import annotations

from typing import Any

from runtime import patch_queue


def file_hash(args: dict[str, Any]) -> dict[str, Any]:
    resource = str(args.get("resource") or "")
    return {"ok": True, "status": "FILE_HASH", **patch_queue.file_hash(resource)}


def propose_patch(args: dict[str, Any]) -> dict[str, Any]:
    return patch_queue.propose_patch(
        agent_id=str(args.get("agent_id") or ""),
        target=str(args.get("target") or args.get("target_path") or ""),
        base_hash=str(args.get("base_hash") or ""),
        diff_text=str(args.get("diff_text") or ""),
        metadata=args.get("metadata") if isinstance(args.get("metadata"), dict) else {},
    )


def list_patches(args: dict[str, Any]) -> dict[str, Any]:
    return patch_queue.list_patches(target=args.get("target"), status=args.get("status"))
