# TENOR INIT V2.16 — Single Authority Rescue

## Statut

Cette branche restaure TENOR INIT comme unique autorité d'installation, de
relocation, de préparation Graphify et de reprise multi-agent.

Ce document distingue toujours :

- **implémenté** : présent dans le code de la branche ;
- **testé** : couvert par un test exécutable ;
- **prouvé terrain** : observé dans un host réel et une codebase réelle.

Aucune de ces catégories ne remplace les deux autres.

## Finalité : LLM Experience

`.agent` n'est pas une collection de scripts. C'est une couche d'exploitation
portable qui externalise des capacités cognitives et opérationnelles :

- **Graphify** compresse la structure, les dépendances, la centralité et le blast
  radius afin d'éviter la lecture massive de fichiers et la saturation du contexte ;
- **SCRIBE** conserve la causalité, les douleurs, décisions, régressions,
  interdictions, SCAR, GHOST et `ne_pas_reproposer` qui doivent influencer la
  tâche actuelle ;
- **TENOR** transforme le protocole en prochain geste mécanique et vérifiable ;
- **runtime/MCP** donne une conscience partagée aux agents actifs : identité,
  claims, resource locks, leases, patch queue et clôture.

Le but est qu'un petit LLM discipliné obtienne des réflexes proches d'un grand
modèle sans dépendre de sa mémoire conversationnelle ni de sa fenêtre de contexte.

## Autorité d'identité

L'identité du projet est décidée avant SCRIBE à partir de :

1. la racine réellement résolue ;
2. `.agent/state/install/agent-installation.json` ;
3. l'ancien root enregistré ;
4. l'empreinte actuelle des marqueurs du projet.

Classifications :

```text
TENOR_INIT_NEW_INSTALLATION
TENOR_INIT_SAME_PROJECT
TENOR_INIT_RELOCATED_PROJECT
TENOR_INIT_LEGACY_INSTALLATION
TENOR_INIT_CORRUPT_INSTALLATION
```

Le fichier `AGENT-MEMOIRE_PROJECT_STATUS.scribe` ne décide jamais si le projet
est nouveau. Il produit seulement, après classification :

```text
SCRIBE_MEMORY_ADOPT
SCRIBE_MEMORY_CREATE
```

## Transaction locale

L'installation utilise deux états explicites :

```text
preparing
ready
```

`server_entry.py` est non destructif. Il inspecte l'installation et retourne
l'exit code `78` avec `TENOR_INIT_REQUIRED` tant que le manifest n'est pas
finalisé. Il ne purge, ne migre et ne crée aucun runtime caché.

Ordre :

```text
RESOLVE
CLASSIFY
RESET_IF_REQUIRED
ADOPT_PROJECT
ADOPT_MEMORY
VERIFY_GRAPH
FINALIZE_INSTALLATION
VERIFY_LOCAL_MCP
CONFIGURE_AND_VERIFY_HOST
REGISTER_SESSION
READY
```

Une erreur avant `FINALIZE_INSTALLATION` laisse le manifest en `preparing` et le
serveur continue à refuser le démarrage.

## Relocation

Une relocation A vers B :

- purge uniquement `.agent/state/` copié depuis A ;
- conserve le moteur portable `.agent` ;
- conserve la mémoire canonique déjà présente dans B ;
- rejette les sessions, proofs, locks, outputs et bindings de A ;
- écrit le manifest de B ;
- reconstruit les états dérivés pour B.

Les chemins de purge sont validés, les symlinks externes sont refusés et les
échecs transitoires de suppression utilisent un backoff borné.

## Concurrence : six terminaux

Le bootstrap partagé est protégé par `.agent/.tenor-init.lock`.

Le lock contient :

- nonce propriétaire ;
- PID ;
- hostname ;
- root ;
- étape courante ;
- timestamps de création et heartbeat.

Un lock âgé n'est pas stale si son propriétaire local vit encore. Un waiter ne
peut supprimer que le nonce exact qu'il a observé ; une relecture ferme la course
TOCTOU entre libération et réacquisition.

Après bootstrap, chaque terminal enregistre une session indépendante. Les agents
partagent le runtime mais jamais :

- `agent_id` ;
- proof token ;
- action lease ;
- claim propriétaire ;
- resource lock propriétaire.

## Graphify readiness

Les outputs canoniques sont :

```text
.agent/state/outputs/graphify-out/graph.json
.agent/state/outputs/graphify-out/GRAPH_REPORT.md
.agent/state/outputs/graphify-out/graph.html
.agent/state/outputs/graphify-out/GRAPHIFY_READY.json
```

Un fichier présent n'est pas une preuve. Le validateur vérifie :

- JSON parseable avec listes `nodes` et `edges` ;
- rapport et HTML non vides ;
- manifest de readiness supporté ;
- root lié égal au root courant ;
- empreinte workspace courante égale à l'empreinte liée ;
- absence de marqueur smoke/placeholder interdit ;
- type de manifest autorisé ;
- graphe réel non vide pour un projet avec code.

Verdicts principaux :

