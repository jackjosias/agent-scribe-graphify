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

- `UNSAFE`: MCP absent ou non visible, mauvais root, ou écritures directes libres.
- `ACCEPTABLE`: MCP visible + root bound + workflow MCP utilisé, mais shell/write/edit directs encore accessibles.
- `SAFE_CANDIDATE`: MCP visible + root bound, write/edit natifs désactivés ou en permission ask stricte, shell lecture autorisé mais écritures bloquées ou demandent approbation.
- `SAFE`: MCP visible + root bound, aucun chemin d’écriture projet hors MCP, ou sandbox vérifiée empêchant les écritures directes hors MCP.
- `UNKNOWN`: non teste ou information insuffisante.

## Direct Tool Neutralization

Tant que `Direct shell/write/edit: YES` existe sans permission stricte ni
sandbox, le verdict ne peut pas être `SAFE`. Le host reste `ACCEPTABLE` au
mieux: le protocole est correct, mais une voie de contournement existe encore.

Défense en couches:

1. Host permissions: désactiver ou mettre en ask/deny les tools natifs dangereux.
2. Project-local host config: porter les réglages du host dans le projet si supporté.
3. OS sandbox: lancer le host avec projet read-only hors MCP quand possible.
4. MCP workflow gate: écrire seulement via `propose_patch`, `apply_patch`, `delete_resource`.
5. Dirty-write detector: comparer `git status`, hashes et logs MCP avant/après tâche.
6. Policy: `DIRECT_WRITE_BYPASS_DETECTED` stoppe la tâche, liste les fichiers touchés et demande rollback ou validation explicite.

Regle importante: MCP visible seul ne suffit pas. Si le host expose encore shell/edit direct hors sandbox, `.agent` reste un workflow gate, pas une barriere OS complete.
