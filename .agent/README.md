# `.agent` — Démarrage TENOR project-local

Ce répertoire est la couche d’exploitation portable TENOR/SCRIBE/Graphify/MCP du projet courant. Il se copie avec la codebase et ne dépend pas d’un skill installé globalement chez l’utilisateur.

## Phrase unique d’initialisation

Dans une nouvelle session d’un LLM hôte, depuis le workspace qui contient `.agent/`, envoyer exactement :

```text
TENOR INIT ::[— depuis la racine du workspace courant, lis comme un fichier local avec l’outil normal de lecture de fichiers — jamais avec un résolveur de skills — le chemin exact "./.agent/skills/init-tenor/SKILL.md"; n’utilise jamais "~/.agent", "~/.agents" ni aucun chemin global; applique ensuite intégralement ce fichier et continue automatiquement jusqu’à TENOR_INIT_READY, HOST_RECONNECT_REQUIRED ou un verdict FAIL_CLOSED explicite.]
```

Cette même phrase est utilisée au premier démarrage, après une relocation et dans les sessions suivantes. Il n’existe pas une commande conversationnelle différente à mémoriser pour chaque hôte.

## Ce que cette phrase impose

Le LLM hôte doit :

1. partir de la racine du workspace actuellement ouvert ;
2. utiliser son lecteur normal de fichiers, pas un registre ou résolveur de skills ;
3. lire le fichier exact `./.agent/skills/init-tenor/SKILL.md` ;
4. ne jamais remplacer ce chemin par `~/.agent`, `~/.agents` ou une installation globale ;
5. lire ensuite les règles project-local indiquées par le skill ;
6. appliquer le workflow jusqu’à un verdict terminal machine.

Si le fichier project-local est absent ou illisible, l’agent doit échouer explicitement. Il ne doit pas chercher silencieusement une copie globale.

## Déroulement attendu

Le skill project-local conduit l’hôte dans cet ordre :

1. résolution du root courant ;
2. classification de l’installation ou de la relocation ;
3. adoption ou création de la mémoire SCRIBE du projet ;
4. validation ou reconstruction Graphify bornée et single-flight ;
5. finalisation de l’installation locale ;
6. détection et configuration project-local du host pris en charge ;
7. reconnexion du host si sa configuration vient de changer ;
8. preuve que le MCP est réellement visible depuis le processus du host ;
9. preuve du root binding ;
10. bridge de la session indépendante ;
11. verdict `TENOR_INIT_READY`.

Une simple liste locale des tools MCP ne prouve ni leur visibilité dans le host ni le bon root.

## Verdicts terminaux

### `TENOR_INIT_READY`

L’installation locale, SCRIBE, Graphify, la visibilité MCP, le root binding et le bridge de session sont prouvés. Une tâche peut commencer avec :

```text
TENOR TASK:: <objectif>
```

### `HOST_RECONNECT_REQUIRED`

La configuration project-local du host a été créée ou modifiée. Redémarrer ou reconnecter le host, puis envoyer à nouveau la phrase unique d’initialisation ci-dessus. Ce verdict est une interruption de sécurité attendue, pas un succès final.

### Verdict `FAIL_CLOSED`

Arrêter le travail produit et restituer le verdict exact avec sa preuve. Ne pas inventer de succès, changer de root, créer une identité de remplacement ou demander à l’utilisateur d’appliquer un patch manuel.

## SCRIBE et Graphify pendant les tâches

Après `TENOR_INIT_READY`, `tenor_task_start` récupère de manière ciblée :

- SCRIBE pour la causalité : décisions, erreurs, SCAR, GHOST, interdictions et `ne_pas_reproposer` ;
- Graphify pour la structure : dépendances, communautés, centralité et blast radius.

Ces preuves sont liées à la tâche et doivent modifier le plan. Une requête exécutée puis ignorée ne constitue pas une utilisation de la mémoire.

Les mutations passent par un changeset atomique avec validateurs. La clôture produit un verdict explicite d’admission mémoire : promotion canonique, runtime-only motivé, décision utilisateur ou conflit. Il n’existe pas de clôture silencieuse.

## Multi-agent

Chaque terminal exécute la même phrase d’initialisation et reçoit sa propre identité de session. Les agents partagent SCRIBE, Graphify et le runtime de coordination, mais ne partagent pas leurs leases, claims, locks ou preuves one-shot. La reconstruction Graphify est sérialisée afin que plusieurs terminaux ne lancent pas plusieurs builds concurrents.

## Portabilité et limite honnête

Le contrat est project-local et indépendant du framework applicatif : C, C++, Rust, Java, Python, JavaScript ou projet mixte.

La phrase est portable entre hôtes capables de lire le workspace et d’exécuter les actions autorisées. Aucun texte ne peut contourner une permission de lecture ou d’exécution réellement refusée par le host ; cette situation doit produire un verdict explicite plutôt qu’une fausse initialisation.

## Autorités

En cas de contradiction, consulter dans cet ordre :

1. `.agent/rules/tenor-init-v2.json` ;
2. `.agent/skills/init-tenor/SKILL.md` ;
3. `.agent/docs/TENOR_INIT_SINGLE_AUTHORITY.md` ;
4. `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`.

L’ancien déclencheur court `TENOR INIT::[.agent/skills/init-tenor/SKILL.md]` reste uniquement une compatibilité historique. Les nouvelles sessions et documentations utilisent la phrase project-local explicite.
