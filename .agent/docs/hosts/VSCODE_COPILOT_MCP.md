# VS Code / GitHub Copilot MCP — V2.16 Terrain Guide

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

The historical workspace-local candidate is `.vscode/mcp.json`. Verify the current VS Code/Copilot MCP schema and installed extension version before editing. Configure only the current project's STDIO server:

```bash
python3 .agent/mcp/server_entry.py
```

Do not use an absolute path to another checkout and do not change user settings without explicit permission.

## Required V2.16 proof

Local `--list-tools` proves only local MCP startup. In the actual Copilot/agent interface, prove the complete `.agent` tool surface is visible.

Then prove:

```text
sentinel hash match
current workspace root
tenor_init_bridge OK
complete MCP micro-write
native terminal/edit bypass controlled
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

Audit terminal commands, workspace edits, native patch/write tools, redirects, `tee`, `sed -i`, `rm`, `mv`, `cp` and extensions. Any mutation lacking MCP receipts must yield `DIRECT_WRITE_BYPASS_DETECTED`.

## Terrain verdict

```text
Workspace MCP config on final head: NOT_TESTED
MCP tools visible to Copilot LLM: UNKNOWN
Root binding: UNKNOWN
TENOR_INIT_BRIDGE_OK: NOT_TESTED
Complete MCP micro-write: NOT_TESTED
Direct-write bypass: NOT_TESTED
Final verdict: UNKNOWN
```

Update evidence only under `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`.
