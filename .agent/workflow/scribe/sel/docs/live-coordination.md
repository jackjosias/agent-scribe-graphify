# Workflow multi-agent: live coordination SCRIBE

Derniere verification locale: 2026-06-01.

## Emplacement canonique

Ce fichier appartient a la racine SCRIBE portable: `.agent/workflow/scribe/sel/docs/live-coordination.md`.
Tout dossier frere comme `.agent/workflow/multi-agent/` est non canonique: migrer son contenu sous `.agent/workflow/scribe/` puis supprimer le dossier vide.

Ce workflow encadre plusieurs agents CLI ouverts en parallele dans le meme
repository. Objectif: eviter que les agents cassent le travail des autres,
ecrivent une memoire obsolete dans le SCRIBE, ou modifient la meme surface sans
coordination.

## Principe central

Le lock SCRIBE protege uniquement l'ecriture dans
`AGENT-MEMOIRE_PROJECT_STATUS.scribe`. Il ne suffit pas pour coordonner quatre
agents qui modifient du code en parallele.

Il faut ajouter trois couches:

- presence: savoir quels agents sont actifs;
- surface: savoir sur quoi chacun travaille;
- precedence: connaitre les changements deja faits avant d'implementer ou
  d'ecrire dans SCRIBE.

## Identite de session

Chaque agent doit obtenir une identite de session depuis le code, pas la fabriquer a la main. Le format canonique est `<agent-type>-<YYYYMMDD>-<hash12>`, ou `hash12` derive du PID, du hostname et de `time_ns()`. Deux agents lances le meme jour, la meme seconde ou dans le meme terminal doivent quand meme produire deux IDs distincts.

Commande canonique:

```bash
.agent/workflow/scribe/scribe whoami --type cli --surface idle
```

La ligne `Mon ID:` devient le `AGENT_NAME` stable de la session. Ne plus utiliser `codex-YYYYMMDD-01`, car ce format collisionne des que plusieurs agents demarrent en parallele.

## Initialisation obligatoire d'un agent

Au debut d'une session multi-agent, chaque terminal commence dans le pool idle:

```bash
AGENT_TYPE="cli"

.agent/workflow/scribe/scribe bootstrap
.agent/workflow/scribe/scribe whoami --type "$AGENT_TYPE" --surface idle
AGENT_NAME="<Mon ID from scribe whoami>"
.agent/workflow/scribe/scribe workflow read --agent "$AGENT_NAME" --type "$AGENT_TYPE"
.agent/workflow/scribe/scribe workflow check --agent "$AGENT_NAME"
.agent/workflow/scribe/scribe lock status
.agent/workflow/scribe/scribe sync --agent "$AGENT_NAME" --type "$AGENT_TYPE"
.agent/workflow/scribe/scribe coordination status
.agent/workflow/scribe/scribe-rag context
```

Quand une tache concrete arrive, l'agent prend un `coordination claim` semantique. Il ne se fige pas dans un role manuel: le terminal disponible peut enchainer une autre tache apres livraison. Escalader vers `scribe-rag preflight --tier CRITICAL --strict "<plan>"` seulement pour auth/data/API publique, mutation SCRIBE, surface partagee ou coordination a risque.

L'agent doit annoncer:

- son `AGENT_NAME`;
- son statut idle ou son claim semantique courant;
- les surfaces deja modifiees qu'il voit;
- le statut du workflow ack;
- le statut du lock SCRIBE;
- s'il peut travailler sans conflit.

## Presence live

`scribe sync` indique le dernier writer memoire, mais ne donne pas a lui seul le nombre de sessions ouvertes. La presence canonique est maintenant `scribe-out/presence/`, alimentee par `scribe whoami`.

Creation ou heartbeat toutes les 60 secondes:

```bash
.agent/workflow/scribe/scribe whoami --agent "$AGENT_NAME" --type "$AGENT_TYPE" --surface idle
```

Voir les agents actifs et le lock courant:

```bash
.agent/workflow/scribe/scribe whoami --agent "$AGENT_NAME" --type "$AGENT_TYPE" --surface idle --no-register
.agent/workflow/scribe/scribe lock status
```

Regles:

