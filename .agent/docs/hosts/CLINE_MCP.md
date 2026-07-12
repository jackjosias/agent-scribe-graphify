# Cline MCP — V2.16 Terrain Guide

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

The historical Cline configuration surface is `mcp.json`. Verify the current official Cline MCP schema and installed extension version before editing. Configure the current project's STDIO server only:

```bash
python3 .agent/mcp/server_entry.py
```

Prefer project/workspace scope when supported. Do not point to another checkout or change global configuration without explicit permission.

## Required V2.16 proof

Inside Cline, prove the complete guard/lock/claim/patch/audit/finish/bridge surface is visible to the model. Local `--list-tools` is insufficient.

Then prove a matching sentinel hash, current root, `TENOR_INIT_BRIDGE_OK`, one complete MCP micro-write and controlled native-write behavior.

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

Audit Cline terminal access, native file edits, auto-approval settings, redirects, `tee`, `sed -i`, `rm`, `mv`, `cp` and extension tools. Any mutation without MCP receipts must yield `DIRECT_WRITE_BYPASS_DETECTED`.

## Terrain verdict

```text
Cline MCP config on final head: NOT_TESTED
MCP tools visible to Cline LLM: UNKNOWN
Root binding: UNKNOWN
TENOR_INIT_BRIDGE_OK: NOT_TESTED
Complete MCP micro-write: NOT_TESTED
Direct-write bypass: NOT_TESTED
Final verdict: UNKNOWN
```

Update evidence only under `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`.
