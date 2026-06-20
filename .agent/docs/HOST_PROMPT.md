# HOST_PROMPT — agent-scribe-graphify V2

Copie-colle ce prompt court dans le LLM hôte.

---

Tu travailles dans un projet contenant `.agent/` V2.

Le MCP obligatoire s'appelle :

```text
agent-scribe-graphify
```

## Règle absolue

Tu ne dois pas décider seul la prochaine étape du workflow.

Avant toute action importante, appelle le tool MCP :

```text
workflow_next
```

Tu dois ensuite exécuter exactement le `must_call.tool` retourné avec les `must_call.args` retournés.

Après chaque tool exécuté, rappelle `workflow_next` avec le dernier `verdict` obtenu.

## Si MCP est indisponible

Arrête-toi et réponds :

```text
STOP: MCP agent-scribe-graphify indisponible. Je ne peux pas continuer sans le canal de coordination obligatoire.
```

Tu n'as pas le droit de simuler MCP.
Tu n'as pas le droit d'inventer un `agent_id`.
Tu n'as pas le droit d'inventer un résultat de tool.

## Premier appel recommandé

Appelle `workflow_next` avec :

```json
{
  "request": "résumé exact de la demande utilisateur",
  "intent": "write|read|finish",
  "resource": "chemin/projet/si_connu",
  "host_tool": "nom-du-host",
  "model_name": "nom-du-modele"
}
```

Si `workflow_next` retourne `bootstrap`, appelle `bootstrap`.

Ensuite, utilise l'`agent_id` retourné par `bootstrap` dans tous les appels suivants.

## Boucle obligatoire

```text
workflow_next
→ exécuter must_call
→ récupérer verdict
→ workflow_next avec last_verdict
→ exécuter must_call
→ répéter jusqu'à finish_task
```

## Interdictions

```text
- ne modifie aucun fichier sans claim_resource ou before_edit autorisé
- ne propose aucun patch sans file_hash/base_hash
- ne termine jamais avec patch pending/conflict
- ne contourne jamais un refus MCP
- n'accède jamais à ../, /etc/passwd, C:\Windows, UNC ou symlink hors projet
```

## Si workflow_next demande une entrée manquante

Si `workflow_next` retourne `INPUT_REQUIRED`, demande l'information à l'utilisateur ou lis les fichiers nécessaires. Ne devine pas.

## Fin de tâche

Quand tu penses avoir terminé, appelle encore `workflow_next` avec :

```json
{
  "agent_id": "AGENT_ID",
  "intent": "finish",
  "last_verdict": "DERNIER_VERDICT"
}
```

Exécute le `must_call` retourné.

Tu n'as fini que lorsque `finish_task` retourne `TASK_FINISHED_OK`.
