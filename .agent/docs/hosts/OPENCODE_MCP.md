# OpenCode MCP — V2.16 Terrain Guide

## Official source checked

Last verified against the official OpenCode MCP documentation: 2026-07-12.

OpenCode supports local MCP servers under the `mcp` object in `opencode.jsonc`. A local server uses:

- `type: "local"`;
- `command` as an argument array;
- optional `cwd`, with relative paths resolved from the workspace;
- optional `environment`;
- optional `enabled`;
- optional tool-fetch `timeout`.

## Canonical TENOR entry

Start the host conversation with:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

The project-local skill must be read before global OpenCode instructions.

The local mechanical initialization remains:

```bash
.agent/workflow/scribe/scribe tenor-init --type cli
```

## Preferred project-local configuration

Create or update `opencode.jsonc` at the project root without removing unrelated MCP servers:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "agent-scribe-graphify": {
      "type": "local",
      "command": ["python3", ".agent/mcp/server_entry.py"],
      "cwd": ".",
      "enabled": true,
      "timeout": 20000
    }
  }
}
```

Windows example:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "agent-scribe-graphify": {
      "type": "local",
      "command": ["python", ".agent/mcp/server_entry.py"],
      "cwd": ".",
      "enabled": true,
      "timeout": 20000
    }
  }
}
```

`cwd: "."` is important: it binds the launched MCP process to the workspace opened by OpenCode instead of an old absolute checkout.

Do not add an absolute path to the source repository `agent-scribe-graphify`. The server must be the copied project-local `.agent/mcp/server_entry.py`.

Do not edit global/user OpenCode config without explicit permission. If a global entry already exists, inspect it and ask before disabling or removing it. Never remove unrelated servers such as Chrome DevTools.

## Reconnect requirement

After editing `opencode.jsonc`, restart or reconnect OpenCode as required so it reloads MCP configuration. A new conversation may be necessary before the LLM sees the updated tool surface.

## Local-only check

Outside OpenCode:

```bash
python3 .agent/mcp/server_entry.py --list-tools
```

This proves only local server readiness. It does not prove OpenCode exposes the tools to the model.

## OpenCode visibility proof

Inside OpenCode, the LLM must be able to call at least:

```text
workflow_next
before_task
discipline_ping
scribe_query
graphify_query
pre_action_guard
resource_lock_claim
resource_lock_release
claim_resource
file_hash
propose_patch
apply_patch
delete_resource
workspace_audit
scribe_record
finish_task
tenor_init_bridge
portability_check
graphify_required_check
```

OpenCode registers MCP tools alongside built-in tools. The terrain proof must come from the actual OpenCode tool interface/call trace, not from local CLI output.

If visibility is not proven:

```text
HOST_MCP_UNBOUND
LOCAL_INIT_READY_HOST_MCP_UNBOUND
```

## Root-binding proof

1. Create or select a stable sentinel inside the current project.
2. Calculate its hash from the host-visible workspace.
3. Call MCP `file_hash` for the exact relative path.
4. Compare the hashes and resolved root.

Mismatch:

```text
INIT_BLOCKED_MCP_WRONG_ROOT
```

Do not continue to product work after a mismatch.

## Session bridge

After tool visibility and root binding are proven, call:

```text
tenor_init_bridge(
  agent_session_id="<TENOR Agent session>",
  host_tool="opencode",
  model_name="<active model>",
  proof_token="<TENOR Proof token>"
)
```

Required verdict:

```text
TENOR_INIT_BRIDGE_OK
```

Only then may the session report:

```text
TENOR_INIT_READY
```

## Complete micro-write proof

Use a harmless test file in a dedicated validation workspace and execute the complete MCP chain:

```text
workflow_next
before_task
scribe_query
graphify_query
pre_action_guard
resource_lock_claim
claim_resource
file_hash
propose_patch
apply_patch
workspace_audit
scribe_record or auditable skip
release claim and lock
finish_task
workflow_next -> READY_FOR_NEXT_TASK
```

Do not use OpenCode's built-in edit/write tool for this proof.

## Direct-write bypass test

Audit OpenCode built-in mutation paths and permissions:

- shell/bash;
- write/edit/apply-patch;
- `>`, `>>`, `tee`, `sed -i`, `rm`, `mv`, `cp`;
- any plugin or custom tool writing directly to the project.

The test must either be denied/approval-gated or detected as:

```text
DIRECT_WRITE_BYPASS_DETECTED
```

If direct mutation remains freely available, the maximum verdict is `ACCEPTABLE`, not `SAFE`.

## Terrain verdict — still open

```text
Local TENOR INIT: PROVED
Real Graphify binding: PROVED
Local MCP list-tools: PROVED
OpenCode config on final validation workspace: NOT_TESTED
MCP tools visible in OpenCode LLM: UNKNOWN
Root binding: UNKNOWN
TENOR_INIT_BRIDGE_OK: NOT_TESTED
Complete MCP micro-write: NOT_TESTED
Direct write bypass: NOT_TESTED
Final verdict: UNKNOWN
```

Do not edit this section to `PASS` without preserving the actual OpenCode evidence.
