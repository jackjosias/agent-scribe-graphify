from __future__ import annotations

"""Accept exact path/basename evidence before distinctive-token fallback."""

from pathlib import Path
from typing import Any


def install(task_discovery: Any, patch_queue: Any) -> None:
    if getattr(task_discovery, "_EXACT_EVIDENCE_POLICY_INSTALLED", False):
        return

    def validate_evidence(resource: str, summary: str, evidence: str) -> None:
        clean_summary = (summary or "").strip()
        clean_evidence = (evidence or "").strip()
        if len(clean_summary) < 24:
            raise task_discovery.TaskDiscoveryError("TASK_DISCOVERY_SUMMARY_TOO_SHORT")
        if len(clean_evidence) < 80:
            raise task_discovery.TaskDiscoveryError("TASK_DISCOVERY_EVIDENCE_TOO_SHORT")

        text = f"{clean_summary}\n{clean_evidence}".lower()
        safe = patch_queue.safe_resource(resource)
        lowered = safe.lower().replace("\\", "/")
        basename = Path(lowered).name
        if lowered in text or basename in text:
            return

        tokens = task_discovery._resource_tokens(safe)
        if tokens and any(token in text for token in tokens):
            return
        raise task_discovery.TaskDiscoveryError(
            "TASK_DISCOVERY_RESOURCE_EVIDENCE_REQUIRED",
            {"resource": safe},
        )

    # record_discovery resolves this name from the module globals at call time.
    task_discovery._validate_evidence = validate_evidence
    globals_dict = getattr(task_discovery.record_discovery, "__globals__", None)
    if isinstance(globals_dict, dict):
        globals_dict["_validate_evidence"] = validate_evidence
    task_discovery._EXACT_EVIDENCE_POLICY_INSTALLED = True
