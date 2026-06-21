# OpenAI Codex MCP

Recherche web: 2026-06-21.

## Source officielle

https://developers.openai.com/codex/mcp

## Fichier de config

`.codex/config.toml`

## Commande `.agent`

```bash
python3 .agent/mcp/server_entry.py
```

## Validation des tools

Verifier que le host expose au minimum:

- `workflow_next`
- `before_task`
- `scribe_query`
- `graphify_query`
- `propose_patch`
- `apply_patch`
- `delete_resource`
- `finish_task`

Commande locale de controle hors host:

```bash
python3 .agent/mcp/server_entry.py --list-tools
```

## Permissions a verifier

- Shell direct: verifier si `bash`, `sh`, terminal integre ou commande equivalente sont disponibles.
- Edit direct: verifier si un outil `apply_patch`, `write_file`, edition IDE ou modification directe du workspace est disponible hors MCP.
- Desactivation: verifier si Codex CLI permet de desactiver shell/edit directs pour cette session, ou de forcer un usage MCP-only.
- Sandbox: verifier si le host peut etre lance via `.agent/scripts/agent_sandbox.py` avec projet read-only.

## Verdict terrain

Host audite: Codex CLI session courante.

```text
MCP visible: YES
MCP tools visibles: workflow_next, before_task, scribe_query, graphify_query, propose_patch, apply_patch, delete_resource, finish_task
Shell direct: YES
Edit/write_file direct: YES
Desactivation shell/edit possible: UNKNOWN
Sandbox agent_sandbox.py possible: UNKNOWN
Direct FS test: OPEN
Verdict: ACCEPTABLE
```

Raison: MCP `.agent` est visible, mais le host courant conserve shell direct et edition directe. `.agent` protege le workflow MCP, pas le filesystem direct hors sandbox.
