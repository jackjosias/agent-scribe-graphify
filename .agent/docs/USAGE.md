# USAGE — agent-scribe-graphify V2

Ce document est la documentation humaine courte. Le workflow réel ne dépend pas de ce texte : il est piloté mécaniquement par le tool MCP `workflow_next`.

## Principe central

```text
Le LLM hôte ne décide pas seul la prochaine étape.
Il appelle workflow_next.
Il exécute le must_call retourné.
Il rappelle workflow_next après chaque étape.
```

Depuis la version MCP `0.2.8`, les écritures acceptées passent par le **context gate MCP** puis le **write gate MCP** :

```text
before_task → scribe_query → graphify_query si requis → task_id/context_token → file_hash → propose_patch → apply_patch
```

Les suppressions acceptées passent par le **delete gate MCP** :

```text
workflow_next → before_task → scribe_query → graphify_query si code → claim_resource → file_hash → delete_resource → release_claim → scribe_record → scribe_promote_record si durable → finish_task
```

`delete_resource` exige une permission utilisateur explicite avec la phrase exacte :

```text
DELETE chemin/relatif/du/fichier
```

`before_edit` refuse les écritures directes du host. Cela ne remplace pas une sandbox OS, mais cela empêche un agent de finir proprement dans le protocole `.agent` en contournant le chemin MCP. L'isolation OS reste optionnelle : le MCP normal fonctionne sans sandbox.

## Validation locale

Depuis la racine du projet :

```bash
python3 .agent/scripts/mcp_smoke.py
```

Résultat attendu :

```text
MCP_SMOKE_ALL_OK
```

Ce smoke-test valide :

```text
- bootstrap MCP
- workflow_next mécanique
- before_task
- claim_resource forcé en patch_queue pour écriture
- before_edit refuse direct host edit
- file_hash
- propose_patch
- apply_patch écrit réellement le fichier via MCP
- delete_resource visible dans server_entry
- finish_task refusé si patch pending
- release_claim
- finish_task OK
- chemins dangereux refusés
- symlink escape refusé
- copie portable de .agent
```

## Audit enforcement V2.8

Audit non bloquant des gates MCP et des bypass réels :

```bash
python3 .agent/scripts/enforcement_redteam_smoke.py
```

Audit strict du lien contexte :

```bash
python3 .agent/scripts/enforcement_redteam_smoke.py --strict-context
```

Le mode normal prouve les gates fondamentaux et rapporte `context_bypass=OPEN|CLOSED`. Depuis V2.8, le résultat attendu est `context_bypass=CLOSED` et `--strict-context` doit passer. Depuis V2.8.1, ce smoke vérifie aussi que les contextes wildcard, les mismatches de resource et les intents non mutateurs sont refusés pour les patches/deletes. `DIRECT_FS_WRITE_OUTSIDE_SANDBOX_OPEN` peut rester présent : c'est une limite host/OS, pas une limite MCP-context.

Les smokes MCP qui nettoient `.agent/state/runtime/coordination.sqlite` ne sont pas parallélisables par défaut. En CI, exécuter `enforcement_redteam_smoke.py`, `mcp_smoke.py`, `delete_resource_smoke.py`, `scribe_record_smoke.py` et `sandbox_smoke.py` en séquentiel, ou isoler chaque job avec son propre runtime state.

## Copier .agent dans un nouveau projet

```bash
cp -a /chemin/source/.agent /chemin/nouveau-projet/.agent
cd /chemin/nouveau-projet
python3 .agent/scripts/mcp_smoke.py
```

Le runtime doit être créé dans :

```text
.agent/state/runtime/coordination.sqlite
```

## Entrée MCP recommandée

Toujours configurer le host MCP vers :

```text
.agent/mcp/server_entry.py
```

Exemple générique :

```json
{
  "mcpServers": {
    "agent-scribe-graphify": {
      "command": "python3",
      "args": ["/CHEMIN/PROJET/.agent/mcp/server_entry.py"],
      "cwd": "/CHEMIN/PROJET"
    }
  }
}
```

`server_entry.py` recalcule la racine du projet à partir de son propre emplacement. Il ne dépend pas du dossier courant du host.

## Tool parent obligatoire : workflow_next

Le LLM hôte doit appeler :

```text
workflow_next
```

avant toute action importante.

Exemple avant bootstrap :

```json
{
  "request": "corriger le bug dans src/app.py",
  "intent": "write",
  "resource": "src/app.py",
  "host_tool": "claude-code",
  "model_name": "model-name"
}
```

Réponse attendue :

```json
{
  "verdict": "NEXT_ACTION_REQUIRED",
  "must_call": {
    "tool": "bootstrap"
  }
}
```

Après avoir exécuté `must_call`, le LLM doit rappeler `workflow_next` avec `agent_id`, `intent`, `resource` et `last_verdict`.

## Boucle contexte obligatoire

Avant toute écriture ou suppression MCP, `workflow_next` impose le contexte et transporte un token :

```text
before_task → task_id/context_token → targeted_scribe_query → targeted_graphify_query si requis → action
```

`scribe_query` est une requête RAG ciblée et minimale. SCRIBE n'est jamais lu entièrement par défaut. `graphify_query` est une requête ciblée structure/impact/blast-radius dès que la tâche touche au code, architecture, refactor, bug, API, test, backend, frontend, sécurité, base de données, migration ou production.

