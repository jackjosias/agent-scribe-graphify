---
name: init-tenor
description: >
  Initialiser ou reprendre une session TENOR/SCRIBE/GRAPHIFY dans le projet
  courant, puis prouver l'intégration MCP du host avant toute tâche.
---

# TENOR INIT V2.16 — AUTORITÉ UNIQUE

Le déclencheur :

```text
[[.agent/skills/init-tenor/SKILL.md]]
```

signifie : exécuter le protocole ci-dessous. Ce fichier est lu avant tout autre
fichier du projet, puis `.agent/rules/tenor-init-v2.json` est lu et appliqué.

## Finalité

`.agent` est une couche d'exploitation portable pour agents LLM :

- **TENOR** impose l'ordre mécanique sûr ;
- **Graphify** compresse le contexte structurel, le blast radius et les dépendances ;
- **SCRIBE** restitue les douleurs, décisions, erreurs, interdictions et patterns passés ;
- **runtime/MCP** coordonne plusieurs agents actifs dans la même codebase.

Le but est de réduire l'écart entre petits et grands modèles. Un petit LLM qui
respecte les verdicts doit pouvoir agir avec les réflexes d'un modèle plus fort,
sans lire toute la codebase ni oublier les cicatrices du projet.

## Invariants non négociables

1. L'existence de `AGENT-MEMOIRE_PROJECT_STATUS.scribe` ne décide jamais si le
   projet est nouveau. Le manifest d'installation et le root courant décident.
2. Une relocation purge uniquement l'état local lié à l'ancien root, jamais la
   mémoire canonique du projet cible.
3. `server_entry.py` ne purge et n'initialise rien. Il retourne
   `TENOR_INIT_REQUIRED` tant que l'installation n'est pas finalisée.
4. Un `GRAPH_REPORT.md` existant n'est pas une preuve. Le graphe doit être
   parseable, non-stub, lié au root courant et à l'empreinte actuelle du workspace.
5. Une requête SCRIBE ou Graphify n'est pas une checkbox : son résultat doit
   modifier le plan ou produire une contradiction explicitement auditée.
6. Chaque terminal obtient une session agent distincte. Le bootstrap commun est
   sérialisé ; `SAME_PROJECT` ne purge jamais les autres agents actifs.
7. Aucune écriture produit directe : claims, resource locks, leases, patch queue,
   audit et clôture MCP sont obligatoires.
8. Une réponse prose « terminé » sans preuve terminale MCP n'est pas une fin.

# PHASE 1 — RÉSOUDRE LE PROJET ET EXÉCUTER TENOR INIT LOCAL

Depuis le root qui contient `.agent/` :

```text
.agent/workflow/scribe/scribe tenor-init --type cli
```

Sous Windows, l'invocation Python directe est autorisée lorsque l'association
exécutable n'est pas disponible :

```text
python .agent/workflow/scribe/scribe tenor-init --type cli
```

La commande doit émettre rapidement :

```text
TENOR_INIT_START
TENOR_INIT_STAGE ...
```

Verdicts d'installation attendus :

```text
TENOR_INIT_NEW_INSTALLATION
TENOR_INIT_SAME_PROJECT
TENOR_INIT_RELOCATED_PROJECT
TENOR_INIT_LEGACY_INSTALLATION
TENOR_INIT_CORRUPT_INSTALLATION
```

Puis une action mémoire :

```text
SCRIBE_MEMORY_ADOPT
SCRIBE_MEMORY_CREATE
```

Le SCRIBE n'est traité qu'après classification de l'installation.

## Graphify non prêt

Si TENOR INIT retourne `Graphify: build_required`, exécuter uniquement l'action
bornée affichée :

```text
.agent/workflow/scribe/scribe graph --project-build --timeout 180
```

Puis relancer TENOR INIT. Ne pas lancer silencieusement un build non borné et ne
jamais accepter un stub smoke comme graphe terrain.

Pour un projet réellement vide, TENOR peut créer un placeholder lié au root ; il
devient stale dès que du code applicatif apparaît.

## Échec local

- `TENOR_INIT_ALREADY_RUNNING` : attendre le bootstrap commun ; ne pas supprimer
  le lock d'un propriétaire vivant.
