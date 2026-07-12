# Roo Code MCP — V2.16 Terrain Guide

## Canonical TENOR entry

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Mechanical local initialization:

```bash
.agent/workflow/scribe/scribe tenor-init --type extension
```

`bootstrap` is internal/legacy, not the public start.

## Host configuration

The historical workspace-local candidate is `.roo/mcp.json`. Verify the current official Roo Code MCP schema and installed extension version before editing. Configure only the current project's STDIO server:

```bash
python3 .agent/mcp/server_entry.py
```

Do not use another checkout or global configuration without explicit permission.

## Required V2.16 proof

Inside Roo Code, prove the full `.agent` tool surface is visible. Local `--list-tools` is not enough.

Then prove:

```text
host/MCP sentinel hash match
current project root
tenor_init_bridge OK
complete MCP micro-write
native write paths controlled
```

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

Audit Roo Code modes, auto-approval, terminal, native edits, redirects, `tee`, `sed -i`, `rm`, `mv`, `cp` and extension tools. Any mutation without MCP receipts must yield `DIRECT_WRITE_BYPASS_DETECTED`.

## Terrain verdict

```text
Roo MCP config on final head: NOT_TESTED
MCP tools visible to Roo LLM: UNKNOWN
Root binding: UNKNOWN
TENOR_INIT_BRIDGE_OK: NOT_TESTED
Complete MCP micro-write: NOT_TESTED
Direct-write bypass: NOT_TESTED
Final verdict: UNKNOWN
```

Update evidence only under `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`.