## Gravure mémoire

`scribe_record` écrit un record JSON local de staging dans :

```text
scribe-out/records/
```

Ce record ne signifie pas qu'une mémoire durable a été mise à jour. Quand `workflow_next` demande `scribe_record`, il faut l'exécuter avant de décider s'il faut ensuite appeler `scribe_promote_record` puis `finish_task`.

## Preuve mémoire finish_task

Depuis V2.15, `finish_task` exige une preuve mémoire si des patches MCP ont été
appliqués via le protocole. Le verdict `MEMORY_PROOF_REQUIRED` est retourné quand
aucun `patch_id` des mutations autorisées n'apparaît dans le fichier mémoire canonique
`AGENT-MEMOIRE_PROJECT_STATUS.scribe`.

Procédure de déblocage :

1. Modifier `AGENT-MEMOIRE_PROJECT_STATUS.scribe` via le protocole MCP contrôlé
   (`file_hash` → `propose_patch` → `apply_patch`) avec les `patch_id` retournés
   par `finish_task` dans `applied_patch_ids`
2. Rappeler `scribe_query` pour rafraîchir le hash mémoire
3. Rappeler `finish_task`

Le commit du fichier mémoire n'est pas automatiquement requis. Si l'utilisateur
demande explicitement le versioning, `git add`/`git commit` sont autorisés.

Quand le record local est durable, il faut d'abord le promouvoir avec `scribe_promote_record`.
La vérification se fait par substring exact-match : au moins un `patch_id` doit
apparaître textuellement dans le fichier mémoire. Aucune modification directe de
`AGENT-MEMOIRE_PROJECT_STATUS.scribe` n'est autorisée — le tripwire la détecte
comme `DIRECT_WRITE_BYPASS_DETECTED`.

## Workflow mécanique attendu

```text
workflow_next
→ bootstrap
→ workflow_next
→ before_task
→ workflow_next
→ scribe_query
→ workflow_next
→ graphify_query
→ workflow_next
→ claim_resource
→ workflow_next
→ file_hash
→ workflow_next
→ propose_patch
→ workflow_next
→ apply_patch
→ workflow_next
→ release_claim
→ workflow_next
→ scribe_record
→ workflow_next
→ finish_task
```

Le LLM ne doit pas inventer ou sauter une étape. `propose_patch` et `delete_resource` refusent maintenant les appels sans contexte prêt, sans resource de contexte précise, avec resource différente, ou avec intent non mutateur.

## Workflow suppression attendu

```text
workflow_next
→ bootstrap
→ workflow_next
→ before_task
→ workflow_next
→ scribe_query
→ workflow_next
→ graphify_query
→ workflow_next
→ claim_resource
→ workflow_next
→ file_hash
→ workflow_next
→ demander permission utilisateur exacte
→ delete_resource
→ workflow_next
→ release_claim
→ workflow_next
→ scribe_record
→ workflow_next
→ finish_task
```

La confirmation transmise à `delete_resource` doit correspondre exactement à :

```text
DELETE chemin/relatif/du/fichier
```

## Règles de sécurité appliquées côté MCP

Ressources refusées :

```text
- traversal hors projet
- chemins absolus système
- chemins absolus Windows/UNC
- symlinks qui sortent du projet
```

Règles d'écriture :

```text
- lecture libre
- écriture = claim obligatoire
- écriture acceptée = apply_patch obligatoire
- suppression = claim + base_hash + confirmation exacte obligatoires
- patch = base_hash obligatoire
- finish_task interdit avec patch pending/conflict
- finish_task retourne MEMORY_PROOF_REQUIRED si des patches MCP sont appliqués sans preuve dans AGENT-MEMOIRE_PROJECT_STATUS.scribe
- before_edit refuse les écritures directes du host
```

## Debug manuel

Lister les tools :

```bash
python3 .agent/mcp/server_entry.py --list-tools
```

Bootstrap manuel :

```bash
python3 .agent/mcp/server_entry.py --call bootstrap --args '{"host_tool":"manual","model_name":"test","run_legacy_bootstrap":false}'
```

Demander la prochaine étape :

```bash
python3 .agent/mcp/server_entry.py --call workflow_next --args '{"request":"modifier README.md","intent":"write","resource":"README.md","host_tool":"manual","model_name":"test"}'
```

## Limite honnête

`.agent` ne peut pas retirer au système d'exploitation les droits d'écriture déjà donnés à un processus externe du même utilisateur. Pour une prévention physique totale, il faudra une sandbox OS, un utilisateur séparé, un conteneur ou un wrapper qui ne donne pas d'outil d'écriture direct au host.

Mais dans le protocole `.agent` V2.4, une modification acceptable doit passer par :

```text
workflow_next → before_task → scribe_query → graphify_query si code → claim_resource → file_hash → propose_patch → apply_patch
```

Et une suppression acceptable doit passer par :

```text
workflow_next → before_task → scribe_query → graphify_query si code → claim_resource → file_hash → delete_resource → release_claim → scribe_record → scribe_promote_record si durable → finish_task
```

## Règle finale

Avant de confier un projet à un LLM hôte :

```bash
python3 .agent/scripts/mcp_smoke.py
```

Si le résultat n'est pas `MCP_SMOKE_ALL_OK`, ne pas utiliser le projet en mode agentique.
