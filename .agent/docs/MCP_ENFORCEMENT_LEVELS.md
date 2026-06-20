# MCP Enforcement Levels

## Level 1 — Prompt discipline

Le prompt demande au modèle de suivre le workflow. C'est utile pour l'intention, mais ce n'est pas une barrière technique.

## Level 2 — workflow_next route mécanique

`workflow_next` est un routeur mécanique MCP. Il ordonne `before_task`, `scribe_query`, `graphify_query`, puis les étapes de claim, hash, patch, delete, record et finish.

Ce niveau guide le host qui respecte le protocole, mais ne prouve pas à lui seul que les tools bas niveau ont reçu un contexte.

## Level 3 — MCP write/delete gates

`apply_patch` et `delete_resource` sont des gates MCP réels.

`apply_patch` impose patch propriétaire, status `proposed`, claim actif, `base_hash` et hash courant compatible.

`delete_resource` impose confirmation exacte, `base_hash`, claim actif, fichier régulier, et absence de patch `proposed` ou `conflict` en attente.

## Level 4 — Host sans shell/edit direct

Le host ne fournit pas de shell direct ni d'outil d'édition direct, ou les désactive pour cette session. Les écritures passent alors par les tools MCP visibles.

## Level 5 — OS sandbox/proxy/daemon

Une sandbox OS, un proxy filesystem ou un daemon de contrôle empêche physiquement les écritures hors MCP. C'est le niveau requis pour bloquer un processus local qui possède autrement les droits filesystem.

## Limites V2.7

Le contexte `before_task/scribe_query/graphify_query` n'est pas encore cryptographiquement lié au patch tant qu'il n'y a pas de task token.

Sans sandbox ou désactivation shell/edit, un host peut contourner MCP par filesystem direct.

Cette V2.7 audite les niveaux d'enforcement existants. Elle ne crée pas encore `task_id/context_token` et ne ferme pas le bypass contexte.
