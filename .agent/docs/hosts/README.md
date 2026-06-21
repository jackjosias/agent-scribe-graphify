# Host MCP Installation Guides

Derniere recherche web: 2026-06-21.

Ce dossier regroupe les fiches d'installation MCP par host, IDE et extension. Objectif: documenter comment brancher le serveur MCP local `.agent` et comment auditer si le host conserve des chemins d'ecriture directs.

Commande MCP `.agent` standard pour les hosts STDIO:

```bash
python3 .agent/mcp/server_entry.py
```

Tools MCP critiques a valider dans chaque host:

- `workflow_next`
- `before_task`
- `scribe_query`
- `graphify_query`
- `propose_patch`
- `apply_patch`
- `delete_resource`
- `finish_task`

Fiches:

- `AGENT_MCP_INSTALL_MATRIX.md`
- `OPENAI_CODEX_MCP.md`
- `OPENCODE_MCP.md`
- `CLAUDE_CODE_MCP.md`
- `VSCODE_COPILOT_MCP.md`
- `CLINE_MCP.md`
- `KCODE_MCP.md`
- `ROO_CODE_MCP.md`
- `WINDSURF_MCP.md`
- `COMMAND_CODE_CLI.md`
- `CURSOR_MCP.md`

Verdicts:

- `SAFE`: MCP `.agent` visible et chemins directs bloques ou sandbox stricte active.
- `ACCEPTABLE`: MCP `.agent` visible avec chemins directs controles humainement.
- `UNSAFE`: chemins directs libres ou MCP `.agent` invisible.
- `UNKNOWN`: non teste ou information insuffisante.

Regle importante: MCP visible seul ne suffit pas. Si le host expose encore shell/edit direct hors sandbox, `.agent` reste un workflow gate, pas une barriere OS complete.
