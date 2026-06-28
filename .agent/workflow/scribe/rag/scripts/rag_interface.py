from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import sys

SCRIPT_PATH = Path(__file__).resolve()


def find_project_root(path: Path) -> Path:
    for ancestor in path.parents:
        if ancestor.name == ".agent":
            return ancestor.parent
    raise RuntimeError(f"Cannot resolve project root from {path}")


def find_portable_root(path: Path) -> Path:
    for ancestor in path.parents:
        if ancestor.name == "scribe" and ancestor.parent.name == "workflow":
            if (ancestor / "sel" / "scribe").exists():
                return ancestor
    raise RuntimeError(f"Cannot resolve portable SCRIBE root from {path}")


SCRIBE_RAG_ROOT = SCRIPT_PATH.parents[1]
SCRIBE_ROOT = find_portable_root(SCRIPT_PATH)
PROJECT_ROOT = find_project_root(SCRIPT_PATH)
SEL_CLI = (SCRIBE_ROOT / "scribe").resolve()
DEFAULT_SCRIBE = PROJECT_ROOT / "AGENT-MEMOIRE_PROJECT_STATUS.scribe"
SEL_SCRIPTS_DIR = SCRIBE_ROOT / "sel" / "scripts"
if str(SEL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SEL_SCRIPTS_DIR))
from scribe_output_paths import scribe_out_dir
RAG_INDEX_PATH = scribe_out_dir(PROJECT_ROOT) / "rag-index.json"


class SELCommandError(RuntimeError):
    pass


def sel(args: list[str], *, cwd: Path | None = None) -> str:
    if not SEL_CLI.exists():
        raise SELCommandError(f"SEL CLI introuvable: {SEL_CLI}")
    result = subprocess.run(
        [str(SEL_CLI), *args],
        cwd=str(cwd or PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SELCommandError(result.stderr.strip() or result.stdout.strip() or f"SEL exited {result.returncode}")
    return result.stdout


def export_scribe(*, include_values: bool = True) -> dict[str, Any]:
    return export_scribe_native(include_values=include_values)


def export_scribe_index(*, include_values: bool = True) -> dict[str, Any]:
    import sys

    scripts_dir = str(SEL_SCRIPTS_DIR)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from scribe_memory_admin import export_entity, json_default, normalized_current_tiers
    from scribe_store import load_scribe

    store = load_scribe(DEFAULT_SCRIBE)
    payload = {
        "source": str(store.path),
        "schema_version": store.data.get("schema_version"),
        "summary": {
            "entities": len(store.entities),
            "ids": len(store.index.id_index),
            "doctor_errors": sum(1 for item in store.findings if item.severity == "ERROR"),
            "doctor_warnings": sum(1 for item in store.findings if item.severity == "WARNING"),
            "source_line_count": len(store.raw.splitlines()),
        },
        "tiers": normalized_current_tiers(store),
        "entities": [export_entity(store, entity, include_values) for entity in store.entities],
    }
    return json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=json_default))


def export_scribe_native(*, include_values: bool = True) -> dict[str, Any]:
    import sys

    scripts_dir = str(SEL_SCRIPTS_DIR)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from scribe_memory_admin import export_payload, json_default
    from scribe_store import load_scribe

    payload = export_payload(load_scribe(DEFAULT_SCRIBE), include_values=include_values)
    return json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=json_default))


def export_scribe_cli(*, include_values: bool = True) -> dict[str, Any]:
    args = ["export", "--format", "json"]
    if include_values:
        args.append("--include-values")
    output = sel(args)
    return json.loads(output)


def doctor() -> str:
    return sel(["doctor", "--suggest-fix"])


def source_snapshot(scribe_path: Path = DEFAULT_SCRIBE) -> dict[str, Any]:
    raw = scribe_path.read_bytes()
    import hashlib
    stat = scribe_path.stat()
    return {
        "path": str(scribe_path),
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "mtime_ns": stat.st_mtime_ns,
        "line_count": len(raw.decode("utf-8").splitlines()),
    }
