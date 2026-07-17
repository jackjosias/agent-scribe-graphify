# OpenCode MCP — V2.16 Terrain Guide

## Official source checked

Last verified against the official OpenCode MCP documentation: 2026-07-14.

OpenCode supports local MCP servers under the `mcp` object in `opencode.jsonc`. A local server uses:

- `type: "local"`;
- `command` as an argument array;
- optional `cwd`, with relative paths resolved from the workspace;
- optional `environment`;
- optional `enabled`;
- optional tool-fetch `timeout`.

## Canonical TENOR entry

Start the host conversation with:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

The project-local skill must be read before global OpenCode instructions.

The local mechanical initialization remains:

```bash
.agent/workflow/scribe/scribe tenor-init --type cli --host opencode
```

Invariant terrain V2.16.1 : sur `TENOR_INIT_SAME_PROJECT`, l'init de session est strictement en lecture seule des fichiers suivis ; l'installateur forcé n'est jamais appelé (voir `.agent/docs/TENOR_INIT_SINGLE_AUTHORITY.md`).

## Preferred project-local configuration

Create or update `opencode.jsonc` at the project root without removing unrelated MCP servers:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "agent-scribe-graphify": {
      "type": "local",
      "command": ["python3", ".agent/mcp/server_entry.py"],
      "cwd": ".",
      "environment": {
        "AGENT_MCP_HOST": "opencode",
        "AGENT_MCP_BINDING_ID": "<generated-by-TENOR>",
        "AGENT_SCRIBE_GRAPHIFY_ROOT": "."
      },
      "enabled": true,
      "timeout": 20000
    }
  },
  "permission": {
    "edit": "deny",
    "bash": {
      "*": "deny",
      ".agent/workflow/scribe/scribe tenor-init --type cli --host opencode": "allow",
      "python .agent/workflow/scribe/scribe tenor-init --type cli --host opencode": "allow",
      "python3 .agent/workflow/scribe/scribe tenor-init --type cli --host opencode": "allow",
      "py -3 .agent/workflow/scribe/scribe tenor-init --type cli --host opencode": "allow"
    }
  }
}
```

Windows example:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "agent-scribe-graphify": {
      "type": "local",
      "command": ["python", ".agent/mcp/server_entry.py"],
      "cwd": ".",
      "environment": {
        "AGENT_MCP_HOST": "opencode",
        "AGENT_MCP_BINDING_ID": "<generated-by-TENOR>",
        "AGENT_SCRIBE_GRAPHIFY_ROOT": "."
      },
      "enabled": true,
      "timeout": 20000
    }
  },
  "permission": {
    "edit": "deny",
    "bash": {
      "*": "deny",
      ".agent/workflow/scribe/scribe tenor-init --type cli --host opencode": "allow",
      "python .agent/workflow/scribe/scribe tenor-init --type cli --host opencode": "allow",
      "python3 .agent/workflow/scribe/scribe tenor-init --type cli --host opencode": "allow",
      "py -3 .agent/workflow/scribe/scribe tenor-init --type cli --host opencode": "allow"
    }
  }
}
```

Do not handcraft `AGENT_MCP_BINDING_ID`: TENOR creates it and commits `.agent/state/install/host-binding.json` with the current config hash. The examples show the schema only. `cwd: "."` binds the launched MCP process to the workspace opened by OpenCode instead of an old absolute checkout.

Do not add an absolute path to the source repository `agent-scribe-graphify`. The server must be the copied project-local `.agent/mcp/server_entry.py`.

Do not edit global/user OpenCode config without explicit permission. If a global entry already exists, inspect it and ask before disabling or removing it. Never remove unrelated servers such as Chrome DevTools.

