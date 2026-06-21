# Gemini CLI MCP

Recherche web: non effectuee dans cette passe.

## Statut

A verifier selon la version installee.

## Regle universelle

- Ne pas supposer le format de config.
- Verifier la documentation officielle ou la config locale du host.
- Verifier MCP visible au LLM.
- Verifier root binding par hash sentinel.
- Si config MCP inconnue: `HOST_GUIDE_INCOMPLETE`.

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
