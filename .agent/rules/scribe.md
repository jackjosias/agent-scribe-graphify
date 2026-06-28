---
trigger: always_on
---

# SCRIBE — REGLE ALWAYS-ON

Ce fichier est une regle courte pour les LLM hotes. Il ne remplace pas le
protocole complet.

## Source canonique

- Racine portable unique: `.agent/workflow/scribe/`
- CLI maintenance interne: `.agent/workflow/scribe/scribe`
- CLI lecture agent: `.agent/workflow/scribe/scribe-rag`
- Protocole complet: `.agent/workflow/scribe/sel/docs/scribe.md`
- Regles locales: `.agent/workflow/scribe/sel/docs/AGENTS.md`
- Politique de friction: `.agent/workflow/scribe/sel/docs/friction-policy.md`
- Installation multi-agent: `.agent/workflow/scribe/sel/docs/multi-agent-installation.md`
- Coordination live: `.agent/workflow/scribe/sel/docs/live-coordination.md`
- Skill init: `.agent/skills/init-tenor/SKILL.md`

Tout chemin SCRIBE hors de `.agent/workflow/scribe/` est non canonique. Ne pas
creer de dossier de compatibilite visible; corriger les anciens appels vers les
commandes ci-dessus.

## Etat stabilise 2026-06-01

Le bundle est stable et ne doit plus etre perfectionne hors bug reel:

- SEL: tests verts (`81 OK` apres les tests lock ownership).
- RAG: tests verts (`25 OK`).
- Gate/eval: `.agent/workflow/scribe/scribe-rag gate` vert a `8/8`.
- Doctor: `0 error`; `W009` legacy pre-V3.2 reste cosmetique.
- Identite/presence: `os.getpid()` utilise, stale PID nettoye, IDs/PIDs simultanes distincts.
- Coordination: claims avec `ttl_seconds` et `expires_at`; claims expires ou sans TTL nettoyes.
- Lock: `release` verifie agent/surface avant de nettoyer un stale lock; utiliser `SCRIBE_OWNER_PID` ou `--owner-pid` pour representer un processus proprietaire long-vivant.
- Backup de reference: `~/backups/agent-scribe-stable-20260601.tar.gz`.
- Ratio causal mesure: environ `17.5%`, cible `35%`; ne jamais creer de SCAR/GHOST/PAT cosmetique pour gonfler ce ratio.
- Reflexe douleur reelle: bug resolu en plus de 2 tentatives, regression, rollback couteux ou smoke visuel casse => SCAR immediat avec `cause_racine`, `resolution` et `test_binding`; avant une tache proche, interroger `scribe-rag query/explain/challenge` pour exploiter ces cicatrices.

Instruction operationnelle: STOP `.agent`. Revenir au vrai projet. Ne rouvrir
le chantier SCRIBE que pour un bug SCRIBE observe, un test rouge, ou une derive
documentaire concrete.

## Reflexe de demarrage par tier

Depuis la racine du projet:

```bash
.agent/workflow/scribe/scribe bootstrap
```

`bootstrap` est idempotent et initialise seulement ce qui manque:
`AGENT-MEMOIRE_PROJECT_STATUS.scribe`, `.agent/state/outputs/scribe-out/`, `state.json`,
`.graphifyignore` et le bloc gere de `AGENTS.md`. Il ne lance aucun daemon.

Mode NANO, correction < 30 min, 1 fichier, sans surface partagee:

```bash
.agent/workflow/scribe/scribe-rag context
```

Mode STANDARD, changement significatif:

```bash
.agent/workflow/scribe/scribe-rag build
.agent/workflow/scribe/scribe-rag context
.agent/workflow/scribe/scribe-rag challenge "<plan>"
```

Mode CRITICAL ou mutation SCRIBE/surface partagee:

```bash
.agent/workflow/scribe/scribe workflow read --agent <name> --type <extension|cli|api|unknown>
.agent/workflow/scribe/scribe workflow check --agent <name>
.agent/workflow/scribe/scribe sync --agent <name> --type <extension|cli|api|unknown>
.agent/workflow/scribe/scribe-rag preflight --tier CRITICAL --strict "<objectif ou plan de session>"
```

`workflow read` calcule le SHA du workflow canonique et enregistre l'ack dans
`.agent/state/outputs/scribe-out/workflow-acks.json`. `workflow check` doit etre vert avant toute
mutation SCRIBE ou surface partagee.