- `TENOR_INIT_REQUIRED` : exécuter l'action indiquée.
- mémoire invalide/corrompue : ne pas l'écraser ; arrêter et réparer.
- Graphify stale/corrompu/non lié : reconstruire puis relancer.
- aucun verdict d'échec ne peut être transformé en `INIT_CONFORME`.

# PHASE 2 — VÉRIFIER LE SERVEUR MCP LOCAL

Uniquement après succès de la phase 1 :

```text
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
claim_resource
file_hash
propose_patch
apply_patch
delete_resource
workspace_audit
scribe_record
finish_task
tenor_init_bridge
```

# PHASE 3 — RÉSOUDRE L'ADAPTATEUR DU HOST

Détecter le host réel : OpenCode, Codex CLI, Claude Code, Cursor, Cline,
VS Code/Copilot, Gemini CLI, Roo, Kilo, Windsurf ou unknown.

Lire la fiche correspondante sous `.agent/docs/hosts/`. Ne jamais appliquer la
configuration d'un host à un autre et ne jamais inventer un nom de fichier de
configuration.

Règles d'écriture de configuration :

- préférer le workspace/project-local ;
- aucune configuration globale ou utilisateur sans permission explicite ;
- aucun chemin absolu vers un ancien projet ;
- signaler lorsqu'un redémarrage du host est nécessaire ;
- Chrome/DevTools n'est installé ou configuré que si le host/la tâche navigateur
  le requiert et avec permission pour toute portée globale.

# PHASE 4 — PROUVER LA VISIBILITÉ HOST ET LE ROOT BINDING

Vérifier dans l'interface réelle du host que les tools MCP sont directement
appelables par le modèle.

Si non :

```text
HOST_MCP_UNBOUND
Init status: LOCAL_INIT_READY_HOST_MCP_UNBOUND
```

Le projet local est préparé, mais aucune tâche produit n'est autorisée avant
configuration/reconnexion du host.

Si les tools sont visibles, comparer une sentinelle stable calculée côté host et
via MCP (`file_hash`) pour prouver que le MCP est lié au projet courant.

Mauvais root :

```text
INIT_BLOCKED_MCP_WRONG_ROOT
```

# PHASE 5 — ENREGISTRER LA SESSION AGENT

Chaque terminal utilise l'`Agent session` et le `Proof token` émis par TENOR INIT,
puis appelle :

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

Six terminaux dans le même projet donnent six sessions distinctes partageant :

- le runtime SQLite ;
- la mémoire SCRIBE canonique ;
- la carte Graphify ;
- les claims, resource locks et leases.

Ils ne doivent jamais partager un `agent_id`, un proof token ou une lease.

# PHASE 6 — RAPPORT TERMINAL

Une init conforme doit distinguer explicitement :

```text
Installation/root classification
SCRIBE adopted/created
Graphify readiness verdict
MCP local server ready
MCP tools visible to host LLM
MCP root binding
Agent session registered
Active agents/claims
Next action
```

Verdict final autorisé uniquement si tout est prouvé :

```text
TENOR_INIT_READY
```

# TENOR TASK

Après init conforme :

```text
TENOR TASK:: <objectif>
```

Ordre minimal :

```text
tenor_task_prompt
discipline_ping
workflow_next
before_task
targeted scribe_query
targeted graphify_query
resource_lock_claim / claim_resource
file_hash
propose_patch
apply_patch
workspace_audit
scribe_record / canonical memory decision
finish_task
workflow_next -> READY_FOR_NEXT_TASK
```

Si SCRIBE retrouve un SCAR, GHOST, `ne_pas_reproposer`, invariant ou décision
pertinente, l'agent doit indiquer comment cette entrée modifie son plan. Si aucun
contexte pertinent n'existe, il le dit sans inventer.

# INTERDICTIONS

```text
- coder avant TENOR_INIT_READY
- confondre list-tools local et tools visibles au host
- ignorer une relocation ou un manifest incomplet
- accepter un Graphify stub/non lié/stale
- lire massivement des fichiers quand Graphify peut produire le sous-contexte
- interroger SCRIBE puis ignorer le résultat
- écrire via shell/Edit/apply_patch natif hors MCP
- utiliser la lease ou le claim d'un autre agent
- supprimer un lock d'init appartenant à un processus vivant
- déclarer terminé sans finish_task et READY_FOR_NEXT_TASK
```
