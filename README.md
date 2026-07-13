# agent-scribe-graphify

`.agent` est une couche d'exploitation portable pour agents LLM travaillant sur une codebase réelle.

Elle combine quatre autorités complémentaires :

- **TENOR** impose l'ordre mécanique sûr d'installation, de reprise et de tâche ;
- **Graphify** compresse la structure, les dépendances, les communautés et le blast radius ;
- **SCRIBE** restitue la mémoire causale, les erreurs, décisions, SCAR, GHOST et interdictions ;
- **runtime/MCP** coordonne les agents actifs avec identités, proofs, claims, locks, leases et patch queue.

## Démarrage canonique

Pour une session pilotée par un LLM hôte, le déclencheur humain canonique est :

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Ce déclencheur oblige l'agent à lire d'abord le skill local du projet.

La commande mécanique canonique, exécutée depuis la racine du projet, est :

```bash
.agent/workflow/scribe/scribe tenor-init --type cli
```

Sous Windows :

```powershell
python .agent/workflow/scribe/scribe tenor-init --type cli
```

`bootstrap` reste une primitive interne/legacy. Il n'est plus l'autorité publique d'installation, de relocation ou de reprise V2.16.

Invariant terrain V2.16.1 : sur `TENOR_INIT_SAME_PROJECT`, l'init de session est strictement en lecture seule des fichiers suivis (`AGENTS.md`, `.agent/rules/scribe.md`, `.graphifyignore`, `.agent/.gitignore`) ; l'installateur forcé n'est jamais appelé et la réparation du bundle reste explicite (`scribe install --force`). `NEW_INSTALLATION` / `RELOCATED_PROJECT` / `LEGACY_INSTALLATION` conservent l'installation du bundle quand nécessaire.

Invariant terrain V2.16.2 : une purge requise réinitialise le runtime projet-lié mais préserve `.agent/state/outputs/` byte-for-byte. La destination canonique gagne lors d'un conflit avec un output legacy, qui est déplacé sous `_legacy_migrated/`. Un Graphify préservé doit encore réussir les contrôles root/fingerprint avant toute readiness.

## Ordre de vérité V2.16

```text
RESOLVE
CLASSIFY
RESET_RUNTIME_IF_REQUIRED
PRESERVE_CANONICAL_OUTPUTS
MIGRATE_AND_QUARANTINE_LEGACY_CONFLICTS
ADOPT_PROJECT
ADOPT_MEMORY
VERIFY_GRAPH
FINALIZE_INSTALLATION
VERIFY_LOCAL_MCP
CONFIGURE_AND_VERIFY_HOST
PROVE_ROOT_BINDING
BRIDGE_SESSION
TENOR_INIT_READY
```

Une commande locale `server_entry.py --list-tools` prouve seulement que le serveur MCP local est chargeable. Elle ne prouve jamais que les tools sont visibles par le modèle dans OpenCode, Codex, Cline, Cursor ou un autre host.

## État actuel

La branche V2.16 a prouvé :

- nouvelle installation, même projet et relocation sûre ;
- préservation exacte de la mémoire SCRIBE cible ;
- purge runtime sans suppression des outputs canoniques ;
- quarantaine des conflits legacy sans écrasement canonique ;
- Graphify réel au format `nodes + links` ainsi que compatibilité `nodes + edges` ;
- serveur MCP local non destructif ;
- concurrence et atomicité sur Linux, macOS et Windows ;
- validation profonde et red-team ;
- adoption terrain sur une copie isolée d'une codebase de plus de 1 000 fichiers.

Le gate encore volontairement ouvert avant fusion est la preuve dans un **host LLM réel** : tools visibles, root binding, `tenor_init_bridge`, micro-write complet et test de contournement direct.

## Documents canoniques

- Autorité V2.16 : `.agent/docs/TENOR_INIT_SINGLE_AUTHORITY.md`
- Contrat de préservation : `.agent/docs/V2.16_DATA_PRESERVATION.md`
- Skill de démarrage : `.agent/skills/init-tenor/SKILL.md`
- Règles machine : `.agent/rules/tenor-init-v2.json`
- Résultats terrain : `.agent/docs/V2.16_TERRAIN_FINDINGS.md`
- Synchronisation documentaire : `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`
- Guides hosts : `.agent/docs/hosts/README.md`

## Interdiction centrale

Aucune tâche produit ne doit commencer avant `TENOR_INIT_READY`. Aucune écriture ne doit contourner les tools MCP, les claims, locks, leases, patch queue, audit et clôture.
