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
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown> --host <host-id|auto>
```

Sous Windows :

```powershell
py -3 .agent/workflow/scribe/scribe tenor-init --type cli --host <host-id|auto>
```

Dans une session hôte connue, `<host-id>` n'est jamais un placeholder à
deviner : utiliser immédiatement l'identité exacte du host. Dans OpenCode, la
commande est donc strictement :

```bash
.agent/workflow/scribe/scribe tenor-init --type cli --host opencode
```

`--host auto` est réservé à un opérateur réellement incapable d'identifier le
host ; il ne doit jamais être essayé puis corrigé par un petit modèle déjà
exécuté dans OpenCode, Codex ou Claude Code.

Sous OpenCode autonome, le profil project-local refuse tout `edit` natif et
toute commande shell sauf les formes exactes de cette commande TENOR INIT. Ne
lui ajoute ni redirection, ni pipe, ni `&&`, ni `;` : un suffixe ferait refuser
la commande. Les reconstructions Graphify passent ensuite par le tool MCP
`graphify_project_build`, jamais par un shell global.

`bootstrap` est une primitive interne/legacy. Il ne constitue plus l'entrée publique d'installation, de relocation ou de reprise V2.16.

## Finalité

`.agent` est une couche d'exploitation portable pour agents LLM :

- **TENOR** impose l'ordre mécanique sûr ;
- **Graphify** compresse la structure, les dépendances, les communautés et le blast radius ;
- **SCRIBE** restitue les douleurs, décisions, erreurs, interdictions et patterns passés ;
- **runtime/MCP** coordonne plusieurs agents actifs dans la même codebase.

Le but est qu'un petit modèle discipliné bénéficie de réflexes durables sans lire toute la codebase ni oublier les cicatrices du projet.

La copie brute et complète de `.agent/` dans n'importe quel projet compatible Linux, macOS ou Windows est un chemin d'installation obligatoire. Au premier TENOR INIT, le root courant, le manifest copié, la mémoire SCRIBE de destination et le fingerprint Graphify décident séparément s'il faut adopter, créer, purger l'ancien runtime ou reconstruire le graphe. Aucun outil de synchronisation externe n'est requis pour rendre cette copie portable.

## Invariants non négociables

1. Le manifest d'installation et le root courant décident de l'identité du projet avant SCRIBE.
1b. Sur `TENOR_INIT_SAME_PROJECT`, `bootstrap_project()` ne répare jamais le bundle : aucun `AGENTS.md` / `.agent/rules/scribe.md` / `.graphifyignore` / `.agent/.gitignore` n'est réécrit. TENOR peut uniquement créer ou mettre à jour l'entrée MCP project-local vérifiée du host détecté et son reçu `.agent/state/install/host-binding.json`; la réparation du bundle reste explicite (`scribe install --force`).
2. `AGENT-MEMOIRE_PROJECT_STATUS.scribe` ne décide jamais si un projet est nouveau.
3. Une relocation purge uniquement l'état copié lié à l'ancien root et conserve la mémoire canonique de la destination.
4. `server_entry.py` ne purge ni n'initialise ; il retourne `TENOR_INIT_REQUIRED` tant que l'installation locale n'est pas finalisée.
5. Un fichier Graphify présent n'est pas une preuve : le graphe doit être parseable, non-stub, lié au root et au fingerprint courants.
6. Le schéma Graphify réel NetworkX utilise `nodes + links`; le format historique supporté utilise `nodes + edges`. Toute autre représentation doit être explicitement reconnue ou refusée.
7. Une requête SCRIBE ou Graphify doit modifier le plan ou produire une contradiction auditable.
8. Chaque terminal reçoit une session, une preuve serveur one-shot et des leases distincts. Le bearer token complet n'est ni imprimé ni persisté.
9. Aucune écriture produit directe : locks, claims, lease, patch queue, audit et clôture MCP sont obligatoires.
10. Une réponse prose « terminé » sans preuve terminale MCP n'est pas une fin.

# PHASE 1 — TENOR INIT LOCAL

Depuis le root qui contient `.agent/` :

```bash
.agent/workflow/scribe/scribe tenor-init --type cli --host <host-id|auto>
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

