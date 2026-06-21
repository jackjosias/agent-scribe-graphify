# Windsurf MCP

Recherche web: 2026-06-21.

## Source officielle

La page officielle MCP specifique n'a pas ete trouvee pendant cette passe. Fiche a verifier manuellement dans Windsurf/Cascade avant installation.

## Fichier de config

A verifier.

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

- Shell direct: verifier si Windsurf/Cascade expose terminal/commande shell.
- Edit direct: verifier si Windsurf/Cascade peut modifier directement le workspace hors MCP.
- Desactivation: verifier si shell/edit directs peuvent etre retires ou soumis a approbation stricte.
- Sandbox: verifier si Windsurf peut etre lance via `.agent/scripts/agent_sandbox.py` ou isolation OS equivalente.

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
