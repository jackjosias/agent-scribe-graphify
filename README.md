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
TENOR INIT ::[— depuis la racine du workspace courant, lis comme un fichier local avec l’outil normal de lecture de fichiers — jamais avec un résolveur de skills — le chemin exact "./.agent/skills/init-tenor/SKILL.md"; n’utilise jamais "~/.agent", "~/.agents" ni aucun chemin global; applique ensuite intégralement ce fichier et continue automatiquement jusqu’à TENOR_INIT_READY, HOST_RECONNECT_REQUIRED ou un verdict FAIL_CLOSED explicite.]
```

Ce déclencheur oblige l'agent à lire d'abord le skill local du projet.

La commande mécanique canonique, exécutée depuis la racine du projet, est :

```bash
.agent/workflow/scribe/scribe tenor-init --type cli --host <host-id|auto>
```

Sous Windows :

```powershell
py -3 .agent/workflow/scribe/scribe tenor-init --type cli --host <host-id|auto>
```

`bootstrap` reste une primitive interne/legacy. Il n'est plus l'autorité publique d'installation, de relocation ou de reprise V2.16.

Invariant terrain V2.16.1 : sur `TENOR_INIT_SAME_PROJECT`, l'init ne répare jamais le bundle (`AGENTS.md`, `.agent/rules/scribe.md`, `.graphifyignore`, `.agent/.gitignore`) ; seule l'entrée MCP project-local vérifiée du host et son reçu de binding peuvent être gérés automatiquement. La réparation du bundle reste explicite (`scribe install --force`).

Invariant terrain V2.16.2 : une purge requise réinitialise le runtime projet-lié mais préserve `.agent/state/outputs/` byte-for-byte. La destination canonique gagne lors d'un conflit avec un output legacy, qui est déplacé sous `_legacy_migrated/`. Un Graphify préservé doit encore réussir les contrôles root/fingerprint avant toute readiness.

Invariant zéro-setup Graphify : aucune installation globale de Graphify n'est
requise. Si le runtime manque, TENOR INIT télécharge le wheel officiel épinglé
`graphifyy==0.9.26`, vérifie son SHA-256, installe ses dépendances contraintes
dans `.agent/state/runtime/toolchains/graphify/`, vérifie le manifeste
d'intégrité, puis construit le graphe dans la même invocation. Une erreur de
politique, téléchargement, hash, dépendance, probe ou intégrité échoue
explicitement sans publier de runtime partiel.

## Ordre de vérité V2.16

```text
RESOLVE
CLASSIFY
RESET_RUNTIME_IF_REQUIRED
PRESERVE_CANONICAL_OUTPUTS
MIGRATE_AND_QUARANTINE_LEGACY_CONFLICTS
ADOPT_PROJECT
ADOPT_MEMORY
PROVISION_AND_VERIFY_LOCAL_GRAPHIFY_RUNTIME_IF_REQUIRED
VERIFY_GRAPH
FINALIZE_INSTALLATION
DETECT_AND_CONFIGURE_VERIFIED_HOST
RECONNECT_AND_RERUN_IF_CHANGED
VERIFY_LOCAL_MCP
VERIFY_ACTUAL_HOST_PROCESS_BINDING
PROVE_ROOT_BINDING
BRIDGE_SESSION
TENOR_INIT_READY
```

Une commande locale `server_entry.py --list-tools` ou un JSON-RPC lancé manuellement au shell prouve seulement que le serveur MCP local est chargeable. Elle ne prouve jamais que les tools sont visibles par le modèle dans OpenCode, Codex, Cline, Cursor ou un autre host.

La copie brute et complète de `.agent/` dans un autre projet est un chemin d'installation obligatoire sur Linux, macOS et Windows. TENOR détecte la relocation, préserve la mémoire SCRIBE de destination, invalide le binding Graphify copié et reconfigure le host project-local sans dépendre d'un outil de synchronisation.

## État actuel

`main` contient la correction V2.16 fusionnée. La phase zéro-setup ajoute :

- nouvelle installation, même projet et relocation sûre ;
- préservation exacte de la mémoire SCRIBE cible ;
- purge runtime sans suppression des outputs canoniques ;
- quarantaine des conflits legacy sans écrasement canonique ;
- Graphify réel au format `nodes + links` ainsi que compatibilité `nodes + edges` ;
- serveur MCP local non destructif ;
- concurrence et atomicité sur Linux, macOS et Windows ;
- validation profonde et red-team ;
- adoption terrain sur une copie isolée d'une codebase de plus de 1 000 fichiers.
- provisioning Graphify project-local, versionné, vérifié et single-flight ;
- replay local depuis une copie brute sans commande `graphify` globale ;
- premier INIT avec graphe réel, second INIT idempotent et bridge
  `TENOR_INIT_READY`.

La preuve CI zéro-setup Ubuntu/macOS/Windows est un gate obligatoire de la
branche qui introduit ce mécanisme. Après sa fusion, le gate suivant reste le
replay de **six véritables hôtes LLM simultanés** ; six processus MCP ne
constituent pas cette preuve.

## Documents canoniques

- Autorité V2.16 : `.agent/docs/TENOR_INIT_SINGLE_AUTHORITY.md`
- Contrat de préservation : `.agent/docs/V2.16_DATA_PRESERVATION.md`
- Skill de démarrage : `.agent/skills/init-tenor/SKILL.md`
- Règles machine : `.agent/rules/tenor-init-v2.json`
- Résultats terrain : `.agent/docs/V2.16_TERRAIN_FINDINGS.md`
- Runtime Graphify zéro-setup : `.agent/docs/GRAPHIFY_ZERO_SETUP.md`
- Synchronisation documentaire : `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`
- Guides hosts : `.agent/docs/hosts/README.md`

## Interdiction centrale

Aucune tâche produit ne doit commencer avant `TENOR_INIT_READY`. Aucune écriture ne doit contourner les tools MCP, les claims, locks, leases, patch queue, audit et clôture.
