---
trigger: always_on
---

# SCRIBE/TENOR — RÈGLE ALWAYS-ON V2.16

Ce fichier est la règle courte destinée aux LLM hôtes. Il ne remplace pas le skill d'initialisation ni le document d'autorité.

## Démarrage canonique

Déclencheur humain/LLM :

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Commande mécanique depuis la racine du projet :

```bash
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown> --host <host-id|auto>
```

Sous Windows :

```powershell
py -3 .agent/workflow/scribe/scribe tenor-init --type cli --host <host-id|auto>
```

`tenor-init` est l'unique autorité publique d'installation, de relocation et de reprise. `bootstrap` est une primitive interne/legacy et ne doit jamais remplacer TENOR INIT dans un bundle V2.16.

## Autorité et ordre

TENOR INIT doit :

1. résoudre le root courant ;
2. classifier l'installation avant SCRIBE ;
3. purger uniquement l'état copié lié à un ancien root quand la relocation est prouvée ;
4. adopter ou créer la mémoire SCRIBE de destination ;
5. vérifier ou demander le build Graphify borné ;
6. finaliser le manifest local ;
7. détecter et configurer uniquement le host project-local vérifié ;
8. exiger une reconnexion puis une nouvelle init si la configuration change ;
9. vérifier le serveur MCP local ;
10. vérifier la visibilité réelle des tools dans le host ;
11. prouver le root binding ;
12. bridger la session indépendante avec la preuve serveur one-shot ;
13. produire `TENOR_INIT_READY`.

Tant que les tools ne sont pas visibles dans le host réel, le verdict maximal est :

```text
LOCAL_INIT_READY_HOST_MCP_UNBOUND
```

`python3 .agent/mcp/server_entry.py --list-tools` ou un JSON-RPC lancé depuis un shell ne prouve jamais la visibilité host.

## Graphify et SCRIBE

- Graphify = structure : quoi, où, comment, dépendances, centralité, communautés, blast radius.
- SCRIBE = causalité : pourquoi, douleur, décision, régression, SCAR, GHOST, dette, `ne_pas_reproposer`.
- Les outputs Graphify canoniques vivent sous `.agent/state/outputs/graphify-out/`.
- Le graphe réel peut exposer `nodes + links` ; le format historique supporté est `nodes + edges`.
- Un graphe manquant, vide à tort, stub, wrong-root, stale ou contradictoire bloque les writes.
- Les agents lisent la mémoire via `.agent/workflow/scribe/scribe-rag` ou MCP `scribe_query`, jamais en parcourant directement le fichier `.scribe`.
- Une requête mémoire doit modifier le plan ou produire une contradiction explicitement auditée.
- Protocole complet : `.agent/workflow/scribe/sel/docs/scribe.md`.

## Workflow par tâche

Avant toute mutation produit :

```text
tenor_task_start(objective, intent, resources, scope)
  -> SCRIBE cible + Graphify cible, executes en interne
tenor_apply_changeset(task_id, changes[], validators[])
  -> preflight complet + locks ordonnes + commit atomique ou rollback total
  -> record SCRIBE runtime + cloture terminale
```

Les writes directs via shell, redirection, `tee`, `sed -i`, `cp`, `mv`, `rm`, outil natif edit/write/apply-patch ou équivalent sont interdits hors MCP.

L'API normale de tâche contient seulement `tenor_task_start`,
`tenor_apply_changeset`, `tenor_activity` et `tenor_task_control`. Les anciens
outils fins restent internes pour compatibilité ; le LLM hôte ne doit jamais
reconstruire manuellement leur chorégraphie.

## Multi-agent

- Chaque terminal exécute son propre TENOR INIT et reçoit une session distincte.
- Le bootstrap partagé est sérialisé.
- `TENOR_INIT_SAME_PROJECT` ne purge jamais la coordination active.
- Les agents partagent runtime SQLite, SCRIBE et Graphify.
- L'identité est liée au processus MCP après le bridge ; les appels de tâche n'acceptent ni `agent_id` ni token fourni par le LLM.
- Un agent ne peut ni retirer ni contrôler la tâche d'un autre agent.
- Un heartbeat daemon et un TTL roulant maintiennent l'activité réelle sans masquer un processus mort.
- Toute clôture laisse zéro transaction ou lock en attente.

## Mémoire causale

Avant de fermer une vraie session, poser :

> Qu'est-ce qui fera souffrir le prochain LLM si je ne le documente pas ?

Une douleur, cause racine, régression ou approche rejetée durable devient SCAR/GHOST/PAT selon le cas. Une activité sans valeur causale ne doit pas être promue artificiellement.

## Hygiène et documentation

- Les outputs runtime et Graphify générés restent hors des commits produit par défaut.
- `.agent/` n'est versionné que lors d'une maintenance intentionnelle de l'outillage.
- Toute évolution d'architecture doit synchroniser les surfaces listées dans `.agent/docs/DOCUMENTATION_SYNC_POLICY.md` et leurs générateurs.
- Les anciens baselines datés, fichiers `.old` et exemples pré-V2.16 sont historiques, jamais normatifs.

## Invariant SAME_PROJECT (V2.16.1)

Sur `TENOR_INIT_SAME_PROJECT`, `bootstrap_project()` n'appelle jamais l'installateur forcé et ne réécrit aucun fichier suivi du bundle. La dérive est signalée en warning ; la réparation reste explicite (`scribe install --force`). TENOR peut uniquement gérer l'entrée MCP project-local vérifiée et son reçu de binding. `NEW_INSTALLATION` / `RELOCATED_PROJECT` / `LEGACY_INSTALLATION` conservent l'installation du bundle.

## Invariant purge/migration sans perte (V2.16.2)

Une purge de runtime conserve `.agent/state/outputs/` byte-for-byte. Elle réinitialise seulement les états projet-liés (runtime, proofs, locks, sessions, agents, redteam, backups et manifest). Les outputs Graphify préservés doivent encore réussir la validation root/fingerprint avant readiness. Lors d'un conflit de migration, la destination canonique gagne et la donnée legacy est placée sous `_legacy_migrated/`.
