---
name: init-tenor
description: >
  Initialiser ou reprendre une session TENOR/SCRIBE/GRAPHIFY dans le projet
  courant, puis prouver l'intégration MCP du host avant toute tâche.
---

# TENOR INIT V2.16 — AUTORITÉ UNIQUE

## Déclencheur canonique

Pour une session pilotée par un LLM hôte, le démarrage commence par :

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Ce déclencheur signifie : lire ce fichier local **avant** toute configuration globale OpenCode, Codex, Gemini, Cursor ou autre host, puis lire `.agent/rules/tenor-init-v2.json`.

L'ancien raccourci `[[.agent/skills/init-tenor/SKILL.md]]` peut être reconnu pour compatibilité historique, mais les nouvelles docs, prompts et templates doivent utiliser uniquement la forme canonique ci-dessus.

Pour CI, scripts ou opérateurs humains, la commande peut être appelée directement :

```bash
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown>
```

Sous Windows :

```powershell
python .agent/workflow/scribe/scribe tenor-init --type cli
```

`bootstrap` est une primitive interne/legacy. Il ne constitue plus l'entrée publique d'installation, de relocation ou de reprise V2.16.

## Finalité

`.agent` est une couche d'exploitation portable pour agents LLM :

- **TENOR** impose l'ordre mécanique sûr ;
- **Graphify** compresse la structure, les dépendances, les communautés et le blast radius ;
- **SCRIBE** restitue les douleurs, décisions, erreurs, interdictions et patterns passés ;
- **runtime/MCP** coordonne plusieurs agents actifs dans la même codebase.

Le but est qu'un petit modèle discipliné bénéficie de réflexes durables sans lire toute la codebase ni oublier les cicatrices du projet.

## Invariants non négociables

1. Le manifest d'installation et le root courant décident de l'identité du projet avant SCRIBE.
1b. Sur `TENOR_INIT_SAME_PROJECT`, `bootstrap_project()` est strictement en lecture seule des fichiers suivis : l'installateur forcé n'est jamais appelé et aucun `AGENTS.md` / `.agent/rules/scribe.md` / `.graphifyignore` / `.agent/.gitignore` n'est réécrit ; la réparation du bundle reste explicite (`scribe install --force`). `NEW_INSTALLATION` / `RELOCATED_PROJECT` / `LEGACY_INSTALLATION` conservent l'installation du bundle.
2. `AGENT-MEMOIRE_PROJECT_STATUS.scribe` ne décide jamais si un projet est nouveau.
3. Une relocation purge uniquement l'état copié lié à l'ancien root et conserve la mémoire canonique de la destination.
4. `server_entry.py` ne purge ni n'initialise ; il retourne `TENOR_INIT_REQUIRED` tant que l'installation locale n'est pas finalisée.
5. Un fichier Graphify présent n'est pas une preuve : le graphe doit être parseable, non-stub, lié au root et au fingerprint courants.
6. Le schéma Graphify réel NetworkX utilise `nodes + links`; le format historique supporté utilise `nodes + edges`. Toute autre représentation doit être explicitement reconnue ou refusée.
7. Une requête SCRIBE ou Graphify doit modifier le plan ou produire une contradiction auditable.
8. Chaque terminal reçoit une session, un proof token et des leases distincts.
9. Aucune écriture produit directe : locks, claims, lease, patch queue, audit et clôture MCP sont obligatoires.
10. Une réponse prose « terminé » sans preuve terminale MCP n'est pas une fin.

# PHASE 1 — TENOR INIT LOCAL

Depuis le root qui contient `.agent/` :

```bash
.agent/workflow/scribe/scribe tenor-init --type cli
```

La commande doit émettre rapidement :

```text
TENOR_INIT_START
TENOR_INIT_STAGE ...
```

Classifications attendues :

```text
TENOR_INIT_NEW_INSTALLATION
TENOR_INIT_SAME_PROJECT
TENOR_INIT_RELOCATED_PROJECT
TENOR_INIT_LEGACY_INSTALLATION
TENOR_INIT_CORRUPT_INSTALLATION
```

Puis :

```text
SCRIBE_MEMORY_ADOPT
SCRIBE_MEMORY_CREATE
```

Le SCRIBE n'est traité qu'après la classification d'installation.

## Graphify non prêt

Si TENOR INIT retourne `Graphify: build_required`, exécuter uniquement l'action bornée affichée :

```bash
.agent/workflow/scribe/scribe graph --project-build --timeout 180
```

Puis relancer TENOR INIT. Ne jamais accepter un stub smoke comme graphe terrain et ne jamais déclencher silencieusement un build lourd non borné.

Pour une codebase très importante, le timeout peut être augmenté explicitement par l'opérateur, sans supprimer la borne.

## Échecs locaux

