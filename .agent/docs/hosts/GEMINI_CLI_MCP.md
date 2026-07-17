# Gemini CLI MCP — V2.16 Terrain Guide

## Canonical TENOR entry

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Mechanical local initialization:

```bash
.agent/workflow/scribe/scribe tenor-init --type cli
```

`bootstrap` is internal/legacy, not the public start.

## Host identification and configuration

The exact Gemini CLI MCP configuration schema must be verified against the installed version and current official documentation before editing. Do not infer it from OpenCode, Codex, Claude Code or an old Graphify hook.

Configure only the current project's server when the host supports project scope:

```bash
python3 .agent/mcp/server_entry.py
```

If the schema or scope is unknown:

```text
HOST_GUIDE_INCOMPLETE
```

Do not write global trusted-hook or MCP registries without explicit permission.

## Required V2.16 proof

Inside Gemini CLI, prove the complete `.agent` MCP surface is visible. Local `--list-tools` is not enough.

Then:

1. call `tenor_init_bridge` from the actual Gemini MCP surface;
2. let it prove the project-local receipt, config hash and current root;
3. let it consume the TENOR proof;
4. require terminal `TENOR_INIT_READY`;
5. run one complete MCP micro-write;
6. audit shell/edit/hook bypass behavior.

## Hook caution

Historical Graphify hooks could emit unsupported fields or exit before consuming stdin. After any Graphify/Gemini hook installation or upgrade, simulate the active hook with representative JSON stdin and verify supported output only.

## Direct-write audit

Audit shell commands, native write/edit tools, trusted hooks, redirects, `tee`, `sed -i`, `rm`, `mv`, `cp` and extensions. Any mutation without MCP receipts must yield `DIRECT_WRITE_BYPASS_DETECTED`.

## Terrain verdict

```text
Official MCP schema on installed version: UNVERIFIED
Gemini MCP config on final head: NOT_TESTED
MCP tools visible to Gemini LLM: UNKNOWN
Root binding: UNKNOWN
TENOR_INIT_READY terminal bridge: NOT_TESTED
Complete MCP micro-write: NOT_TESTED
Direct-write bypass: NOT_TESTED
Final verdict: UNKNOWN
```

Update evidence only under `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`.
