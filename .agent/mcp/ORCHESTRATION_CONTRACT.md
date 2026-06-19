# ORCHESTRATION_CONTRACT — agent-scribe-graphify MCP v0.1

## 0. Intention

`agent-scribe-graphify` transforme `.agent/` en contrôle aérien local multi-agent.
Le serveur MCP est le canal mécanique commun entre les hôtes agentiques : Copilot, Cursor, Claude Code, Gemini CLI, Codex CLI, OpenCode, Windsurf.

## 1. Règle non négociable

Si le serveur MCP `agent-scribe-graphify` n’est pas visible ou autorisé par le host, le LLM hôte doit stopper. Il ne doit pas coder, ne doit pas simuler SCRIBE, ne doit pas simuler Graphify, et doit demander à l’utilisateur d’autoriser/installer le serveur MCP.

## 2. Ordre obligatoire

1. `bootstrap`
2. `register_agent` si bootstrap n’a pas déjà enregistré l’agent
3. `session_status`
4. `before_task` pour chaque demande utilisateur
5. `scribe_query` si demandé par `before_task`
6. `graphify_query` si demandé par `before_task`
7. `claim_resource` avant toute écriture
8. `before_edit` immédiatement avant chaque modification
9. `finish_task` avant la fin de session

## 3. Politique multi-agent

Un agent peut lire librement, mais ne peut écrire qu’avec un claim actif.
Deux agents sur deux ressources différentes peuvent travailler en parallèle.
Deux agents sur le même fichier ne doivent pas faire de direct edit concurrent. Le serveur retourne `DIRECT_EDIT_REFUSED` et exige une future patch queue.

## 4. Politique SCRIBE

Lire SCRIBE souvent via `scribe_query`.
Écrire SCRIBE seulement quand une trace durable est utile : SCAR, GHOST, VAC, PAT, DEBT ou JOURNAL.

## 5. Politique Graphify

Consulter Graphify avant les tâches code, architecture, dépendances, registry, stratégie, hook, sécurité ou refactor.
Mettre à jour Graphify après modification structurante, nouveau fichier source, nouvel import/export ou tâche critique.

## 6. Runtime court terme

La vérité opérationnelle courte durée est `.agent/runtime/coordination.sqlite`.
SCRIBE reste mémoire longue durée.
Graphify reste carte structurelle.

## 7. Patch queue

La v0.1 prépare les tables `patches` et `conflicts`.
La v0.2 ajoutera `propose_patch`, `apply_patch`, base hash obligatoire, overlap detection et conflict resolver.