- `scribe-out/presence/` est un etat runtime local, pas du code produit;
- un heartbeat expire apres `ttl_seconds` et un PID mort rend la presence stale;
- les presences stale sont nettoyees au prochain `scribe whoami`;
- `idle`, `none` et `unknown` ne creent pas de conflit; si deux agents declarent la meme surface active non-idle, la fenetre de coordination arbitre avant toute modification.

## Lock release et propriete

Depuis la stabilisation 2026-06-01, `lock release` ne supprime plus aveuglement
un stale lock appartenant a un autre agent. Il lit le lock brut, verifie agent et
surface, puis nettoie seulement son propre stale lock. Pour une session longue,
faire porter le lock par le processus proprietaire reel via `SCRIBE_OWNER_PID` ou
`--owner-pid`; sinon le lock court peut devenir stale apres la fin de la commande
et sera nettoye au prochain appel.

## Claim de surface

Une surface est une zone large de responsabilite temporaire. Pour les fichiers partages, elle ne suffit pas: chaque tache doit aussi prendre un claim semantique. Exemples:

- `frontend`
- `tests`
- `scribe`
- `auth`
- `websocket`

Avant d'implementer:

```bash
.agent/workflow/scribe/scribe worktree --surface <surface> --agent "$AGENT_NAME" --limit 80
```

Si la commande signale `SURFACE_VIOLATION`, l'agent stoppe et demande coordination. Il ne corrige pas par-dessus un autre agent.

## Claims semantiques et fichiers partages

Le workflow multi-agent n interdit pas a deux agents de modifier le meme fichier. Il interdit de modifier le meme fichier avec une vision mentale obsolete. Les fichiers comme `IndicatorsModal.tsx`, `TechnicalIndicators.ts` ou `indicators.worker.ts` sont des surfaces partagees normales: plusieurs agents peuvent y ajouter des indicateurs differents en parallele.

Regle canonique:

- meme fichier partage: autorise, avec relecture et rebase obligatoire avant livraison;
- meme entite metier: conflit, un seul'agent a la fois;
- meme fonction exacte: conflit fort;
- suppression, renommage ou refactor global: coordination obligatoire avant changement;
- ecriture aveugle d une ancienne version du fichier: interdite.

Avant une tache, l'agent declare une intention semantique, pas seulement un fichier:

```bash
.agent/workflow/scribe/scribe coordination claim \
  --agent "$AGENT_NAME" \
  --claim "indicator:X" \
  --task "implement indicator X" \
  --expected-file "components/technical-analysis/components/modals/IndicatorsModal.tsx" \
  --expected-file "components/technical-analysis/lib/Indicators/TechnicalIndicators.ts" \
  --expected-file "components/technical-analysis/lib/workers/indicators.worker.ts"
```

Si un autre agent a un claim different mais les memes fichiers probables, ce n est pas un blocage. La commande signale `shared_files_detected: yes` et impose: relire les fichiers courants, fusionner les ajouts des autres, puis valider la coexistence.

Si un autre agent a deja le meme claim semantique, par exemple `indicator:X`, la commande refuse. L'agent doit changer de tache ou attendre.

A la fin de la tache:

```bash
.agent/workflow/scribe/scribe coordination finish \
  --agent "$AGENT_NAME" \
  --claim "indicator:X" \
  --summary "implemented indicator X" \
  --changed-file "components/technical-analysis/lib/Indicators/TechnicalIndicators.ts"
```

Les evenements vont dans `scribe-out/coordination/events.jsonl`. Ils servent a synchroniser les agents entre eux; ils ne remplacent pas le SCRIBE, qui reste reserve a la memoire causale durable.

## Avant implementation

Checklist obligatoire:

```bash
.agent/workflow/scribe/scribe workflow check --agent "$AGENT_NAME"
.agent/workflow/scribe/scribe lock status
.agent/workflow/scribe/scribe sync --agent "$AGENT_NAME" --type "$AGENT_TYPE"
.agent/workflow/scribe/scribe worktree --surface <surface> --agent "$AGENT_NAME" --limit 80
.agent/workflow/scribe/scribe coordination status
.agent/workflow/scribe/scribe-rag preflight --tier CRITICAL --strict "<plan>"  # only if risk tier is CRITICAL
```

