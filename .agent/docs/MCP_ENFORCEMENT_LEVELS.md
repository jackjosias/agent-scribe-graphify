# MCP Enforcement Levels

## Level 1 — Prompt discipline

Le prompt demande au modèle de suivre le workflow. C'est utile pour l'intention, mais ce n'est pas une barrière technique.

## Level 2 — workflow_next route mécanique

`workflow_next` est un routeur mécanique MCP. Il ordonne `before_task`, `scribe_query`, `graphify_query`, puis les étapes de claim, hash, patch, delete, record et finish.

Depuis V2.8, `workflow_next` transporte aussi `task_id/context_token` après `before_task` pour que les étapes de contexte puissent être liées aux writes MCP. Depuis V2.8.1, les writes/deletes refusent les contextes wildcard : un task token mutateur doit être scopé à une resource précise.

## Level 3 — MCP context/write/delete gates

V2.8 ferme le bypass MCP-context avec `task_id/context_token` stocké dans `coordination.sqlite`. V2.8.1 ferme le bypass de scope `resource="" -> target` pour `propose_patch` et `delete_resource`.

`before_task` crée un contexte de tâche scopé à `agent_id`, `request`, `intent` et `resource`, avec TTL. `scribe_query` et `graphify_query` marquent ce contexte comme prêt. Le token brut n'est jamais stocké, seul son hash l'est.

`propose_patch` et `delete_resource` exigent maintenant un contexte prêt, une resource de contexte non vide, une égalité stricte avec la cible normalisée, et un intent mutateur autorisé avant d'appeler les gates bas niveau.

`apply_patch` impose patch propriétaire, status `proposed`, claim actif, `base_hash` et hash courant compatible.

`delete_resource` conserve ses protections existantes : confirmation exacte, `base_hash`, claim actif, fichier régulier, et absence de patch `proposed` ou `conflict` en attente.

## Level 4 — Host sans shell/edit direct

Le host ne fournit pas de shell direct ni d'outil d'édition direct, ou les désactive pour cette session. Les écritures passent alors par les tools MCP visibles.

## Level 5 — OS sandbox/proxy/daemon

Une sandbox OS, un proxy filesystem ou un daemon de contrôle empêche physiquement les écritures hors MCP. C'est le niveau requis pour bloquer un processus local qui possède autrement les droits filesystem.

## Limites restantes

V2.8 ferme le bypass MCP-context : `bootstrap -> claim_resource -> file_hash -> propose_patch -> apply_patch` sans contexte ne peut plus proposer de patch. V2.8.1 interdit aussi les contextes wildcard pour les writes/deletes : un contexte mutateur sans resource ne peut plus écrire ou supprimer une cible réelle.

Sans sandbox ou désactivation shell/edit, un host peut encore contourner MCP par filesystem direct. Ce point reste un sujet host/OS, pas un sujet `task_id/context_token`.
