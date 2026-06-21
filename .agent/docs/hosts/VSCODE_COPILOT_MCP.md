# VS Code / Copilot MCP

Recherche web: 2026-06-21.

## Source officielle

Source officielle IDE MCP a verifier dans la documentation VS Code / GitHub Copilot correspondant a la version installee.

## Fichier de config

`.vscode/mcp.json`

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

- Shell direct: verifier si le chat/agent peut lancer le terminal VS Code ou une commande shell.
- Edit direct: verifier si le chat/agent peut appliquer des edits workspace hors MCP.
- Desactivation: verifier les settings workspace/user permettant de restreindre tools, terminal et edits.
- Sandbox: verifier si VS Code peut etre lance dans une isolation OS compatible avec `.agent/scripts/agent_sandbox.py`.

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
