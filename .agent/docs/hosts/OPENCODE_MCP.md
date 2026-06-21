# OpenCode MCP

Recherche web: 2026-06-21.

## Source officielle

https://opencode.ai/docs/mcp-servers

## Fichier de config

`opencode.jsonc`

## Note OpenCode

Pour OpenCode, la strategie recommandee est projet-local si le workspace le
supporte. Cette regle ne s'applique pas automatiquement aux autres hosts.

Une configuration projet-locale peut eviter les chemins globaux figes vers un
ancien `.agent/mcp/server_entry.py`. Mais ce n'est qu'une strategie propre a
OpenCode. La regle universelle reste: le root MCP doit etre prouve par hash
sentinel cote host et cote MCP.

Ne pas ajouter de chemin absolu vers `agent-scribe-graphify` dans une config
globale OpenCode sans instruction explicite. Si une entree globale existe deja,
demander permission avant de la supprimer ou de la desactiver, et ne pas toucher
aux autres MCP comme `chrome-devtools`.

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

- Shell direct: verifier si OpenCode expose une commande shell/bash au modele.
- Edit direct: verifier si OpenCode expose un outil d'ecriture directe hors MCP.
- Desactivation: verifier si la configuration OpenCode permet de retirer shell/edit directs ou de limiter les permissions.
- Sandbox: verifier si OpenCode peut etre lance via `.agent/scripts/agent_sandbox.py`.

## Direct Tool Neutralization

Avant de classer ce host `SAFE`, verifier explicitement:

1. Les tools natifs write/edit/apply_patch sont desactives, refuses, ou soumis a permission ask stricte.
2. Le shell ne peut pas ecrire dans le projet, ou toute ecriture shell demande approbation.
3. Les redirections `>`, `>>`, `tee`, `sed -i`, `perl -pi`, `rm`, `mv`, `cp` et scripts qui ecrivent sont bloques, sandboxes, ou detectes.
4. Une detection dirty-write compare les fichiers modifies avant/apres la tache.
5. Si une modification apparait sans trace MCP attendue: `DIRECT_WRITE_BYPASS_DETECTED`, STOP, rapport utilisateur.

Si un seul point est inconnu, le verdict maximal est `ACCEPTABLE` ou `UNKNOWN`, pas `SAFE`.

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