```text
GRAPHIFY_READY
GRAPHIFY_EMPTY_PROJECT_READY
GRAPHIFY_TEST_FIXTURE_READY
GRAPHIFY_MISSING
GRAPHIFY_OUTPUTS_INCOMPLETE
GRAPHIFY_STUB_INVALID
GRAPHIFY_CORRUPT
GRAPHIFY_LEGACY_UNBOUND
GRAPHIFY_STALE_ROOT
GRAPHIFY_STALE_WORKSPACE
GRAPHIFY_FIXTURE_FORBIDDEN
GRAPHIFY_MANIFEST_INVALID
```

Une fixture smoke est marquée `smoke_fixture`, exige une autorisation de test
explicite et est refusée par TENOR INIT terrain même si la variable fuit.

Le build projet est séparé et borné :

```text
.agent/workflow/scribe/scribe graph --project-build --timeout 180
```

Il construit, migre vers l'output canonique, lie le manifest et revalide.
TENOR INIT ne lance plus silencieusement un build lourd.

## SCRIBE opérationnel

SCRIBE doit être interrogé de manière ciblée avant toute tâche significative.
Les résultats doivent influencer le plan :

- SCAR : ancienne blessure et test protecteur ;
- GHOST : approche rejetée ou dérive détectée ;
- `ne_pas_reproposer` : mémoire négative ;
- décision/invariant : contrainte actuelle ;
- dette : risque accepté mais actif.

Une requête exécutée puis ignorée n'est pas une exploitation de mémoire. Un
`scribe_record` runtime n'est pas automatiquement une mémoire canonique.

Le bridge Graphify/SCRIBE refuse désormais d'analyser la dérive structurelle sur
un graphe manquant, stub, stale ou lié à un autre root. Les écritures GHOST sont
atomiques et protégées par lock propriétaire.

## Host integration

L'ordre correct est :

1. TENOR INIT local ;
2. Graphify prêt ;
3. serveur MCP local listable ;
4. lecture du guide du host réel ;
5. configuration workspace-local lorsque supportée ;
6. redémarrage du host si nécessaire ;
7. preuve que les tools sont visibles dans l'interface LLM ;
8. preuve du root binding ;
9. `tenor_init_bridge` ;
10. `TENOR_INIT_READY`.

`server_entry.py --list-tools` ne prouve jamais la visibilité host. Avant preuve :

```text
HOST_MCP_UNBOUND
LOCAL_INIT_READY_HOST_MCP_UNBOUND
```

Aucune configuration globale/utilisateur ni installation Chrome/DevTools n'est
faite sans besoin réel et permission explicite.

## Retry et dégradation

Le launcher traite l'exit code `78` comme un verdict déterministe, jamais comme
une panne réseau à retry. Les erreurs de policy, import, JSON ou arguments
remontent immédiatement. Les retries exponentiels sont réservés aux erreurs
transitoires explicitement identifiées et restent bornés.

Si le binaire Graphify manque mais qu'un graphe valide, courant et lié existe, la
lecture structurelle peut continuer ; toute reconstruction reste indisponible et
est signalée. Si le graphe devient stale, les writes sont bloqués.

## Portabilité

Le noyau utilise :

- `pathlib.Path` ;
- listes d'arguments subprocess, `shell=False` ;
- timeouts Python ;
- fichiers atomiques `fsync + os.replace` ;
- locks `O_EXCL` ;
- séparateur `os.pathsep` ;
- aucun `/tmp`, `grep`, `sed`, `timeout GNU`, `flock` ou chmod obligatoire dans
  le chemin canonique.

Le workflow `.github/workflows/v216-portability.yml` exécute le noyau sur :

```text
ubuntu-latest
macos-latest
windows-latest
```

La validation Linux profonde reste séparée car elle couvre aussi les scénarios
intégration/red-team qui manipulent un runtime commun.

## Tests ajoutés

```text
test_installation_state.py
test_tenor_init_orchestrator.py
test_graphify_readiness.py
test_v216_cross_platform.py
test_scribe_bootstrap.py
test_host_adapter_autoguard.py
test_graphify_scribe_bridge.py
mcp_smoke.py
validation_suite.py
```

Ils couvrent notamment :

- manifest absent, preparing, ready et corrompu ;
- relocation et purge limitée ;
- mémoire cible préservée ;
- six initialisations concurrentes ;
- récupération prudente des locks stale ;
- graphes absent, incomplet, stub, corrompu, legacy, wrong-root et stale ;
- fixture smoke interdite sur terrain ;
- projet vide ;
- chemins espaces/Unicode et projet non-Git ;
- instructions host atomiques/idempotentes ;
- preflight local avant preuve host ;
- workflow MCP write et tripwire.

## Limites non masquées

Cette branche ne peut pas prouver en CI que chaque UI propriétaire expose les
MCP tools au modèle. Cette preuve exige un test terrain dans le host après sa
configuration et son éventuel redémarrage.

La branche ne doit pas être fusionnée tant que :

- la matrice Linux/macOS/Windows n'est pas verte ;
- la validation Linux profonde n'est pas verte ;
- les artefacts de tests ne laissent pas le checkout sale ;
- la diff complète n'a pas été auditée ;
- la PR reste marquée draft si une preuve manque.

## Critère terminal

Le seul succès global autorisé est :

```text
TENOR_INIT_READY
```

Il exige : installation prête, mémoire adoptée/créée, Graphify valide, serveur
local prêt, tools visibles au host, root binding prouvé et session bridgée.
