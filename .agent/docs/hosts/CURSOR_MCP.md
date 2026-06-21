# Cursor MCP

Recherche web: 2026-06-21.

## Source officielle

Source officielle non confirmee dans cette passe. A verifier dans la documentation Cursor MCP correspondant a la version installee.

## Fichier de config

`.cursor/mcp.json`

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

- Shell direct: verifier si Cursor Agent expose terminal/commande shell.
- Edit direct: verifier si Cursor Agent peut modifier les fichiers hors MCP.
- Desactivation: verifier si shell/edit directs peuvent etre retires ou soumis a approbation stricte.
- Sandbox: verifier si Cursor peut etre lance via `.agent/scripts/agent_sandbox.py` ou isolation OS equivalente.

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
