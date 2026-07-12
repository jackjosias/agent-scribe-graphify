# Windsurf / Cascade MCP — V2.16 Terrain Guide

## Canonical TENOR entry

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Mechanical local initialization:

```bash
.agent/workflow/scribe/scribe tenor-init --type extension
```

`bootstrap` is internal/legacy, not the public start.

## Host identification and configuration

The exact official Windsurf/Cascade MCP configuration contract remains unverified in this repository. Before editing configuration:

1. verify the installed version and official MCP documentation;
2. confirm project-local support and the exact config schema;
3. configure only the current project's STDIO server:

```bash
python3 .agent/mcp/server_entry.py
```

If the config contract is unknown:

```text
HOST_GUIDE_INCOMPLETE
```

Do not invent a path, copy another host's config or edit global settings without explicit permission.

## Required V2.16 proof

Inside Windsurf/Cascade, prove the complete `.agent` tool surface is visible. Then prove the current root by sentinel hash, call `tenor_init_bridge`, require `TENOR_INIT_BRIDGE_OK`, run one complete MCP micro-write and audit native writes.

Local `--list-tools` alone is insufficient.

## Direct-write audit

Audit Cascade terminal/actions, native edits, approval modes, redirects, `tee`, `sed -i`, `rm`, `mv`, `cp` and plugins. Any mutation without MCP receipts must yield `DIRECT_WRITE_BYPASS_DETECTED`.

## Terrain verdict

```text
Official MCP schema: UNVERIFIED
Windsurf config on final head: NOT_TESTED
MCP tools visible to Windsurf LLM: UNKNOWN
Root binding: UNKNOWN
TENOR_INIT_BRIDGE_OK: NOT_TESTED
Complete MCP micro-write: NOT_TESTED
Direct-write bypass: NOT_TESTED
Final verdict: UNKNOWN
```

Update evidence only under `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`.
