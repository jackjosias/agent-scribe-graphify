# agent-scribe-graphify MCP

Serveur MCP portable contenu dans `.agent/`.

## Lancement serveur

Depuis la racine du projet :

```bash
python .agent/mcp/server.py
```

Le serveur parle MCP JSON-RPC sur stdio et expose les outils :

- `bootstrap`
- `register_agent`
- `heartbeat`
- `session_status`
- `before_task`
- `scribe_query`
- `graphify_query`
- `claim_resource`
- `release_claim`
- `before_edit`
- `finish_task`
- `installation_required`

## Tests smoke

```bash
python .agent/mcp/server.py --list-tools
python .agent/mcp/server.py --call bootstrap --args '{"host_tool":"manual-smoke","model_name":"test","run_legacy_bootstrap":false}'
python .agent/mcp/server.py --call before_task --args '{"request":"Ajoute une fonction de validation email"}'
```

## Contrat

Voir `ORCHESTRATION_CONTRACT.md`.

## Principe

`TENOR INIT::[.agent/skills/init-tenor/SKILL.md]` doit vérifier la disponibilité du serveur MCP `agent-scribe-graphify`.
Si le MCP est absent, le LLM hôte doit stopper et demander l’installation/autorisation du serveur MCP.
