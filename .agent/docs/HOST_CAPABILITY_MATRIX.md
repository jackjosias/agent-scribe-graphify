# Host Capability Matrix

Le modèle n'a pas MCP par lui-même. Le host choisit quels tools sont visibles, quels accès filesystem existent, et si les écritures directes hors MCP sont possibles.

Cette matrice sert à distinguer le niveau d'enforcement réel par host. Elle doit être remplie par test pratique, pas par promesse de prompt.

| Host | MCP tools visibles | Shell direct | Edit direct | Peut désactiver shell/edit | Sandbox possible | Verdict |
|---|---:|---:|---:|---:|---:|---|
| Codex CLI | À vérifier | À vérifier | À vérifier | À vérifier | Oui via agent_sandbox.py si compatible | UNKNOWN |
| OpenCode | À vérifier | À vérifier | À vérifier | À vérifier | Oui via agent_sandbox.py si compatible | UNKNOWN |
| Claude Code | À vérifier | À vérifier | À vérifier | À vérifier | Oui via agent_sandbox.py si compatible | UNKNOWN |
| Autre | À vérifier | À vérifier | À vérifier | À vérifier | À vérifier | UNKNOWN |

## Verdicts

- `SAFE` : MCP visible + shell/edit direct désactivé ou sandbox strict actif.
- `ACCEPTABLE` : MCP visible + shell/edit direct humainement contrôlé mais pas bloqué OS.
- `UNSAFE` : shell/edit direct libre et host non sandboxé.
- `UNKNOWN` : non testé.

## Lecture opérationnelle

Si un host expose MCP mais conserve un shell ou un outil d'édition direct sans sandbox OS, les gates MCP protègent seulement les appels qui passent par MCP. Le host peut encore écrire par filesystem direct.
