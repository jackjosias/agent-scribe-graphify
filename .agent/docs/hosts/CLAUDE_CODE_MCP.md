# Claude Code MCP

Recherche web: 2026-06-21.

## Source officielle

https://docs.anthropic.com/en/docs/claude-code/mcp

## Fichier de config

`.mcp.json`

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

- Shell direct: verifier si Claude Code expose bash/shell au modele.
- Edit direct: verifier si Claude Code expose edition directe du workspace hors MCP.
- Desactivation: verifier si les permissions Claude Code permettent de refuser shell/edit directs et de conserver le MCP `.agent`.
- Sandbox: verifier si Claude Code peut etre lance via `.agent/scripts/agent_sandbox.py`.

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