Une réussite locale se termine par le contrat machine suivant :

```text
TENOR_INIT_TERMINAL=false
TENOR_INIT_NEXT_TOOL=tenor_init_bridge
TENOR_INIT_AGENT_SESSION=<id>
TENOR_INIT_RESPONSE_POLICY=CONTINUE_WITHOUT_USER_RESPONSE
```

Ce contrat interdit une réponse utilisateur intermédiaire : le modèle appelle
immédiatement le tool MCP indiqué dans le même tour. Les requêtes SCRIBE et
Graphify ciblées sont différées vers `tenor_task_start`; TENOR INIT ne lance
plus quatre recherches RAG générales qui ralentissaient l'entrée de session.

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
python3 .agent/mcp/server_entry.py --list-tools
```

Cette commande prouve seulement :

```text
MCP_LOCAL_SERVER_READY
```

Elle ne prouve pas que l'interface du host expose les tools au modèle.

Tools minimaux :

```text
file_hash
tenor_init_bridge
portability_check
graphify_required_check
graphify_project_build
tenor_task_start
tenor_apply_changeset
tenor_activity
tenor_task_control
```

Les cinq premiers outils servent à l'initialisation/bootstrap. Les quatre
outils `tenor_*` restants constituent toute l'API normale annoncée au LLM
hôte. Les primitives historiques fines restent internes au runtime pour
compatibilité et ne doivent pas être orchestrées manuellement par le modèle.

# PHASE 3 — ADAPTATEUR DU HOST

TENOR détecte le host avec l'ordre d'autorité suivant : `--host` explicite, environnement du host, puis marqueur project-local non ambigu. Utiliser l'identifiant réel (`--host opencode`, `--host claude-code`, `--host codex-cli`) dès qu'il est connu ; `auto` échoue fermé si aucun host ou plusieurs hosts sont détectés.

La configuration automatique project-local est vérifiée uniquement pour OpenCode (`opencode.jsonc`), Claude Code (`.mcp.json`) et Codex (`.codex/config.toml`). Pour Cursor, Cline, VS Code/Copilot, Gemini CLI, Roo, Kilo, Windsurf ou un host inconnu, TENOR s'arrête sur la fiche exacte au lieu d'inventer une configuration.

Lire la fiche correspondante sous `.agent/docs/hosts/`. Ne jamais appliquer la configuration d'un host à un autre ni inventer un fichier de configuration.

Règles :

- préférer une configuration workspace/project-local ;
- aucune configuration globale/utilisateur sans permission explicite ;
- aucun chemin absolu vers un ancien projet ;
- signaler tout redémarrage/reconnexion nécessaire ;
- Chrome/DevTools n'est ajouté que si le host ou la tâche le requiert.

Après une création ou modification de configuration, TENOR retourne `HOST_RECONNECT_REQUIRED` sans émettre de preuve de session. Redémarrer/reconnecter le host, puis relancer TENOR INIT depuis l'interface réelle du host.

# PHASE 4 — VISIBILITÉ HOST ET ROOT BINDING

Vérifier dans l'interface réelle du host que les tools MCP sont directement appelables par le modèle. Un `--list-tools` ou un appel JSON-RPC lancé manuellement depuis un shell ne constitue jamais cette preuve.

Si cette preuve manque :

```text
HOST_MCP_UNBOUND
Init status: LOCAL_INIT_READY_HOST_MCP_UNBOUND
```

Aucune tâche produit n'est autorisée.

L'appel réel à `tenor_init_bridge` constitue la preuve de visibilité. Il
vérifie ensuite mécaniquement que le processus MCP porte l'identité du host,
que le reçu project-local correspond au root résolu, et que le hash de la
configuration chargée correspond au reçu. Cette preuve ne dépend d'aucun
fichier propre à un framework (`package.json`, Cargo, Maven, CMake, etc.) et
reste donc portable dans une codebase C++, Java, Rust ou autre.

Mauvais root :

```text
INIT_BLOCKED_MCP_WRONG_ROOT
```

# PHASE 5 — BRIDGE DE SESSION

Depuis le processus MCP réellement lancé par la configuration du host, chaque terminal utilise son `Agent session` puis appelle :

```text
tenor_init_bridge(
  agent_session_id="<Agent session>",
  host_tool="<host>",
  model_name="<modèle>"
)
```

Résultat attendu :

```text
TENOR_INIT_READY
```

Le résultat conserve `bridge_verdict=TENOR_INIT_BRIDGE_OK` comme preuve
interne, puis retourne le verdict terminal unique `TENOR_INIT_READY` avec la
preuve de root, l'identité liée au processus, `terminal=true` et
`next_action=READY_FOR_NEXT_TASK`. Le serveur consomme atomiquement la preuve
one-shot correspondante sans exposer de bearer token. Une compatibilité
explicite avec l'ancien paramètre `proof_token` reste acceptée, mais ce n'est
plus le chemin canonique.

Six terminaux partagent runtime SQLite, SCRIBE, Graphify et l'autorité de
transaction, mais chacun conserve une identité liée à son propre processus
MCP. Un agent ne peut ni retirer ni contrôler la tâche d'un autre.

# PHASE 6 — SUCCÈS TERMINAL

Le rapport doit distinguer :

```text
Installation/root classification
SCRIBE adopted/created
Graphify readiness verdict
MCP local server ready
MCP tools visible to host LLM
MCP root binding
Agent session bridged (scope HOST_PROCESS_ROOT_AND_SESSION)
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

