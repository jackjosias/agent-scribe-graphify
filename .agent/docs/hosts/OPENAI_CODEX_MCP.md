# OpenAI Codex MCP — V2.16 Terrain Guide

## Current official capability

Codex CLI, the Codex IDE extension and the ChatGPT desktop Codex host support MCP servers. Codex stores MCP configuration in `config.toml`; project-scoped `.codex/config.toml` is supported for trusted projects. STDIO servers are launched by a command.

## Canonical TENOR entry

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

The local project skill is read first. Mechanical initialization:

```bash
.agent/workflow/scribe/scribe tenor-init --type cli
```

## Preferred project scope

Prefer a trusted project-local `.codex/config.toml` rather than a global path to another checkout. The configured command must launch the current project's:

```bash
python3 .agent/mcp/server_entry.py
```

Do not point Codex at the source repository's `.agent`. Do not modify `~/.codex/config.toml` without explicit permission.

## Required proof

Local `--list-tools` proves only the local server. Inside Codex, prove that the complete required MCP surface is visible, including guard, locks, claims, patch queue, audit, finish and `tenor_init_bridge`.

Then prove root binding with a sentinel hash and call:

```text
tenor_init_bridge(
  agent_session_id="<TENOR session>",
  host_tool="codex",
  model_name="<active model>",
  proof_token="<TENOR proof>"
)
```

Only `TENOR_INIT_BRIDGE_OK` plus host/root proof permits `TENOR_INIT_READY`.

## Native mutation audit

Codex may expose shell and native patch/edit capabilities. Verify approvals/sandboxing for:

```text
shell commands
write/edit/apply-patch
>, >>, tee, sed -i
rm, mv, cp
```

A mutation without MCP receipts must become `DIRECT_WRITE_BYPASS_DETECTED`.

## Terrain evidence

Earlier evidence showed `.agent` MCP tools visible while direct shell/edit remained available. That historical observation is insufficient for V2.16 because it did not prove the current root binding, bridge, complete tool surface, micro-write and bypass behavior.

```text
Local TENOR INIT: PROVED on isolated projects
Local MCP list-tools: PROVED
Codex tools visible on final head: NOT_REPLAYED
Root binding: NOT_TESTED
TENOR_INIT_BRIDGE_OK: NOT_TESTED
Complete MCP micro-write: NOT_TESTED
Direct-write bypass: OPEN
Final verdict: UNKNOWN
```

Follow `.agent/docs/DOCUMENTATION_SYNC_POLICY.md` when this verdict changes.
