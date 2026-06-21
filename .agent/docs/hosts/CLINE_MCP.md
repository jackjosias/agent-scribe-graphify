# Cline MCP

Recherche web: 2026-06-21.

## Source officielle

https://docs.cline.bot/mcp/mcp-overview

## Fichier de config

`mcp.json`

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

- Shell direct: verifier si Cline expose terminal/commande shell.
- Edit direct: verifier si Cline peut modifier les fichiers hors MCP.
- Desactivation: verifier les options d'approbation et de desactivation des tools directs.
- Sandbox: verifier si l'IDE contenant Cline peut etre lance via `.agent/scripts/agent_sandbox.py` ou autre isolation OS.

## Verdict terrain

```text
MCP visible: UNKNOWN
MCP tools visibles: UNKNOWN
Shell direct: UNKNOWN
Edit/write_file direct: UNKNOWN
Desactivation shell/edit possible: UNKNOWN
Sandbox agent_sandbox.py possible: UNKNOWN
Direct FS test: NOT_TESTED
Verdict: UNKNOWN
```