En mode 4 terminaux, ne pas imposer de noms fixes. Chaque terminal demarre en
presence `idle`, obtient son ID via `scribe whoami`, lit `coordination status`,
puis prend un `coordination claim` seulement quand une tache concrete arrive.
`workflow status` affiche le pool reel; `--required ... --strict` sert uniquement
si une liste nommee est imposee.

```bash
.agent/workflow/scribe/scribe whoami --type cli --surface idle
.agent/workflow/scribe/scribe coordination status
.agent/workflow/scribe/scribe workflow status
```

Preuve minimale attendue dans chaque reponse de travail non triviale:
`SCRIBE_RAG_PROOF: preflight <tier> | eval X/8 | challenge <VERDICT>`.
Si `preflight` signale `HYBRID_REQUIRED`, reconstruire avec
`scribe-rag build --with-embeddings --force` ou justifier explicitement le
maintien BM25.

## Dashboard SCRIBE

```bash
.agent/workflow/scribe/scribe dashboard
.agent/workflow/scribe/scribe dashboard --serve --host 127.0.0.1 --port 8765
```

`dashboard` genere un HTML statique dans `.agent/state/outputs/scribe-out/`. `dashboard --serve`
lance a la demande un serveur HTTP local leger (`ThreadingHTTPServer`) pour vue
live/reload; ce processus s'arrete avec Ctrl+C et n'est jamais demarre par
`bootstrap`.

## Reflexe avant mutation SCRIBE

Avant toute commande qui modifie la memoire:

```bash
.agent/workflow/scribe/scribe workflow check --agent <name>
.agent/workflow/scribe/scribe doctor --suggest-fix
.agent/workflow/scribe/scribe lock acquire --agent <name> --type <extension|cli|api|unknown> --session <JOURNAL-ID>
```

Apres validation:

```bash
.agent/workflow/scribe/scribe doctor --suggest-fix
.agent/workflow/scribe/scribe sync --repair --agent <name> --type <extension|cli|api|unknown> --session <JOURNAL-ID>
.agent/workflow/scribe/scribe lock release --agent <name>
```

`lock acquire` refuse un agent sans ack workflow frais. Utiliser `--surface <nom>`
pour reserver une surface partagee non-SCRIBE avec le meme garde-fou. `lock release`
ne supprime plus le stale lock d'un autre agent: il verifie agent et surface avant
nettoyage.

Les commandes SEL read-only de maintenance (`explain`, `related`, `stats`, `doctor`)
ne doivent pas etre bloquees par le lock. Pour le retrieval agent, ne pas appeler
`scribe context` ni `scribe query` directement; utiliser `scribe-rag preflight`, puis
`scribe-rag query/explain/challenge` selon le besoin.

## Separation Graphify / SCRIBE

- Graphify = structure du code: quoi, ou, comment.
- SCRIBE = causalite: pourquoi, douleur, decision, cicatrice.

Ne pas ecrire dans SCRIBE ce que Graphify peut deduire du code. Ecrire un
SCAR ou un GHOST seulement si l'information evitera une vraie souffrance au
prochain agent.

## Hygiene Git / push

- Scope par defaut: le code produit du projet hote.
- `AGENT-MEMOIRE_PROJECT_STATUS.scribe`: a versionner seulement si l'equipe
  veut partager la memoire causale entre agents et humains.
- `.agent/state/outputs/graphify-out/`: ne pas versionner par defaut; c'est un graphe genere,
  reconstructible avec `graphify update .`.
- `.agent/state/outputs/scribe-out/`: ne pas versionner par defaut; c'est de l'etat runtime local
  (index, locks, rapports, dashboards, exports, sync metadata).
- `.agent/`: a versionner seulement quand l'equipe maintient volontairement
  l'outillage agentique; sinon le garder hors des commits produit.

Avant livraison, utiliser `.agent/workflow/scribe/scribe worktree` pour separer source
reelle, memoire causale validee, tooling volontaire et bruit genere. Quand le bundle
SCRIBE/RAG change, lancer aussi `.agent/workflow/scribe/scribe-rag gate`; le gate doit rester a 8/8
pour CI/pre-commit.

## Intention finale obligatoire

Avant de fermer une vraie session de coding, poser cette question:

> "Qu'est-ce qui fera souffrir le prochain LLM si je ne le documente pas ?"

Si la reponse est une douleur concrete, la graver en SCAR ou GHOST. Sinon, le
JOURNAL suffit.
