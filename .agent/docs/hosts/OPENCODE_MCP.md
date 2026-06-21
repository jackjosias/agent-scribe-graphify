# OpenCode MCP

Recherche web: 2026-06-21.

## Source officielle

https://opencode.ai/docs/mcp-servers

## Fichier de config

`opencode.jsonc`

## Note OpenCode

Pour OpenCode, une configuration projet-locale peut eviter les chemins globaux figes vers un ancien `.agent/mcp/server_entry.py`. Mais ce n'est qu'une strategie propre a OpenCode. La regle universelle reste: le root MCP doit etre prouve par hash sentinel cote host et cote MCP.

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

- Shell direct: verifier si OpenCode expose une commande shell/bash au modele.
- Edit direct: verifier si OpenCode expose un outil d'ecriture directe hors MCP.
- Desactivation: verifier si la configuration OpenCode permet de retirer shell/edit directs ou de limiter les permissions.
- Sandbox: verifier si OpenCode peut etre lance via `.agent/scripts/agent_sandbox.py`.

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
