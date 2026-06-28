from __future__ import annotations

import json
from pathlib import Path

import sys
from typing import Any

from rag_interface import PROJECT_ROOT, RAG_INDEX_PATH, SEL_SCRIPTS_DIR
if str(SEL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SEL_SCRIPTS_DIR))
from scribe_output_paths import scribe_out_dir

STATE_PATH = scribe_out_dir(PROJECT_ROOT) / "state.json"
LOCK_PATH = scribe_out_dir(PROJECT_ROOT) / "locks" / "scribe.lock"


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def format_whoami(
    *,
    state_path: Path = STATE_PATH,
    lock_path: Path = LOCK_PATH,
    index_path: Path = RAG_INDEX_PATH,
) -> str:
    state = read_json(state_path) or {}
    lock = read_json(lock_path)
    index = read_json(index_path) or {}
    writer = state.get("writer") if isinstance(state.get("writer"), dict) else {}

    lines = [
        "SCRIBE-RAG WHOAMI",
        f"Last writer : {writer.get('agent', '-')}",
        f"Last session: {state.get('last_session', '-')}",
        f"Lock status : {'locked' if lock else 'unlocked'}",
    ]
    if lock:
        lines.extend(
            [
                f"Lock owner  : {lock.get('agent', '-')}",
                f"Lock surface: {lock.get('surface', '-')}",
                f"Lock since  : {lock.get('acquired_at', '-')}",
            ]
        )
    lines.extend(
        [
            f"Index mode  : {index.get('mode', 'absent')}",
            "Eval status : run .agent/workflow/scribe/scribe-rag eval --force",
        ]
    )
    return "\n".join(lines) + "\n"
