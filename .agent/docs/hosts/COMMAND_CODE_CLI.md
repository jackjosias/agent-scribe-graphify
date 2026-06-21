# CommandCode CLI

Recherche web: 2026-06-21.

## Source officielle

Nom ambigu dans cette passe. A clarifier: l'utilisateur peut vouloir dire Claude Code, Codex CLI, ou un autre outil nomme CommandCode. Verifier le nom exact, le depot officiel et la documentation MCP avant installation.

## Fichier de config

A verifier apres clarification du host exact.

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

- Shell direct: verifier si le host expose shell/bash.
- Edit direct: verifier si le host expose write_file/apply_patch/edition workspace hors MCP.
- Desactivation: verifier si shell/edit directs peuvent etre desactives.
- Sandbox: verifier si le host peut etre lance via `.agent/scripts/agent_sandbox.py`.

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
