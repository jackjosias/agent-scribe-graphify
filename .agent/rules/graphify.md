---
trigger: always_on
---

# GRAPHIFY — CONTRAT PORTABLE ET HONNÊTE

Graphify décrit la structure observée lors du dernier build validé. Il n’est
jamais présumé « toujours frais » et aucun `watch` n’est présumé actif.

## Sortie canonique unique

```text
.agent/state/outputs/graphify-out/graph.json
.agent/state/outputs/graphify-out/GRAPH_REPORT.md
.agent/state/outputs/graphify-out/graph.html
.agent/state/outputs/graphify-out/GRAPHIFY_READY.json
```

Root `graphify-out/` est legacy-only. Il ne doit être ni créé, ni lu comme
source d’autorité, ni commité par l’agent.

## Build autorisé

- Host MCP lié : appeler `graphify_project_build(timeout_seconds=180)`.
- Avant liaison MCP : exécuter `.agent/workflow/scribe/scribe graph --project-build --timeout 180`.
- Interdit dans un projet portable `.agent` : `graphify .`, `graphify update .`,
  `graphify watch`, ou toute écriture directe vers `graphify-out/`.

Le wrapper autorisé copie les sources dans un miroir isolé, exécute Graphify
dans ce miroir, rebind les chemins au projet réel, publie transactionnellement
la sortie canonique puis vérifie root et fingerprint.

## Consultation

Utiliser le tool MCP `graphify_query` avec `task_id` et `context_token`. Une
sortie absente, stale, wrong-root, fixture, vide ou contradictoire bloque les
mutations et exige le build canonique. Lire quelques fichiers ciblés reste
autorisé lorsque le graphe ne suffit pas ; prétendre qu’un graphe est frais
sans vérifier `GRAPHIFY_READY.json` est interdit.
