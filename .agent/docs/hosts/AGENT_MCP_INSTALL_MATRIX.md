# Agent MCP Install Matrix

Derniere recherche web: 2026-06-21.

Commande locale commune: `python3 .agent/mcp/server_entry.py`.

Root binding check obligatoire pour tous les hosts: MCP visible ne suffit pas; `MCP_BOUND_TO_CURRENT_PROJECT` doit etre prouve par hash sentinelle cote host et cote MCP.

| Host | Source officielle deja listee | Fichier de config | Statut terrain | Verdict |
|---|---|---|---|---|
| OpenCode | https://opencode.ai/docs/mcp-servers | `opencode.jsonc` | a retester | UNKNOWN |
| Codex CLI | https://developers.openai.com/codex/mcp | `.codex/config.toml` | teste dans le host courant | ACCEPTABLE |
| Claude Code | https://docs.anthropic.com/en/docs/claude-code/mcp | `.mcp.json` | a tester | UNKNOWN |
| VS Code / Copilot MCP | a verifier dans la doc IDE officielle | `.vscode/mcp.json` | a tester | UNKNOWN |
| Cline | https://docs.cline.bot/mcp/mcp-overview | `mcp.json` | a tester | UNKNOWN |
| Kilo Code | source officielle non confirmee dans cette passe | `kilo.jsonc` | a tester | UNKNOWN |
| Roo Code | https://docs.roocode.com/features/mcp/using-mcp-in-roo | `.roo/mcp.json` | a tester | UNKNOWN |
| Cursor | source officielle non confirmee dans cette passe | `.cursor/mcp.json` | a verifier | UNKNOWN |
| Windsurf | source officielle MCP specifique non confirmee pendant cette passe | a verifier | a verifier | UNKNOWN |
| CommandCode CLI | nom ambigu, source officielle a clarifier | a verifier | a verifier | UNKNOWN |

Validation minimale par host:

1. Brancher le serveur STDIO avec `python3 .agent/mcp/server_entry.py`.
2. Verifier que les tools critiques sont visibles: `workflow_next`, `before_task`, `scribe_query`, `graphify_query`, `propose_patch`, `apply_patch`, `delete_resource`, `finish_task`.
3. Verifier si shell/bash direct est expose.
4. Verifier si edit/write_file direct est expose.
5. Verifier si shell/edit peuvent etre desactives ou si `agent_sandbox.py` peut lancer le host avec projet read-only.
6. Classer le host: SAFE, ACCEPTABLE, UNSAFE ou UNKNOWN.
