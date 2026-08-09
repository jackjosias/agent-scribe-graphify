# Graphify Zero Setup — runtime project-local vérifié

## Statut et portée

Ce document définit le contrat d'approvisionnement Graphify utilisé par TENOR
INIT lorsqu'aucune commande `graphify` globale n'est installée.

Les catégories de preuve restent distinctes :

- **implémenté** : autorité runtime, installation atomique et intégration au build ;
- **testé** : politique, hash, concurrence, idempotence, altération et rollback ;
- **terrain-prouvé sur Linux** : copie neuve sans Graphify global, graphe réel,
  second INIT propre et bridge `TENOR_INIT_READY` ;
- **CI-prouvé** : seulement après succès du gate dédié sur Ubuntu, macOS et Windows ;
- **non prouvé ici** : six véritables hôtes LLM simultanés.

## Autorités suivies

```text
.agent/mcp/runtime/graphify_runtime_policy.json
.agent/mcp/runtime/graphify_runtime.py
.agent/workflow/scribe/sel/scripts/scribe_bundle_graph.py
```

La politique épingle :

```text
distribution: graphifyy
version: 0.9.26
module: graphify
index: https://pypi.org/simple
wheel: graphifyy-0.9.26-py3-none-any.whl
wheel sha256: 2184c5891b71f6b9cea127eb0e92fdd33ab8ee5c254c99312227fc6c5af3ada5
python: 3.10+
```

Les dépendances sont contraintes exactement, avec marqueurs Python lorsque
nécessaire. L'installation refuse les distributions source et n'accepte que
des wheels binaires. TENOR neutralise les variables `PIP_*`, `PYTHONHOME` et
`PYTHONPATH` héritées, désactive les fichiers de configuration pip, puis passe
l'index officiel explicitement afin qu'un host ne puisse pas injecter un index
ou un répertoire de wheels alternatif.

## Emplacement et identité

Le runtime est privé au projet :

```text
.agent/state/runtime/toolchains/graphify/
  <graphify-version>/
    <python-os-architecture-abi>/
      TENOR_GRAPHIFY_RUNTIME.json
      tenor_graphify.py
      constraints.txt
      artifacts/
      site/
```

La clé de plateforme contient le tag d'implémentation Python, le système,
l'architecture machine et la plateforme ABI. Un runtime copié depuis un autre
OS, une autre architecture ou un autre ABI ne peut pas satisfaire le manifeste.

## Installation transactionnelle

TENOR :

1. acquiert le verrou interprocessus
   `.agent/state/runtime/locks/graphify-runtime-install.lock` ;
2. recontrôle si un autre processus a déjà publié le runtime ;
3. télécharge le wheel épinglé depuis l'index officiel dans un répertoire
   staging ;
4. vérifie son SHA-256 avant toute installation ;
5. installe le wheel et les dépendances exactes sous `site/` ;
6. crée un lanceur Python isolé relatif à son propre emplacement ;
7. sonde la distribution, sa version et son module ;
8. calcule un digest d'intégrité sur tous les fichiers non générés ;
9. écrit et `fsync` le manifeste ;
10. renomme atomiquement le staging vers l'emplacement final.

Un échec conserve éventuellement une quarantaine `.failed-*` pour le
diagnostic, mais ne publie jamais le chemin final. Huit installateurs
concurrents convergent vers un seul téléchargement et une seule installation.

## Résolution et vérification

Un runtime project-local valide est préféré à toute commande externe. Lors
d'une première résolution dans un processus, TENOR revalide :

- schéma et politique ;
- version, plateforme et hash de politique ;
- SHA-256 du wheel ;
- digest, nombre de fichiers et nombre d'octets ;
- probe isolé de la distribution.

Le résultat sain est mis en cache seulement pour la durée du processus. Un
nouveau processus recommence le contrôle d'intégrité.

Une commande `graphify` externe reste une compatibilité legacy pour les
installations existantes, mais n'est pas qualifiée supply-chain. Le replay
zéro-setup fixe `TENOR_GRAPHIFY_REQUIRE_LOCAL=1` afin d'interdire que cette
compatibilité masque une régression.

## Budget et échec fail-closed

L'approvisionnement et le build partagent la borne du build Graphify. Les
verdicts distinguent notamment :

```text
GRAPHIFY_RUNTIME_POLICY_INVALID
GRAPHIFY_RUNTIME_PYTHON_UNSUPPORTED
GRAPHIFY_RUNTIME_WHEEL_DOWNLOAD_FAILED
GRAPHIFY_RUNTIME_WHEEL_MISSING
GRAPHIFY_RUNTIME_WHEEL_HASH_MISMATCH
GRAPHIFY_RUNTIME_DEPENDENCY_INSTALL_FAILED
GRAPHIFY_LOCAL_RUNTIME_PROBE_FAILED
GRAPHIFY_LOCAL_RUNTIME_INTEGRITY_FAILED
GRAPHIFY_RUNTIME_INSTALL_LOCK_TIMEOUT
TENOR_INIT_GRAPHIFY_RECOVERY_FAILED
```

Aucun de ces verdicts n'autorise une installation globale manuelle comme
fallback automatique.

## Replay d'acceptation

Commande :

```bash
python3 .agent/scripts/graphify_zero_setup_replay.py
```

Le replay :

1. crée et commit un projet applicatif neuf ;
2. copie seulement le bundle `.agent/`, sans runtime ni état ;
3. exige l'absence de commande `graphify` globale ;
4. exécute uniquement TENOR INIT ;
5. vérifie runtime, version, wheel, graphe réel, root et fingerprint ;
6. commit le baseline après la première init ;
7. relance TENOR INIT et exige un arbre Git inchangé ;
8. lance le serveur project-local avec son binding vérifié ;
9. appelle `tenor_init_bridge` et exige `TENOR_INIT_READY` avec scope
   `HOST_PROCESS_ROOT_AND_SESSION`.

Le marqueur terminal est :

```text
GRAPHIFY_ZERO_SETUP_REPLAY_OK
```

Le workflow `.github/workflows/v216-portability.yml` exécute ce replay dans
une copie GitHub Actions neuve sur Ubuntu, macOS et Windows, conserve les logs
sur tout échec et bloque la validation profonde tant que les trois OS ne sont
pas verts.