`ask` n'est pas une barrière suffisante en mode OpenCode `--auto`, où les
demandes sont approuvées automatiquement. Le profil autonome V2.16 refuse donc
le tool natif `edit` et refuse par défaut toute commande `bash`. Seules les
quatre formes exactes, sans suffixe shell, de TENOR INIT ci-dessus restent
autorisées afin qu'une nouvelle session puisse obtenir sa preuve et son identité
stable sur Linux, macOS ou Windows. Les mutations passent par un unique
`tenor_apply_changeset` atomique multi-fichier ; la reconstruction
structurelle passe par `graphify_project_build`. Les validateurs sont fournis
comme argv bornés au changeset et exécutés sans shell par TENOR.

## Reconnect requirement

When TENOR creates or changes `opencode.jsonc`, it returns `HOST_RECONNECT_REQUIRED` and does not issue a session proof. Restart or reconnect OpenCode so it reloads MCP configuration, then rerun TENOR INIT in a new host session.

## Local-only check

Outside OpenCode:

```bash
python3 .agent/mcp/server_entry.py --list-tools
```

This proves only local server readiness. It does not prove OpenCode exposes the tools to the model.

## OpenCode visibility proof

Inside OpenCode, the LLM must be able to call at least:

```text
file_hash
tenor_init_bridge
portability_check
graphify_required_check
graphify_project_build
tenor_task_start
tenor_apply_changeset
tenor_activity
tenor_task_control
```

OpenCode registers MCP tools alongside built-in tools. The terrain proof must come from the actual OpenCode tool interface/call trace, not from local CLI output.

If visibility is not proven:

```text
HOST_MCP_UNBOUND
LOCAL_INIT_READY_HOST_MCP_UNBOUND
```

## Root-binding proof

1. Create or select a stable sentinel inside the current project.
2. Calculate its hash from the host-visible workspace.
3. Call MCP `file_hash` for the exact relative path.
4. Compare the hashes and resolved root.

Mismatch:

```text
INIT_BLOCKED_MCP_WRONG_ROOT
```

Do not continue to product work after a mismatch.

## Session bridge

After tool visibility and root binding are proven, call through the MCP tool surface:

```text
tenor_init_bridge(
  agent_session_id="<TENOR Agent session>",
  host_tool="opencode",
  model_name="<active model>"
)
```

Required verdict:

```text
TENOR_INIT_BRIDGE_OK
```

The server consumes the matching proof atomically; the full bearer token is never printed or persisted. A successful bridge has scope `MCP_BRIDGE_ONLY`. Only the actual OpenCode host, after its independent root proof, may report:

```text
TENOR_INIT_READY
```

## Complete atomic changeset proof

Use two harmless test files in a dedicated validation workspace:

```text
tenor_task_start(objective, intent="write", resources=[file_a, file_b], scope=".")
tenor_apply_changeset(task_id, changes=[file_a, file_b], validators=[...])
  -> TENOR_CHANGESET_COMMITTED_TASK_FINISHED
```

The proof must also execute one validator failure and confirm both files are
restored byte-for-byte. Do not use OpenCode's built-in edit/write tool.

## Direct-write bypass test

Audit OpenCode built-in mutation paths and permissions:

- shell/bash;
- write/edit/apply-patch;
- `>`, `>>`, `tee`, `sed -i`, `rm`, `mv`, `cp`;
- any plugin or custom tool writing directly to the project.

The test must either be denied/approval-gated or detected as:

```text
DIRECT_WRITE_BYPASS_DETECTED
```

If direct mutation remains freely available, the maximum verdict is `ACCEPTABLE`, not `SAFE`.

## Terrain verdict — still open

```text
Local TENOR INIT: PROVED
Real Graphify binding: PROVED
Local MCP list-tools: PROVED
OpenCode config on final validation workspace: NOT_TESTED
MCP tools visible in OpenCode LLM: UNKNOWN
Root binding: UNKNOWN
TENOR_INIT_BRIDGE_OK: NOT_TESTED
Complete MCP micro-write: NOT_TESTED
Direct write bypass: NOT_TESTED
Final verdict: UNKNOWN
```

Do not edit this section to `PASS` without preserving the actual OpenCode evidence.
