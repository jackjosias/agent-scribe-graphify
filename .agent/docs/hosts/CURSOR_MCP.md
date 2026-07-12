# Cursor MCP — V2.16 Terrain Guide

## Canonical TENOR entry

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Mechanical local initialization:

```bash
.agent/workflow/scribe/scribe tenor-init --type cli
```

`bootstrap` is internal/legacy, not the public start.

## Host configuration

The historical workspace-local candidate is `.cursor/mcp.json`. Verify the current official Cursor MCP schema and installed version before editing. Configure only the current project's STDIO server:

```bash
python3 .agent/mcp/server_entry.py
```

Do not point Cursor to another checkout and do not alter user/global configuration without explicit permission.

## Required V2.16 proof

Local `server_entry.py --list-tools` proves only the server. Inside Cursor, prove the model sees the complete guard/lock/claim/patch/audit/finish/bridge surface.

Then:

1. compare a host sentinel hash with MCP `file_hash`;
2. require the current project root;
3. call `tenor_init_bridge` with the TENOR session and proof;
4. obtain `TENOR_INIT_BRIDGE_OK`;
5. run one complete MCP micro-write;
6. test native edit/terminal bypass behavior.

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

Audit Cursor terminal, agent edits, native patch/write tools, redirects, `tee`, `sed -i`, `rm`, `mv`, `cp` and extensions. Any mutation without MCP receipts must yield `DIRECT_WRITE_BYPASS_DETECTED`.

## Terrain verdict

```text
Cursor MCP config on final head: NOT_TESTED
MCP tools visible to Cursor LLM: UNKNOWN
Root binding: UNKNOWN
TENOR_INIT_BRIDGE_OK: NOT_TESTED
Complete MCP micro-write: NOT_TESTED
Direct-write bypass: NOT_TESTED
Final verdict: UNKNOWN
```

Update evidence only under `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`.