L'agent doit lire les changements visibles dans `worktree` et les claims actifs avant d'ecrire. Si le meme fichier est partage par un claim semantique different, il continue seulement apres relecture de la version courante et rebase explicite. Si le meme claim semantique, la meme fonction exacte, une suppression ou un refactor global est en cours, il stoppe.

## Avant ecriture SCRIBE

Avant de modifier `AGENT-MEMOIRE_PROJECT_STATUS.scribe`, l'agent doit connaitre
les changements deja presents et verifier que la memoire n'a pas evolue depuis
son dernier sync.

Sequence obligatoire:

```bash
.agent/workflow/scribe/scribe workflow check --agent "$AGENT_NAME"
.agent/workflow/scribe/scribe lock status
.agent/workflow/scribe/scribe lock acquire --agent "$AGENT_NAME" --type "$AGENT_TYPE" --session "<JOURNAL-ID>"
.agent/workflow/scribe/scribe sync --agent "$AGENT_NAME" --type "$AGENT_TYPE"
.agent/workflow/scribe/scribe doctor --suggest-fix
.agent/workflow/scribe/scribe worktree --surface scribe --agent "$AGENT_NAME" --limit 80
```

Puis seulement:

1. relire le dernier etat SCRIBE pertinent;
2. integrer les changements faits par les agents precedents;
3. ecrire uniquement en append incremental;
4. relancer doctor;
5. sync repair;
6. release lock.

Fin:

```bash
.agent/workflow/scribe/scribe doctor --suggest-fix
.agent/workflow/scribe/scribe sync --repair --agent "$AGENT_NAME" --type "$AGENT_TYPE" --session "<JOURNAL-ID>" --changed-id "<JOURNAL-ID>" --write-kind memory_append
.agent/workflow/scribe/scribe lock release --agent "$AGENT_NAME"
```

## Regle de precedence

Un agent ne doit jamais ecrire dans SCRIBE comme si son travail etait seul au
monde. Avant son append memoire, il doit verifier:

- qui tient le lock;
- quels fichiers ont change;
- si `AGENT-MEMOIRE_PROJECT_STATUS.scribe` a deja ete modifie;
- si un journal recent couvre deja le meme sujet;
- si son delta contredit une entree precedente.

Si oui, il adapte son entree ou demande coordination.

## Fenetre de coordination et integration

Aucun terminal ne recoit un role permanent au demarrage. Les quatre terminaux restent interchangeables: celui qui termine peut reprendre une autre tache.

La coordination est un role temporaire tenu par la fenetre qui integre les deltas a un instant donne.

Responsabilites temporaires:

- verifier `.agent/workflow/scribe/scribe workflow status --strict` pour voir le pool reel des agents ackes;
- utiliser `--required ... --strict` seulement si une liste nommee est explicitement imposee;
- lire `coordination status` et les claims actifs avant toute attribution de travail;
- arbitrer les conflits de claim semantique, de fonction exacte, de suppression, de renommage ou de refactor global;
- demander aux agents de relire/rebase les fichiers partages avant livraison;
- decider quel delta SCRIBE est grave;
- garder les fichiers generes hors du commit produit.

## Red flags

Stop immediat si:

- `scribe workflow check --agent "$AGENT_NAME"` retourne ACK_REQUIRED ou ACK_STALE;
- `scribe lock status` montre un autre agent actif sur SCRIBE;
- `worktree --surface` retourne `SURFACE_VIOLATION`;
- le fichier cible a change depuis la lecture initiale et l'agent n a pas encore relu puis rebased;
- `AGENT-MEMOIRE_PROJECT_STATUS.scribe` a change depuis le dernier sync;
- l'agent ne sait pas si son information est plus recente que celle d'un autre;
- deux agents corrigent le meme symptome avec deux strategies differentes;
- un agent tente de livrer sans verifier `scribe coordination status` et les claims semantiques actifs.

## Formule courte

Multi-agent SCRIBE = identite unique + presence idle/working + claims semantiques + fichiers partages autorises avec rebase + events runtime + sync/worktree avant travail + lock avant memoire + precedence avant append.

