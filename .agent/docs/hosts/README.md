# Host MCP Installation Guides

Derniere recherche web: 2026-06-21.

Ce dossier regroupe les fiches d'installation MCP par host, IDE et extension. Objectif: documenter comment brancher le serveur MCP local `.agent` et comment auditer si le host conserve des chemins d'ecriture directs.

## Architecture multi-host

Ce dossier n'est pas une doc OpenCode. C'est un registre d'adaptateurs host. Le
protocole universel est:

1. detecter le host
2. lire la fiche host
3. verifier MCP visible
4. verifier root binding
5. appliquer uniquement la strategie de ce host

Ne jamais copier la strategie d'un host vers un autre. OpenCode, Cursor,
Gemini CLI, Codex CLI, Claude Code, Cline, Roo, Kilo et VS Code peuvent avoir
des formats de config differents.

Commande MCP `.agent` standard pour les hosts STDIO:

```bash
python3 .agent/mcp/server_entry.py
```

Note critique: un serveur MCP local listable avec `python3 .agent/mcp/server_entry.py --list-tools` ne signifie pas que les tools MCP sont visibles au LLM host. Il faut verifier separement que le host expose directement `workflow_next`, `before_task`, `scribe_query`, `graphify_query`, `propose_patch`, `apply_patch`, `delete_resource` et `finish_task` au modele.

Pour tous les hosts: MCP visible ne suffit pas. Le root MCP doit etre prouve identique au root projet courant via un fichier sentinelle hashe cote host et cote MCP. Si les hash divergent, le statut est `MCP_WRONG_ROOT` et l'init doit rester bloquee.

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
- `GEMINI_CLI_MCP.md`

Verdicts:

- `SAFE`: MCP `.agent` visible et chemins directs bloques ou sandbox stricte active.
- `ACCEPTABLE`: MCP `.agent` visible avec chemins directs controles humainement.
- `UNSAFE`: chemins directs libres ou MCP `.agent` invisible.
- `UNKNOWN`: non teste ou information insuffisante.

Regle importante: MCP visible seul ne suffit pas. Si le host expose encore shell/edit direct hors sandbox, `.agent` reste un workflow gate, pas une barriere OS complete.
