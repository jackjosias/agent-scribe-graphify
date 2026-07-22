from __future__ import annotations

import os
import sys
from pathlib import Path


MCP_DIR = Path(__file__).resolve().parent.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from runtime import graphify_readiness, installation_state


def prepare_graphify_fixture(root: Path) -> dict[str, str]:
    project_root = root.resolve()
    graph = graphify_readiness.write_smoke_fixture(project_root)
    if not graph.get("ok"):
        raise RuntimeError(f"test Graphify fixture failed: {graph}")
    return {
        **os.environ,
        "AGENT_SCRIBE_GRAPHIFY_ROOT": str(project_root),
        graphify_readiness.FIXTURE_ENV: "1",
    }


def prepare_installed_workspace(root: Path) -> dict[str, str]:
    """Create current installation and Graphify evidence for a test workspace."""

    project_root = root.resolve()
    prepared = installation_state.ensure_fresh_installation_state(project_root)
    if not prepared.get("ok"):
        raise RuntimeError(f"test installation preparation failed: {prepared}")
    finalized = installation_state.finalize_installation_state(project_root)
    if not finalized.get("ok"):
        raise RuntimeError(f"test installation finalization failed: {finalized}")
    return prepare_graphify_fixture(project_root)
