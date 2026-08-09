# OpenAI Codex MCP — V2.16 Terrain Guide

## Current official capability

Codex CLI, the Codex IDE extension and the ChatGPT desktop Codex host support MCP servers. Codex stores MCP configuration in `config.toml`; project-scoped `.codex/config.toml` is supported for trusted projects. STDIO servers are launched by a command.

## Canonical TENOR entry

```text
TENOR INIT ::[— depuis la racine du workspace courant, lis comme un fichier local avec l’outil normal de lecture de fichiers — jamais avec un résolveur de skills — le chemin exact "./.agent/skills/init-tenor/SKILL.md"; n’utilise jamais "~/.agent", "~/.agents" ni aucun chemin global; applique ensuite intégralement ce fichier et continue automatiquement jusqu’à TENOR_INIT_READY, HOST_RECONNECT_REQUIRED ou un verdict FAIL_CLOSED explicite.]
```

The local project skill is read first. Mechanical initialization:

```bash
.agent/workflow/scribe/scribe tenor-init --type cli --host codex-cli
```

## Preferred project scope

Prefer a trusted project-local `.codex/config.toml` rather than a global path
to another checkout. TENOR owns one delimited managed block for this server.
Codex receives the resolved absolute project root for both `cwd` and
`AGENT_SCRIBE_GRAPHIFY_ROOT`. A relative `cwd="."` was observed to resolve from
an isolated `CODEX_HOME` instead of the repository and hid the MCP tools from
the real host. The generated `.codex/` directory is therefore checkout-local
runtime state and is ignored by Git.

```toml
# agent-scribe-graphify:host-config:start
[mcp_servers."agent-scribe-graphify"]
command = "python3"
args = [".agent/mcp/server_entry.py"]
cwd = "<absolute-project-root-generated-by-TENOR>"
enabled = true
startup_timeout_sec = 20
tool_timeout_sec = 60
default_tools_approval_mode = "approve"

[mcp_servers."agent-scribe-graphify".env]
AGENT_MCP_HOST = "codex-cli"
AGENT_MCP_BINDING_ID = "<generated-by-TENOR>"
AGENT_SCRIBE_GRAPHIFY_ROOT = "<absolute-project-root-generated-by-TENOR>"
# agent-scribe-graphify:host-config:end
```

Do not handcraft the binding id. TENOR records it with the config hash, returns `HOST_RECONNECT_REQUIRED` after a change, and issues no session proof until Codex is restarted/reconnected and TENOR is rerun. Do not point Codex at the source repository's `.agent`. Do not modify `~/.codex/config.toml` without explicit permission.

Do not copy a generated `.codex/config.toml` to another checkout. Run TENOR
INIT in the destination so the absolute root, binding id and configuration
digest are regenerated together.

`default_tools_approval_mode = "approve"` is deliberately scoped to this one
project-local TENOR server. Without it, non-interactive `codex exec` can list the
MCP tools but cancels their execution before the server receives the call. The
project must still be marked trusted by Codex before `.codex/config.toml` is
loaded; TENOR does not silently create that host trust decision.

## Required proof

Local `--list-tools` proves only the local server. Inside Codex, prove that the complete required MCP surface is visible, including guard, locks, claims, patch queue, audit, finish and `tenor_init_bridge`.

Then call the bridge from the actual Codex tool surface; it verifies the
project-local receipt, configuration hash and resolved root:

```text
tenor_init_bridge(
  agent_session_id="<TENOR session>",
  host_tool="codex",
  model_name="<active model>"
)
```

The bound server atomically consumes its one-time proof without printing a
bearer token. The public result is terminal `TENOR_INIT_READY` with
`HOST_PROCESS_ROOT_AND_SESSION` scope and preserves the internal
`bridge_verdict=TENOR_INIT_BRIDGE_OK` receipt.

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
TENOR_INIT_READY terminal bridge: NOT_TESTED
Complete MCP micro-write: NOT_TESTED
Direct-write bypass: OPEN
Final verdict: UNKNOWN
```

Follow `.agent/docs/DOCUMENTATION_SYNC_POLICY.md` when this verdict changes.
