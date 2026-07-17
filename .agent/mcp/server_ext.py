#!/usr/bin/env python3
from __future__ import annotations

"""V2.16 MCP facade with hash-bound first-write discovery hardening."""

import runpy
from pathlib import Path

import server  # type: ignore

_IMPL_PATH = Path(__file__).with_name("_server_ext_first_write_impl.py")
_IMPL_NAMESPACE = runpy.run_path(
    str(_IMPL_PATH),
    run_name="agent_scribe_graphify._server_ext_first_write_impl",
)
for _name, _value in _IMPL_NAMESPACE.items():
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = _value

from runtime import first_write_hardening  # noqa: E402
from runtime import first_write_gate_binding  # noqa: E402
from runtime import patch_queue, task_discovery  # noqa: E402
from runtime import task_discovery_evidence_patch  # noqa: E402
from runtime import tenor_public_api  # noqa: E402

task_discovery_evidence_patch.install(task_discovery, patch_queue)
first_write_hardening.install(server, _IMPL_NAMESPACE)
first_write_gate_binding.install(server)
tenor_public_api.install(server)

scribe_query = server.TOOLS["scribe_query"]
workflow_next = server.TOOLS["workflow_next"]
scope_task_resource = server.TOOLS["scope_task_resource"]
record_task_discovery = server.TOOLS["record_task_discovery"]
tool_schema = server.tool_schema
handle = server.handle
list_tools = server.list_tools
main = server.main

if __name__ == "__main__":
    raise SystemExit(server.main())
