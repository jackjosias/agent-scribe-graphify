# Host MCP Installation Guides

Derniere recherche web: 2026-06-21.

Ce dossier regroupe les fiches d'installation MCP par host, IDE et extension. Objectif: documenter comment brancher le serveur MCP local `.agent` et comment auditer si le host conserve des chemins d'ecriture directs.

Fiches prevues:

- `AGENT_MCP_INSTALL_MATRIX.md`
- `OPENCODE_MCP.md`
- `CODEX_CLI_MCP.md`
- `CLAUDE_CODE_MCP.md`
- `VSCODE_COPILOT_MCP.md`
- `CURSOR_MCP.md`
- `CLINE_MCP.md`
- `KILO_CODE_MCP.md`
- `ROO_CODE_MCP.md`
- `WINDSURF_MCP.md`
- `COMMAND_CODE_CLI.md`

Commande MCP `.agent` standard pour les hosts STDIO:

```bash
python3 .agent/mcp/server_entry.py
```

Verdicts:

- `SAFE`: MCP `.agent` visible et chemins directs bloques ou sandbox stricte active.
- `ACCEPTABLE`: MCP `.agent` visible avec chemins directs controles humainement.
- `UNSAFE`: chemins directs libres ou MCP `.agent` invisible.
- `UNKNOWN`: non teste.
