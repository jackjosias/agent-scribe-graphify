# Kilo Code MCP — V2.16 Terrain Guide

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

The previous guide mentioned `kilo.jsonc`, but the official schema was not confirmed. Before any installation:

1. identify the exact Kilo Code version and official documentation;
2. confirm its MCP configuration file and scope;
3. prefer project/workspace-local configuration;
4. configure the current project's STDIO server only:

```bash
python3 .agent/mcp/server_entry.py
```

If the config contract is still unknown:

```text
HOST_GUIDE_INCOMPLETE
```

Do not invent a config file or write globally.

## Required V2.16 proof

Local `--list-tools` proves only local readiness. Inside Kilo Code, call
`tenor_init_bridge` from the actual MCP surface and require terminal
`TENOR_INIT_READY`; the call proves the receipt/config/root binding. Then run
one complete MCP micro-write and audit controlled native-write paths.

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

Audit terminal, native edit/write tools, approval modes, redirects, `tee`, `sed -i`, `rm`, `mv`, `cp` and plugins. Any mutation without MCP receipts must yield `DIRECT_WRITE_BYPASS_DETECTED`.

## Terrain verdict

```text
Official MCP schema: UNVERIFIED
Kilo MCP config on final head: NOT_TESTED
MCP tools visible to Kilo LLM: UNKNOWN
Root binding: UNKNOWN
TENOR_INIT_READY terminal bridge: NOT_TESTED
Complete MCP micro-write: NOT_TESTED
Direct-write bypass: NOT_TESTED
Final verdict: UNKNOWN
```

Update evidence only under `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`.
