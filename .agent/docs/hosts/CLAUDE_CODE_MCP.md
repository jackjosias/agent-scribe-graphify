# Claude Code MCP — V2.16 Terrain Guide

## Canonical TENOR entry

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Mechanical local initialization:

```bash
.agent/workflow/scribe/scribe tenor-init --type cli --host claude-code
```

`bootstrap` is internal/legacy, not the public start.

## Host configuration

Claude Code supports project-scoped `.mcp.json` with `mcpServers`. TENOR merges only the current project's `agent-scribe-graphify` STDIO entry and preserves unrelated servers:

```json
{
  "mcpServers": {
    "agent-scribe-graphify": {
      "command": "python3",
      "args": [".agent/mcp/server_entry.py"],
      "env": {
        "AGENT_MCP_HOST": "claude-code",
        "AGENT_MCP_BINDING_ID": "<generated-by-TENOR>",
        "AGENT_SCRIBE_GRAPHIFY_ROOT": "."
      }
    }
  }
}
```

Do not handcraft the binding id. After TENOR returns `HOST_RECONNECT_REQUIRED`, restart/reconnect Claude Code and rerun TENOR INIT. Do not use an absolute path to another `.agent` checkout and do not edit user/global configuration without explicit permission.

## Required V2.16 proof

Inside Claude Code, prove the complete MCP tool surface is visible to the model. Local `--list-tools` is not host proof.

Then:

1. compare a sentinel hash from the host workspace and MCP `file_hash`;
2. require matching root;
3. call `tenor_init_bridge` with the TENOR session through the actual host-bound process; the server consumes its one-time proof without exposing a token;
4. obtain `TENOR_INIT_BRIDGE_OK`;
5. execute one complete MCP micro-write;
6. audit native shell/edit bypass paths.

Wrong root:

```text
INIT_BLOCKED_MCP_WRONG_ROOT
```

Unproven host:

```text
HOST_MCP_UNBOUND
LOCAL_INIT_READY_HOST_MCP_UNBOUND
```

## Direct-write audit

Verify Claude Code permissions for shell, native edits, redirects, `tee`, `sed -i`, `rm`, `mv`, `cp` and custom scripts. A mutation outside the MCP receipts must yield `DIRECT_WRITE_BYPASS_DETECTED`.

## Terrain verdict

```text
MCP configuration on final head: NOT_TESTED
MCP tools visible to Claude Code LLM: UNKNOWN
Root binding: UNKNOWN
TENOR_INIT_BRIDGE_OK: NOT_TESTED
Complete MCP micro-write: NOT_TESTED
Direct-write bypass: NOT_TESTED
Final verdict: UNKNOWN
```

Update this evidence only under `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`.
