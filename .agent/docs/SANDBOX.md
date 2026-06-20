# SANDBOX — agent-scribe-graphify V2

Ce document décrit le mode d'isolation OS optionnel de `.agent`.

## Objectif

Le write gate MCP empêche une écriture d'être acceptée par le protocole si elle ne passe pas par :

```text
workflow_next → file_hash → propose_patch → apply_patch
```

Le mode sandbox ajoute une barrière plus forte : le host LLM est lancé dans un environnement où le projet est visible en lecture seule. Le serveur MCP réel reste hors sandbox et conserve le droit d'écrire via `apply_patch`.

## Architecture

```text
host LLM sandboxé
  ↓ stdio MCP
server_proxy.py
  ↓ socket local
mcp_daemon.py hors sandbox
  ↓
server_ext.py / apply_patch / delete_resource
  ↓
écriture ou suppression contrôlée
```

## Fichiers ajoutés

```text
.agent/scripts/mcp_daemon.py     = daemon MCP hors sandbox
.agent/mcp/server_proxy.py       = proxy stdio MCP dans sandbox
.agent/scripts/agent_sandbox.py  = launcher Linux strict basé bubblewrap
```

## Dépendance stricte

Le mode strict Linux demande bubblewrap :

```bash
sudo apt install bubblewrap
```

Sans bubblewrap, le launcher refuse de prétendre fournir une isolation Linux stricte.

macOS et Windows ont actuellement un plan d'installation et une stratégie dans `OS_ISOLATION.md`, mais pas encore un launcher strict aussi complet que le wrapper Linux bubblewrap.

## Générer la config MCP proxy

```bash
python3 .agent/scripts/agent_sandbox.py --print-mcp-config
```

La configuration générée pointe vers :

```text
.agent/mcp/server_proxy.py
```

Le proxy parle au daemon MCP hors sandbox via `AGENT_MCP_SOCKET`.

## Lancer un host dans la sandbox

Exemple générique :

```bash
python3 .agent/scripts/agent_sandbox.py -- <commande-du-host>
```

Exemple simple :

```bash
python3 .agent/scripts/agent_sandbox.py -- python3 -c 'print("host sandbox ok")'
```

## Vérifier que le proxy voit MCP

Dans un terminal, générer la config ou lancer via le launcher. Le host doit voir les tools :

```text
workflow_next
apply_patch
delete_resource
```

Smoke complet du chemin proxy/daemon :

```bash
python3 .agent/scripts/sandbox_smoke.py
```

Résultat attendu :

```text
SANDBOX_SMOKE_OK
```

## Limite honnête

Cette isolation est stricte seulement pour un host réellement lancé via `agent_sandbox.py`.

Si un autre processus est lancé hors sandbox avec les droits normaux de ton utilisateur, il garde les droits que Linux lui donne. Pour obtenir une barrière complète, tous les hosts LLM doivent être lancés via le wrapper sandbox ou via un compte utilisateur/conteneur dédié.