- `TENOR_INIT_ALREADY_RUNNING` : attendre le propriétaire vivant ; ne pas supprimer son lock.
- `TENOR_INIT_REQUIRED` : exécuter l'action indiquée.
- mémoire invalide/corrompue : ne pas l'écraser ; arrêter et réparer.
- Graphify stale/corrompu/non lié : reconstruire puis relancer.
- aucun verdict d'échec ne peut être transformé en succès par prose.

# PHASE 2 — SERVEUR MCP LOCAL

Après succès local :

```bash
python .agent/mcp/server_entry.py --list-tools
```

Cette commande prouve seulement :

```text
MCP_LOCAL_SERVER_READY
```

Elle ne prouve pas que l'interface du host expose les tools au modèle.

Tools minimaux :

```text
workflow_next
before_task
discipline_ping
scribe_query
graphify_query
pre_action_guard
resource_lock_claim
resource_lock_release
claim_resource
file_hash
propose_patch
apply_patch
delete_resource
workspace_audit
scribe_record
finish_task
tenor_init_bridge
portability_check
graphify_required_check
```

# PHASE 3 — ADAPTATEUR DU HOST

Détecter le host réel : OpenCode, Codex CLI, Claude Code, Cursor, Cline, VS Code/Copilot, Gemini CLI, Roo, Kilo, Windsurf ou unknown.

Lire la fiche correspondante sous `.agent/docs/hosts/`. Ne jamais appliquer la configuration d'un host à un autre ni inventer un fichier de configuration.

Règles :

- préférer une configuration workspace/project-local ;
- aucune configuration globale/utilisateur sans permission explicite ;
- aucun chemin absolu vers un ancien projet ;
- signaler tout redémarrage/reconnexion nécessaire ;
- Chrome/DevTools n'est ajouté que si le host ou la tâche le requiert.

# PHASE 4 — VISIBILITÉ HOST ET ROOT BINDING

Vérifier dans l'interface réelle du host que les tools MCP sont directement appelables par le modèle.

Si cette preuve manque :

```text
HOST_MCP_UNBOUND
Init status: LOCAL_INIT_READY_HOST_MCP_UNBOUND
```

Aucune tâche produit n'est autorisée.

Si les tools sont visibles, comparer une sentinelle stable calculée côté host et via MCP (`file_hash`) pour prouver que le MCP est lié au projet courant.

Mauvais root :

```text
INIT_BLOCKED_MCP_WRONG_ROOT
```

# PHASE 5 — BRIDGE DE SESSION

Chaque terminal utilise l'`Agent session` et le `Proof token` émis par TENOR INIT, puis appelle :

```text
tenor_init_bridge(
  agent_session_id="<Agent session>",
  host_tool="<host>",
  model_name="<modèle>",
  proof_token="<Proof token>"
)
```

Résultat attendu :

```text
TENOR_INIT_BRIDGE_OK
```

Six terminaux partagent runtime SQLite, SCRIBE, Graphify, claims, locks et patch queue, mais ne partagent jamais identité, proof ou lease.

# PHASE 6 — SUCCÈS TERMINAL

Le rapport doit distinguer :

```text
Installation/root classification
SCRIBE adopted/created
Graphify readiness verdict
MCP local server ready
MCP tools visible to host LLM
MCP root binding
Agent session bridged
Active agents/claims/locks
Next action
```

Verdict final autorisé uniquement si tout est prouvé :

```text
TENOR_INIT_READY
```

# TENOR TASK

Après `TENOR_INIT_READY` :

```text
TENOR TASK:: <objectif>
```

Ordre minimal d'une écriture :

```text
discipline_ping
workflow_next
before_task
targeted scribe_query
targeted graphify_query
pre_action_guard
resource_lock_claim
claim_resource
file_hash
propose_patch
apply_patch
workspace_audit
scribe_record ou skip causal justifié
release claim et lock
finish_task
workflow_next -> READY_FOR_NEXT_TASK
```

Si SCRIBE retrouve un SCAR, GHOST, `ne_pas_reproposer`, invariant ou décision pertinente, l'agent indique comment cette entrée modifie son plan. S'il n'existe aucun contexte pertinent, il le dit sans inventer.

# INTERDICTIONS

```text
- coder avant TENOR_INIT_READY
- confondre list-tools local et tools visibles au host
- utiliser bootstrap comme autorité V2.16 publique
- ignorer une relocation ou un manifest preparing
- accepter un Graphify stub/non lié/stale/inconnu
- lire massivement des fichiers quand Graphify suffit
- interroger SCRIBE puis ignorer le résultat
- écrire via shell/Edit/write_file/apply_patch natif hors MCP
- utiliser la lease, le proof ou le claim d'un autre agent
- supprimer le lock d'un propriétaire vivant
- déclarer terminé sans finish_task et READY_FOR_NEXT_TASK
```

# SYNCHRONISATION DOCUMENTAIRE

Toute évolution du protocole doit suivre `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`. Les surfaces canoniques, les générateurs et la description de PR doivent être mis à jour dans le même lot que le code.
