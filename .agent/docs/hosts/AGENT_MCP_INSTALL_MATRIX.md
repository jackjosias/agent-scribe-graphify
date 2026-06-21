# Agent MCP Install Matrix

Derniere recherche web: 2026-06-21.

Commande locale commune: `python3 .agent/mcp/server_entry.py`.

Root binding check obligatoire pour tous les hosts: MCP visible ne suffit pas; `MCP_BOUND_TO_CURRENT_PROJECT` doit etre prouve par hash sentinelle cote host et cote MCP.

Host guide obligatoire avant correction config: detecter le host, lire sa
fiche dans `.agent/docs/hosts/`, puis appliquer uniquement cette strategie. Ne
pas transposer la strategie OpenCode, Cursor, Codex CLI ou Gemini CLI vers un
autre host.

| Host | MCP visible possible | Root binding possible | Shell direct désactivable | Write/edit natif désactivable | Permission ask/deny possible | Sandbox possible | Config projet-local possible | Verdict cible possible | Fiche host |
|---|---|---|---|---|---|---|---|---|---|
| OpenCode | YES | YES | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | YES | ACCEPTABLE, SAFE_CANDIDATE si permissions/sandbox prouvées | `OPENCODE_MCP.md` |
| Codex CLI | YES | YES | UNKNOWN | UNKNOWN | YES via approvals partielles | UNKNOWN | UNKNOWN | ACCEPTABLE, SAFE_CANDIDATE si sandbox/deny prouvé | `OPENAI_CODEX_MCP.md` |
| Claude Code | YES | YES | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | YES | UNKNOWN puis ACCEPTABLE/SAFE_CANDIDATE selon audit | `CLAUDE_CODE_MCP.md` |
| Cursor | YES | YES | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | YES | UNKNOWN puis ACCEPTABLE/SAFE_CANDIDATE selon audit | `CURSOR_MCP.md` |
| Gemini CLI | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | `GEMINI_CLI_MCP.md` |
| VS Code / Copilot | YES | YES | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | YES | UNKNOWN puis ACCEPTABLE/SAFE_CANDIDATE selon audit | `VSCODE_COPILOT_MCP.md` |
| Cline | YES | YES | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | YES | UNKNOWN puis ACCEPTABLE/SAFE_CANDIDATE selon audit | `CLINE_MCP.md` |
| Roo Code | YES | YES | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | YES | UNKNOWN puis ACCEPTABLE/SAFE_CANDIDATE selon audit | `ROO_CODE_MCP.md` |
| Kilo Code | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | `KCODE_MCP.md` |
| Windsurf | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | `WINDSURF_MCP.md` |
| unknown | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | `README.md` |

Validation minimale par host:

1. Brancher le serveur STDIO avec `python3 .agent/mcp/server_entry.py`.
2. Verifier que les tools critiques sont visibles: `workflow_next`, `before_task`, `scribe_query`, `graphify_query`, `propose_patch`, `apply_patch`, `delete_resource`, `finish_task`.
3. Verifier si shell/bash direct est expose.
4. Verifier si edit/write_file direct est expose.
5. Verifier si shell/edit peuvent etre desactives ou si `agent_sandbox.py` peut lancer le host avec projet read-only.
6. Classer le host: UNSAFE, ACCEPTABLE, SAFE_CANDIDATE, SAFE ou UNKNOWN.
7. Si un fichier change sans trace MCP attendue, déclarer `DIRECT_WRITE_BYPASS_DETECTED` et stopper.