Le contrat machine n'accepte que `intent=read`, `intent=write` ou
`intent=delete`. La demande humaine complète reste dans `request`; elle ne doit
jamais être recopiée dans `intent`. Après le bridge, l'identité est dérivée du
processus MCP : aucun appel de tâche n'accepte un `agent_id` ou un token fourni
par le modèle. Un agent conserve un seul `task_id` actif jusqu'au verdict
terminal.

Ordre public d'une écriture :

```text
tenor_task_start(objective, intent, resources, scope)
  -> targeted SCRIBE + Graphify, internes
tenor_apply_changeset(task_id, changes[], validators[])
  -> preflight de tous les chemins/hashes/locks avant la première écriture
  -> commit de tous les fichiers ou rollback de tous les fichiers
  -> validation, record SCRIBE runtime et clôture terminale
```

Le changeset accepte jusqu'à 64 fichiers, refuse traversal et symlinks, exige
un hash de base pour chaque opération, acquiert les locks dans un ordre stable,
exécute les validateurs sous forme d'argv sans shell et supporte les retries
idempotents via `request_id`. Toute suppression exige la confirmation exacte
du chemin. `tenor_activity` fournit l'état consolidé ; `tenor_task_control`
gère pause/reprise/annulation et la clôture d'une tâche de lecture.

Si Graphify doit être reconstruit après la liaison du host, appeler
`graphify_project_build(timeout_seconds=180)`. Avant liaison, utiliser seulement
`.agent/workflow/scribe/scribe graph --project-build --timeout 180`. Ne jamais
exécuter `graphify update .` ni créer `graphify-out/` à la racine.

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
- créer un agent ou une tâche de remplacement pour contourner un verdict fail-closed
- utiliser un intent descriptif au lieu de read|write|delete
- lancer graphify update . ou graphify watch dans le projet portable
- fournir une identité ou un token de tâche depuis le LLM
- retirer, remplacer ou contrôler un autre agent
- présenter un shell JSON-RPC comme preuve de visibilité host
- supprimer le lock d'un propriétaire vivant
- déclarer terminé sans verdict terminal machine et preuve des validateurs
```

# SYNCHRONISATION DOCUMENTAIRE

Toute évolution du protocole doit suivre `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`. Les surfaces canoniques, les générateurs et la description de PR doivent être mis à jour dans le même lot que le code.
