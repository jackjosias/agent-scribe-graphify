# DELETE_RESOURCE — suppression contrôlée

La suppression réelle d'un fichier doit passer par MCP.

Workflow :

```text
workflow_next → claim_resource → file_hash → delete_resource → release_claim → finish_task
```

Règle de permission :

```text
delete_resource refuse d'agir sans confirm_phrase exacte.
```

Format de confirmation :

```text
DELETE chemin/relatif/du/fichier
```

Exemple :

```text
DELETE src/old-file.ts
```

Le LLM hôte doit demander cette permission à l'utilisateur avant d'appeler `delete_resource` avec `confirm_phrase`.

Interdictions :

```text
- pas de suppression directe par outil host
- pas de commande système directe pour supprimer
- pas de suppression sans claim
- pas de suppression sans base_hash frais
- pas de suppression avec patch proposé ou en conflit sur la même ressource
```
