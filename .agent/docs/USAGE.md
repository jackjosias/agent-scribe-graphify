# USAGE — agent-scribe-graphify V2

Ce document est la documentation humaine courte. Le workflow réel ne dépend pas de ce texte : il est piloté mécaniquement par le tool MCP `workflow_next`.

## Principe central

```text
Le LLM hôte ne décide pas seul la prochaine étape.
Il appelle workflow_next.
Il exécute le must_call retourné.
Il rappelle workflow_next après chaque étape.
```

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
- claim_resource
- file_hash
- propose_patch
- finish_task refusé si patch pending
- reject_patch
- release_claim
- finish_task OK
- chemins dangereux refusés
- symlink escape refusé
- copie portable de .agent
```

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
    "tool": "bootstrap",
    "args": {
      "host_tool": "claude-code",
      "model_name": "model-name",
      "run_legacy_bootstrap": false
    }
  }
}
```

Après avoir exécuté `must_call`, le LLM doit rappeler `workflow_next` avec `agent_id`, `intent`, `resource` et `last_verdict`.

## Workflow mécanique attendu

```text
workflow_next
→ bootstrap
→ workflow_next
→ before_task
→ workflow_next
→ claim_resource
→ workflow_next
→ file_hash
→ workflow_next
→ propose_patch
→ workflow_next
→ list_patches si patch pending
→ reject_patch ou confirm_patch_applied
→ workflow_next
→ release_claim
→ workflow_next
→ finish_task
```

Le LLM ne doit pas inventer ou sauter une étape.

## Règles de sécurité appliquées côté MCP

Ressources refusées :

```text
../outside.txt
/etc/passwd
C:\Windows\win.ini
C:/Windows/win.ini
\\server\share\secret.txt
symlink -> /etc/passwd
symlink directory -> /tmp
```

Règles d'écriture :

```text
- lecture libre
- écriture = claim obligatoire
- patch = base_hash obligatoire
- finish_task interdit avec patch pending/conflict
- direct edit interdit sous claim patch_queue
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

## Règle finale

Avant de confier un projet à un LLM hôte :

```bash
python3 .agent/scripts/mcp_smoke.py
```

Si le résultat n'est pas `MCP_SMOKE_ALL_OK`, ne pas utiliser le projet en mode agentique.
