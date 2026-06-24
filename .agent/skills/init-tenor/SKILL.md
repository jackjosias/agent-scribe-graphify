---
name: init-tenor
description: >
  Initialiser une session TENOR/SCRIBE/GRAPHIFY.
  LECTURE OBLIGATOIRE ET PROUVÉE avant toute action.
  Un LLM qui ne prouve pas sa lecture est interdit d'agir sur le code.
---

## 🔴 TENOR INIT V2 — RACCOURCI UNIQUE

Si l'utilisateur écrit seulement :

```text
[[.agent/skills/init-tenor/SKILL.md]]
```

cela équivaut à :

```text
TENOR INIT V2
```

Après avoir lu ce fichier depuis la ligne 1, l'agent doit immédiatement lire et appliquer le protocole machine :

```text
.agent/rules/tenor-init-v2.json
```

Ce protocole définit :

* le MCP obligatoire `agent-scribe-graphify`
* le bootstrap via `workflow_next`
* l'initialisation `.agent/workflow/scribe/scribe tenor-init --type cli`
* la vérification `.scribe`
* la vérification `scribe-out/`
* la vérification `graphify-out/`
* les tools obligatoires `workflow_next`, `apply_patch`, `delete_resource`
* la boucle de lecture/recherche SCRIBE/Graphify
* la boucle d'écriture via `apply_patch`
* la boucle de suppression via `delete_resource`
* les règles multi-agent avec `claim_resource`
* le protocole V2 exige que `workflow_next` pilote aussi SCRIBE ciblé, Graphify ciblé impact/structure et `scribe_record` typé; le host ne décide pas seul quand les appeler

L'utilisateur ne doit pas recopier un long prompt. Cette ligne suffit :

```text
[[.agent/skills/init-tenor/SKILL.md]]
```


# 🔴 TU LIS CE FICHIER. C'EST LA PREMIÈRE CHOSE QUE TU FAIS.

Tu viens de recevoir `TENOR INIT` ou un signal d'initialisation.
Ton premier réflexe : LIRE CE FICHIER.
Si le prompt contient `TENOR INIT::[.agent/skills/init-tenor/SKILL.md]`,
le premier fichier projet lu DOIT être exactement `.agent/skills/init-tenor/SKILL.md`.
Pas de config globale OpenCode/Codex/Gemini avant. Pas `~/.config/.../AGENTS.md`.
Pas Graphify. Pas README. Pas SCRIBE. **Ce fichier d'abord.**

Ce skill DOIT lancer l'initialisation machine à chaque TENOR INIT avant
de considérer la session valide. Ne cherche pas à deviner si `.agent/` vient
d'être copié: la commande est idempotente, donc elle est le test et la
réparation portable. Elle initialise ou vérifie le SCRIBE projet, `scribe-out/`,
`.agent/rules/scribe.md`, le bloc `AGENTS.md`, `.graphifyignore` et les
surfaces portables attendues.

## 🔴 HOST MCP PREFLIGHT — AVANT TENOR-INIT

Avant de lancer `tenor-init`, l'agent doit vérifier le host courant. Une init
filesystem par shell direct n'est pas une init MCP conforme. Le serveur local
peut exister sans que ses tools soient visibles dans l'interface LLM du host.

### 1. Identifier le host

Classer l'environnement courant dans une seule catégorie :

* Codex CLI
* OpenCode
* Claude Code
* VS Code / Copilot
* Cursor
* Cline
* Kilo Code
* Roo Code
* Windsurf
* autre / unknown

### 1B. HOST ADAPTER RESOLUTION — MULTI-HOST

Après détection du host, l'agent doit résoudre une fiche host dans
`.agent/docs/hosts/`. `.agent` ne hardcode pas une stratégie OpenCode comme
stratégie globale: `.agent` définit les invariants universels, puis chaque
host applique son adaptateur documentaire.

Mapping indicatif :

* OpenCode → `.agent/docs/hosts/OPENCODE_MCP.md`
* Codex CLI → `.agent/docs/hosts/OPENAI_CODEX_MCP.md` ou `CODEX_CLI_MCP.md`
* Claude Code → `.agent/docs/hosts/CLAUDE_CODE_MCP.md`
* Cursor → `.agent/docs/hosts/CURSOR_MCP.md`
* Gemini CLI → `.agent/docs/hosts/GEMINI_CLI_MCP.md`
* VS Code / Copilot → `.agent/docs/hosts/VSCODE_COPILOT_MCP.md`
* Cline → `.agent/docs/hosts/CLINE_MCP.md`
* Roo Code → `.agent/docs/hosts/ROO_CODE_MCP.md`
* Kilo Code → `.agent/docs/hosts/KCODE_MCP.md`
* Windsurf → `.agent/docs/hosts/WINDSURF_MCP.md`
* unknown → `.agent/docs/hosts/README.md` + `AGENT_MCP_INSTALL_MATRIX.md`

Règles :

* Ne pas appliquer une stratégie OpenCode à Cursor.
* Ne pas appliquer une stratégie Cursor à Gemini CLI.
* Ne pas inventer un fichier de config.
* Ne pas écrire dans une config globale/user sans que la fiche host le
  recommande explicitement et sans permission utilisateur.
* Ne pas utiliser de chemin absolu figé vers un ancien projet.
* Toujours préférer les mécanismes project/workspace-local quand le host les
  supporte.
* Si la fiche host est incomplète ou non confirmée : statut
  `HOST_GUIDE_INCOMPLETE` et demander clarification.

### 2. Vérifier deux états séparés

**MCP local server** : vérifier que le serveur local existe avec :

```bash
python3 .agent/mcp/server_entry.py --list-tools
```

Cette commande prouve seulement que `.agent/mcp/server_entry.py` fonctionne.
Elle ne prouve PAS que les tools MCP `.agent` sont exposés au LLM host.

**MCP tools visibles au LLM host** : vérifier dans l'interface réelle du host
que ces tools sont directement appelables par le modèle :

```text
workflow_next
before_task
scribe_query
graphify_query
propose_patch
apply_patch
delete_resource
finish_task
```

Ne jamais confondre ces deux états. `server_entry.py --list-tools` OK signifie
`MCP local server: OK`, pas `MCP tools visibles au LLM: YES`.

### 3. Si les tools MCP `.agent` ne sont pas visibles au LLM host

STOP avant de déclarer l'init conforme. `TENOR INIT` seul ne vaut PAS
permission explicite de bootstrap filesystem quand le host est UNSAFE.

Par défaut, si `MCP tools visibles au LLM: NO`, la seule sortie autorisée est :

```text
HOST_MCP_UNBOUND
Init status: INIT_BLOCKED_HOST_MCP_UNBOUND
```

L'agent doit :

1. Lire `.agent/docs/hosts/AGENT_MCP_INSTALL_MATRIX.md`.
2. Lire la fiche host correspondante dans `.agent/docs/hosts/`.
3. Afficher `HOST_MCP_UNBOUND`.
4. Afficher `Verdict host: UNSAFE`.
5. Demander permission à l'utilisateur pour configurer le MCP dans le host.
6. Dire explicitement qu'un redémarrage du host peut être nécessaire.
7. Demander exactement :

```text
Veux-tu que je configure le MCP .agent dans ce host ?
Ou veux-tu explicitement un bootstrap filesystem malgré host UNSAFE ?
```

8. Ne pas déclarer `TENOR INIT terminé`.
9. Ne pas lancer `.agent/workflow/scribe/scribe bootstrap`.
10. Ne pas lancer `.agent/workflow/scribe/scribe tenor-init --type cli`.
11. Ne pas autoriser les écritures projet sauf ordre explicite.

Le bootstrap filesystem malgré host UNSAFE est autorisé uniquement si
l'utilisateur répond clairement avec une phrase équivalente à :

```text
oui, bootstrap filesystem malgré host UNSAFE
```

Dans ce cas seulement, l'agent peut lancer `tenor-init`, mais le statut final
doit être :

```text
FILESYSTEM_INIT_OK_MCP_UNBOUND
```

Il est interdit d'afficher `INIT CONFORME` ou `TENOR INIT terminé` sans préciser
que le MCP `.agent` n'est pas lié au host.

### 4. HOST MCP ROOT BINDING CHECK — TOUS HOSTS

Aucune configuration host ne doit être considérée valide uniquement parce
qu'elle expose les tools MCP. La preuve finale est le root binding check.

Statuts universels :

```text
MCP_VISIBLE
MCP_UNBOUND
MCP_WRONG_ROOT
MCP_BOUND_TO_CURRENT_PROJECT
```

`MCP_BOUND_TO_CURRENT_PROJECT` signifie : le MCP visible au LLM résout les
fichiers dans la même racine projet que le workspace courant du host.

Vérifier côté host un fichier sentinelle avec Python, méthode portable
Linux/macOS/Windows :

```bash
python3 - <<'PY'
from pathlib import Path
import hashlib
candidates = [
    Path("AGENTS.md"),
    Path(".agent/skills/init-tenor/SKILL.md"),
    Path(".agent/rules/tenor-init-v2.json"),
]
for p in candidates:
    if p.exists() and p.is_file():
        print(str(p))
        print(hashlib.sha256(p.read_bytes()).hexdigest())
        break
else:
    print("__NO_SENTINEL__")
PY
```

Comparer ensuite avec le tool MCP visible au LLM host :

```text
file_hash resource=<sentinel_file>
```

Si le hash côté host/shell et le hash côté MCP ne correspondent pas, afficher :

```text
MCP_WRONG_ROOT
Init status: INIT_BLOCKED_MCP_WRONG_ROOT
```

Si le tool MCP retourne `__new_file__`, fichier absent, ou une erreur pour un
fichier sentinelle existant côté host, afficher aussi `MCP_WRONG_ROOT`.

Dans l'état `MCP_WRONG_ROOT`, l'agent doit STOPPER, lire la fiche host
correspondante dans `.agent/docs/hosts/`, demander permission de corriger le
binding MCP du host, prévenir qu'un redémarrage peut être nécessaire, et ne
pas lancer :

```bash
.agent/workflow/scribe/scribe bootstrap
.agent/workflow/scribe/scribe tenor-init --type cli
```

Pour corriger la configuration, rester générique : lire la fiche host
correspondante et appliquer la stratégie de ce host. Ne pas inventer une
configuration universelle.

### 4B. DIRECT TOOL NEUTRALIZATION — VERS SAFE

Tant que `Direct shell/write/edit: YES` existe sans permission stricte ni
sandbox, le verdict ne peut pas être `SAFE`. Le meilleur statut autorisé est
`ACCEPTABLE`, même si le MCP est visible et bound au bon projet.

Statuts de sécurité :

```text
UNSAFE: MCP absent ou non visible, mauvais root, ou écritures directes libres.
ACCEPTABLE: MCP visible + root bound + workflow MCP utilisé, mais shell/write/edit directs encore accessibles.
SAFE_CANDIDATE: MCP visible + root bound, write/edit natifs désactivés ou en permission ask stricte, shell lecture autorisé mais écritures bloquées ou demandent approbation.
SAFE: MCP visible + root bound, aucun chemin d’écriture projet hors MCP, ou sandbox vérifiée empêchant les écritures directes hors MCP.
```

Défense en couches :

1. Host permissions: désactiver ou mettre en ask/deny les tools natifs
   dangereux quand le host le permet.
2. Project-local host config: chaque projet porte sa config host locale si le
   host le supporte.
3. OS sandbox: lancer le host dans un sandbox qui limite les écritures directes
   quand c’est possible.
4. MCP workflow gate: toute écriture légitime passe par
   `propose_patch`, `apply_patch` ou `delete_resource`.
5. Dirty-write detector: avant et après tâche, comparer `git status`, file
   hashes et patch logs. Si un fichier change sans trace MCP, déclarer
   `DIRECT_WRITE_BYPASS_DETECTED`.
6. Policy: si bypass détecté, STOP, rapporter les fichiers touchés, demander
   rollback ou validation utilisateur explicite.

Les redirections shell (`>`, `>>`, `tee`), `sed -i`, `perl -pi`, scripts
Python/Node/Bash qui écrivent, `rm`, `mv`, `cp`, apply_patch natif et
write/edit natifs du host sont des chemins directs. Ils empêchent `SAFE` sauf
s’ils sont bloqués, sandboxés, ou soumis à permission stricte vérifiée.

### 5. Si les tools MCP `.agent` sont visibles ET bound au projet courant

Continuer l'init normale seulement avec le statut :

```text
MCP_BOUND_TO_CURRENT_PROJECT
```

Puis lancer :

```bash
.agent/workflow/scribe/scribe tenor-init --type cli
```

Copier la sortie brute complète et vérifier les 4 champs :

```text
Agent session
Whoami proof
Workflow ack
Status init
```

### 6. Bloc final obligatoire

Toute réponse d'init doit inclure ces champs avec des valeurs réelles :

```text
Host détecté: Codex CLI / OpenCode / Claude Code / VS Code / Cursor / Cline / Kilo Code / Roo Code / Windsurf / autre / unknown
MCP local server: OK / FAIL
MCP tools visibles au LLM: YES / NO
MCP root binding: MCP_BOUND_TO_CURRENT_PROJECT / MCP_WRONG_ROOT / N/A
Host guide lu: .agent/docs/hosts/<FICHE>.md / N/A
Direct shell/write/edit: YES / NO / UNKNOWN
Verdict host: SAFE / SAFE_CANDIDATE / ACCEPTABLE / UNSAFE / UNKNOWN
Init status: INIT_CONFORME / FILESYSTEM_INIT_OK_MCP_UNBOUND / INIT_BLOCKED_HOST_MCP_UNBOUND / INIT_BLOCKED_MCP_WRONG_ROOT
```

`INIT_CONFORME` est autorisé uniquement si les tools MCP `.agent` sont visibles
au LLM host et si `tenor-init` valide les champs machine requis.

## 🔴 CHEMIN OBLIGATOIRE POUR PETITS MODÈLES

Si tu es un petit modèle, un modèle rapide, un agent avec contexte limité, ou
si tu n'es pas absolument certain de pouvoir suivre toutes les étapes sans
inventer: **NE RECONSTRUIS PAS L'INIT À LA MAIN.**

Lance exactement cette commande depuis la racine projet et colle sa sortie:

```bash
.agent/workflow/scribe/scribe tenor-init --type cli
```

Si tu es une extension, remplace seulement le type:

```bash
.agent/workflow/scribe/scribe tenor-init --type extension
```

La sortie `📋 SCRIBE-CHECK TENOR V4 — MACHINE PROOF` devient la preuve
d'initialisation. Si cette commande échoue, tu STOP et tu montres l'erreur.
Tu n'as pas le droit de compenser avec un TENOR_HEADER inventé.
Tu n'as pas le droit de remplacer cette sortie par un résumé. Tu dois afficher
au minimum les lignes exactes `Agent session`, `Whoami proof`, `Workflow ack`
et le bloc `PREUVES COMMANDES` contenant `whoami: rc=0` / `SCRIBE WHOAMI`.
Si l'interface replie la sortie, tu recopies explicitement ces lignes critiques
après le bloc, sans les reformuler.

Si `tenor-init` n'existe pas dans un vieux bundle, fallback strict:
`.agent/workflow/scribe/scribe bootstrap`, puis `whoami`, `workflow read`,
`workflow check`, `scribe-rag context`. Mais sur ce bundle, `tenor-init` est
la voie canonique.

Si tu es un petit modèle : tu ne lis que les 100 premières lignes.
**Ça suffit. Mais tu dois les lire en entier. Ne défile pas.**

Tant que tu n'as pas produit le bloc ci-dessous
(format exact, caractère pour caractère, avec données réelles),
**tu ne touches à AUCUN fichier. Tu ne réponds à AUCUNE question.**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 SCRIBE-CHECK TENOR V4
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Date session         : [date réelle système]
Heure système        : [heure réelle système]
TENOR global lu      : OUI — version [X]
                       Invariant 1 : [texte exact INV-F001]
                       Invariant 2 : [texte exact INV-F002]
                       Invariant 3 : [texte exact INV-F003]
Graphify lu          : OUI — [N] nodes [N] edges [N] communautés
God-nodes            : [nom1(N edges), nom2(N edges), nom3(N edges)]
Blast radius node 1  : [N] edges impactés ([nom exact])
Agent session        : [AGENT_NAME réel généré par scribe whoami]
Lock status          : [unlocked/locked — valeur réelle]
Last writer          : [ID réel depuis state.json]
Claims actifs        : [N réel — détails ou "Aucun"]
Agents actifs vus    : [N réel — IDs ou "Aucun"]
Mémoire projet lue   : OUI — [N] entités [N] lignes SCRIBE
Hot entries          : [liste réelle depuis scribe-rag context]
Dettes actives       : [liste réelle ou "Aucune"]
Dernier JOURNAL      : [ID réel depuis scribe-rag query]
SCARs HOT            : [Oui/Non] — [premier SCAR l0_abstract réel]
ne_pas_reproposer    : [Oui/Non] — [liste réelle ou "Aucun"]
Mode actif           : NANO / QUICK / STANDARD / CRITICAL
Justification mode   : [1 phrase expliquant pourquoi ce mode]
Workflow ack         : ACK_OK / NON
MCP Chrome lu        : OUI / NON CONCERNÉ
Validation nav.      : [N/A ou détail réel]
Prochaine action     : [description précise de ce que tu vas faire]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Si un seul champ contient [texte] ou [valeur] → STOP.**
**Efface ta réponse. Recommence depuis ÉTAPE 0.**
**Tu ne produis ce bloc qu'avec des données réelles.**

---

# ⛔ RÈGLE TENOR-INIT — NON-NÉGOCIABLE

## LA SEULE COMMANDE D'INITIALISATION

```bash
.agent/workflow/scribe/scribe tenor-init --type cli
```

Cette commande remplace les étapes 3→8. Tu n'as **PAS** le droit de les faire
manuellement. Elle est idempotente — lance-la sans condition à chaque TENOR INIT.

Si tu es une extension IDE (VS Code, Cursor, etc.), remplace uniquement le type :

```bash
.agent/workflow/scribe/scribe tenor-init --type extension
```

## CE QUE TU DOIS FAIRE AVEC LA SORTIE

**1. COPIER LA SORTIE COMPLÈTE dans ta réponse.**

Pas un résumé. Pas une reformulation. Pas un abrégé. La sortie brute intégralement.

**2. VÉRIFIER que la sortie contient ces 4 champs :**

```
Agent session        : [ID réel — pas un placeholder]
Whoami proof         : OK
Workflow ack         : ACK_OK
Status init          : VALID
```

**3. Si UN seul de ces 4 champs manque ou n'est pas valide :**
→ STOP. Affiche l'erreur complète réelle.
→ Informe l'utilisateur avec le message exact de l'erreur.
→ Ne continue pas tant que ces 4 champs ne sont pas confirmés.

**4. PUIS produire le SCRIBE-CHECK V4** en remplissant chaque champ depuis la
sortie `tenor-init`. Pas depuis ta mémoire. Pas depuis tes suppositions.
Depuis la sortie réelle affichée juste au-dessus.

## CE QUI INVALIDE L'INIT IMMÉDIATEMENT

```
❌ "Résumé rapide : ..." au lieu de la sortie brute
❌ "J'ai lancé tenor-init, voici ce que j'ai trouvé..."
❌ Absence de "Agent session : [ID réel]" dans la sortie collée
❌ Absence de "Whoami proof  : OK" dans la sortie collée
❌ "Workflow ack : NON" ou absence de ce champ
❌ "Status init  : INVALID" ou absence de ce champ
❌ SCRIBE-CHECK V4 rempli avec des [placeholders] ou des suppositions
❌ Tout résumé qui remplace ou abrège la sortie brute tenor-init
❌ Produire le SCRIBE-CHECK V4 SANS avoir affiché la sortie tenor-init d'abord
```

## EXEMPLE EXACT DE CE QUI EST ACCEPTÉ

```
$ .agent/workflow/scribe/scribe tenor-init --type cli
[SORTIE BRUTE COMPLÈTE — copiée caractère pour caractère]
...
Agent session        : cli-20260617-a3f9c1
Whoami proof         : OK
Workflow ack         : ACK_OK
Status init          : VALID
...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 SCRIBE-CHECK TENOR V4 — MACHINE PROOF
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[rempli depuis la sortie tenor-init ci-dessus — données réelles uniquement]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## POURQUOI CETTE RÈGLE EXISTE

Les règles textuelles ne contraignent pas les LLMs. Ils les lisent, les comprennent,
et les ignorent quand résumer est plus simple. La seule défense : rendre la preuve
mécanique et non-falsifiable. `tenor-init` produit une sortie machine. Cette sortie
existe ou n'existe pas. Elle contient les 4 champs ou elle ne les contient pas.
Il est impossible de l'inventer sans la lancer réellement.

**La bataille contre les LLMs qui résument se gagne par des commandes machines,
pas par des mots.**

---

# 🔴 MINIMUM VIABLE INIT — PETITS MODÈLES (contexte < 8K)

Si ton contexte est trop serré, tu peux condenser en 5 étapes :

1. Lire ce fichier `.agent/skills/init-tenor/SKILL.md` depuis la ligne 1.
2. Exécuter exactement : `.agent/workflow/scribe/scribe tenor-init --type cli`.
3. Coller la sortie `SCRIBE-CHECK TENOR V4 — MACHINE PROOF` sans la réécrire.
4. Recopier explicitement `Agent session`, `Whoami proof`, `Workflow ack` et le bloc `whoami: rc=0` si l'UI replie la sortie.
5. Si la commande échoue, STOP et afficher l'erreur réelle. Ne jamais remplacer cette preuve par un TENOR_HEADER manuel ni par un résumé.

**🔴 MAIS :** tu lis ce SKILL.md en premier. Toujours.
Même avec 4K de contexte, tu commences par lire ces 80 premières lignes.
Pas de raccourci. Jamais.

**🔴 PROTOCOLE DE LECTURE pour GEMINI.md et les fichiers longs :**
Ne fais PAS `grep "INV-F"` ou `cat | head -30`.
Lis depuis le premier caractère.
Si trop long → par blocs avec confirmation :
`BLOC [N] lu — dernière ligne : "...texte exact..."`
La version + 3 invariants ne sont pas des informations à extraire —
c'est la **PREUVE** que tu as lu le fichier.
Extraction sans lecture = mensonge = session invalide.

---

# ⛔ LES 8 ERREURS FATALES

```
ERREUR 0 — Lire une config globale avant le chemin TENOR INIT
  Tu reçois TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
  et tu lis /home/.../.config/*/AGENTS.md, ~/.gemini/GEMINI.md,
  Graphify, README ou SCRIBE avant ce fichier.
  → STOP. Session invalide. Relis CE fichier depuis la ligne 1.

ERREUR 1 — Inventer un format
  Tu produis TENOR_HEADER avec JURY/STYX/BUDGET
  au lieu du SCRIBE-CHECK ci-dessus.
  → STOP. Efface. Recommence depuis ÉTAPE 0.
  Le format est celui du bloc ci-dessus. Point.

ERREUR 2 — Sauter une étape
  Tu penses que GEMINI.md est optionnel.
  Que Graphify peut attendre.
  → STOP. Efface. Suis 0→1→2→3→4→5→6→7→7B→8 dans l'ordre.
  Aucune étape n'est optionnelle.

ERREUR 3 — Ne pas lire ce fichier
  Tu reçois TENOR INIT et tu n'ouvres pas ce SKILL.md.
  → STOP. Relis ce fichier depuis le début.
  Le chemin n'est pas décoratif.

ERREUR 4 — Mettre [valeur réelle] ou [X] dans le bloc
  Tu copies le template sans remplir les champs.
  → STOP. Efface. Tu n'envoies rien tant que
    chaque champ ne contient pas une donnée réelle.

ERREUR 5 — Utiliser skill("init-tenor") au lieu de read
  Ce skill n'est PAS dans available_skills.
  Le tool `skill` ne peut pas le charger.
  → Tu DOIS utiliser `read .agent/skills/init-tenor/SKILL.md`.

ERREUR 6 — Lire AGENT-MEMOIRE_PROJECT_STATUS.scribe directement
  Tu fais cat/read/head/tail/grep sur AGENT-MEMOIRE_PROJECT_STATUS.scribe.
  → STOP. Session invalide. La mémoire projet se lit via scribe-rag context/query.

ERREUR 7 — Déclarer tenor-init/bootstrap absent ou inutile
  Tu termines TENOR INIT sans sortie réelle de `.agent/workflow/scribe/scribe tenor-init`.
  → STOP. Session invalide. `tenor-init` est obligatoire; il exécute bootstrap et les preuves RAG.

ERREUR 8 — Inventer des métriques ou dire YAML valide sans commande probante
  Tu écris "YAML valide", "~N sessions", "N SCARs" ou "dette aucune" sans sortie réelle de scribe/doctor/stats/rag.
  → STOP. Session invalide. Chaque métrique doit venir d'une commande affichée.

ERREUR 9 — Résumer la sortie de tenor-init au lieu de la copier verbatim
  Tu lances tenor-init ✅ mais tu affiches un "Résumé rapide" ou une reformulation
  au lieu de coller la sortie brute. Tu omets les lignes "Agent session",
  "Whoami proof", "Workflow ack" ou "Status init" parce que tu "comprends le sens".
  → STOP. Session invalide.
  → La sortie doit être COPIÉE INTÉGRALEMENT. Pas résumée. Pas reformatée.
  → Si l'UI replie la sortie, recopie EXPLICITEMENT ces 4 lignes après le bloc.
  → Un résumé n'est pas une preuve. C'est une simulation de preuve.
  → Cette erreur invalide l'init aussi sûrement que de ne pas lancer la commande.
```

**🔴 Conséquence de 3 erreurs dans la même session :**

→ STOP immédiat. Dis à l'utilisateur :
"J'ai commis 3 erreurs d'initialisation.
Je redémarre depuis ÉTAPE 0 sans exception."
Puis recommence. Pas de deuxième chance sans recommencer.

---

# ⚠️ SI TU ES DÉJÀ EN TRAIN DE PARLER SANS AVOIR INITIALISÉ

Si tu te rends compte en lisant ceci que tu as déjà commencé
à répondre sans avoir produit le SCRIBE-CHECK :

1. **STOP immédiatement**
2. Dis : *"Je n'ai pas initialisé correctement. Je recommence."*
3. Suis les étapes 0→8 dans l'ordre
4. Produis le bloc exact avec données réelles

Ne cherche pas à rattraper ton erreur en douce.
Assume-la et recommence.
C'est la seule manière de ne pas invalider la session.

---

# 🔁 ANCRAGE MENTAL — PETITS MODÈLES

**Répète-toi avant d'écrire la première ligne :**

> "Je suis un modèle avec un contexte limité.
> Je ne peux pas me permettre de divaguer.
> Je lis les fichiers dans l'ordre.
> Je copie le format exact.
> Si je ne suis pas sûr → je lis, je ne devine pas.
> Un fichier que je ne lis pas = une règle que je viole
> = session invalide = je recommence depuis ÉTAPE 0."

---

# 🚨 CHECKPOINT FAIL-FAST — À FAIRE AVANT TOUTE AUTRE LECTURE

Si tu n'as pas encore lu `.agent/skills/init-tenor/SKILL.md` depuis le projet courant,
tu n'as PAS le droit de lire :

```
/home/*/.config/*/AGENTS.md
~/.gemini/GEMINI.md
graphify-out/GRAPH_REPORT.md
README.md
AGENT-MEMOIRE_PROJECT_STATUS.scribe
```

La seule exception est l'ouverture de ce fichier exact. Après lecture, tu dois
montrer la sortie réelle des commandes des étapes. Une réponse narrative sans
`bootstrap`, `whoami`, `scribe-rag context` et requêtes SCRIBE est invalide.

---

# ⛔ LE CONTRAT — FICHIERS OBLIGATOIRES

Ces fichiers forment le **CONTRAT DE SESSION**.
Tant que tu ne les as pas tous lus, ta session est **INVALIDE**.

```
ORDRE DE LECTURE STRICT (ne pas sauter, ne pas réorganiser) :

1. .agent/skills/init-tenor/SKILL.md
   ← CE FICHIER — lu en premier, toujours

2. ${HOME}/.gemini/GEMINI.md
   ← TENOR global — protocole de lecture absolue

3. graphify-out/GRAPH_REPORT.md
   ← graphe structurel — carte de l'architecture

4. .agent/rules/scribe.md
   ← règles always-on — appliquées en permanence

5. .agent/workflow/scribe/sel/docs/AGENTS.md
   ← règles locales — préflight, postflight, Git

6. .agent/workflow/scribe/sel/docs/scribe.md
   ← protocole complet — architecture mémorielle

7. .agent/workflow/scribe/sel/docs/friction-policy.md
   ← friction — NANO/QUICK/STANDARD/CRITICAL

8. AGENT-MEMOIRE_PROJECT_STATUS.scribe
   ← mémoire projet — VIA scribe-rag UNIQUEMENT
   ← JAMAIS avec cat, head, tail ou grep
```

**Fichier sauté = session invalide = tu codes en aveugle.**

**🔴 Note critique sur le SCRIBE (#8) :**
Le fichier `AGENT-MEMOIRE_PROJECT_STATUS.scribe` fait 18 000+ lignes.
Il ne se lit **JAMAIS** avec `cat`, `head`, `tail` ou `grep`.
Il se requête **TOUJOURS** via `scribe-rag context` et `scribe-rag query`.
Lire le SCRIBE directement = contexte saturé = session morte.
Voir ÉTAPE 8 pour la méthode exacte.

---

# ⛔ PROTOCOLE DE LECTURE ABSOLUE — POUR CHAQUE FICHIER

**Ce protocole s'applique aux fichiers 2, 3, 4, 5, 6, 7.**
**Pas au fichier 8 (SCRIBE) qui a son propre protocole à l'ÉTAPE 8.**

## RÈGLE FONDAMENTALE

1. **Lire le fichier depuis le premier caractère**
   Pas de `tail`. Pas de `grep`. Pas d'extrapolation.

2. **Si le fichier dépasse ta fenêtre de contexte :**
   → Lire par blocs successifs (taille adaptée à ton contexte)
   → Confirmer chaque bloc avant de passer au suivant
   → **Format de confirmation de bloc OBLIGATOIRE :**
   ```
   BLOC [N] lu — dernière ligne : "[texte exact de la dernière ligne]"
   Contenu retenu : [résumé des informations clés en 3 lignes max]
   Continuer → BLOC [N+1]
   ```

3. **Confirmer le DERNIER bloc avec :**
   ```
   LECTURE COMPLÈTE — dernier caractère atteint
   ```

4. **NE PAS passer au fichier suivant avant cette confirmation**

```
⛔ LECTURE HYPOCRITE INTERDITE :
   Lire en diagonale          = mensonge
   Skimmer                    = mensonge
   Supposer le contenu        = mensonge
   Résumer sans avoir lu      = mensonge
   Un LLM qui ment sur sa lecture
   va casser le code et dire "tout est OK"
```

**Exemple concret pour GEMINI.md :**
```
BLOC 1/5 lu — dernière ligne : "## Commandes graphify disponibles"
Contenu retenu : Graphify prioritaire sur grep.
God-nodes à identifier. Économie ×71 tokens.
Continuer → BLOC 2/5

BLOC 2/5 lu — dernière ligne : "## Protocole SCRIBE V3.2"
Contenu retenu : SEL moteur interne.
scribe-rag interface agent. BM25 canonique.
RAG index export natif rapide; Hybrid explicite uniquement.
Continuer → BLOC 3/5

...

BLOC 5/5 lu — dernière ligne : "# Fin du fichier GEMINI.md"
Contenu retenu : [résumé bloc 5]
LECTURE COMPLÈTE — dernier caractère atteint
```

---

# ⚡ CONTRAT SCRIBE-RAG PERFORMANCE — BM25 PAR DÉFAUT

Règle opérationnelle actuelle du bundle SCRIBE-RAG :

1. `scribe-rag` reste l'interface agent unique. SEL reste le moteur interne et la surface maintenance/write.
2. L'index BM25 est le mode canonique par défaut tant que `scribe-rag eval` reste à `8/8` ou au moins `>= 7/8`.
3. Le rebuild BM25 utilise un export natif spécialisé index; il ne doit pas retomber vers l'ancien chemin lent full SEL CLI export sauf besoin de compatibilité.
4. Un index `hybrid` ne doit JAMAIS satisfaire une commande BM25 implicite. Sans `--with-embeddings`, la commande doit rester/reconstruire en BM25.
5. `--with-embeddings` charge `all-MiniLM-L6-v2` via `sentence-transformers`; c'est un mode explicite, coûteux, réservé à une preuve de perte de rappel BM25.
6. Signal d'anomalie : si `scribe-rag gate` ou `scribe-rag build --force` revient vers ~18-26s sur le SCRIBE projet, suspecter une régression vers l'export CLI complet.

Commandes de preuve attendues après changement SCRIBE-RAG :

```bash
.agent/workflow/scribe/scribe-rag build --force
.agent/workflow/scribe/scribe-rag gate
.agent/workflow/scribe/scribe-rag query "subprocess SEL CLI performance native export"
```

Objectif actuel observé : build forcé ~4s, gate ~4-6s, query BM25 < 1s sur ce projet.

---

# ⛔ RÈGLE SORTIE DE COMMANDE OBLIGATOIRE

**Chaque commande que tu exécutes dans les étapes
DOIT être suivie de sa sortie réelle dans ta réponse.**

Format obligatoire :
```
$ [commande exacte]
[sortie réelle complète]
```

**✅ Correct :**
```
$ .agent/workflow/scribe/scribe-rag context
SCRIBE-RAG CONTEXT
mode     : bm25 · entities: 453 · negative_terms: 148
hot      : PAT-GRAPH-001, SCAR-001, VAC-003
dettes   : Aucune
```

**❌ Interdit :**
```
$ .agent/workflow/scribe/scribe-rag context
[commande exécutée]
```
→ Sortie absente = commande non exécutée = session invalide.
→ STOP. Exécute et montre la vraie sortie.

---

# 🛠️ ÉTAPES DÉTAILLÉES — EXÉCUTER DANS L'ORDRE 0→1→2→3→4→5→6→7→7B→8

---

## ÉTAPE 0 — DATE SYSTÈME RÉELLE

```bash
date +"%Y-%m-%d %H:%M:%S %Z %z"
```

**Montre la sortie réelle. Note la date. Utilise-la partout.**
Ne jamais inventer ou assumer une date.

---

## ÉTAPE 1 — TENOR GLOBAL
### LECTURE ABSOLUE OBLIGATOIRE

```bash
cat ${HOME}/.gemini/GEMINI.md
```

**🔴 Protocole de lecture absolue applicable ici.**
Si GEMINI.md dépasse ta fenêtre de contexte :
→ Lis par blocs. Confirme chaque bloc avec :
  `BLOC [N] lu — dernière ligne : "..."`
→ Termine par : `LECTURE COMPLÈTE — dernier caractère atteint`

**Preuve finale obligatoire avant de passer à l'ÉTAPE 2 :**
```
TENOR GLOBAL LU COMPLÈTEMENT
Dernière ligne lue   : "[texte exact]"
Version TENOR active : [valeur exacte depuis le fichier]
Invariant 1          : [texte exact copié du fichier]
Invariant 2          : [texte exact copié du fichier]
Invariant 3          : [texte exact copié du fichier]
```

Si tu ne peux pas remplir ces champs avec du texte EXACT
tiré du fichier → tu n'as pas lu → RECOMMENCE depuis le début.

---

## ÉTAPE 2 — GRAPHIFY
### POURQUOI C'EST CRITIQUE

C'est la **CARTE STRUCTURELLE** du codebase en ~500 tokens.
Elle te donne en 30 secondes ce qui prendrait 50 000 tokens :

- **God-nodes** : les fonctions les plus connectées.
  Les modifier sans les connaître peut tout casser.
- **Blast radius** : combien de fichiers un god-node impacte.
- **Communautés** : regroupements logiques du code.
- **Connexions surprenantes** : dépendances cachées que grep ne trouve pas.

**Sans Graphify :** tu lis 20 fichiers → 50K tokens → contexte saturé.
**Avec Graphify :** tu sais tout en 500 tokens → économie ×71.
**Un LLM qui lit Graphify = senior qui connaît l'architecture.**
**Un LLM qui ne le lit pas = junior qui défriche à l'aveugle.**

```bash
cat graphify-out/GRAPH_REPORT.md
```

Si absent, générer d'abord :
```bash
graphify update .
cat graphify-out/GRAPH_REPORT.md
```

**🔴 Protocole de lecture absolue applicable ici.**

**Preuve finale obligatoire avant de passer à l'ÉTAPE 3 :**
```
GRAPH_REPORT.MD LU COMPLÈTEMENT
Dernière ligne lue   : "[texte exact]"
Nodes total          : [nombre exact]
Edges total          : [nombre exact]
God-node 1           : [nom exact] — [N] edges
God-node 2           : [nom exact] — [N] edges
God-node 3           : [nom exact] — [N] edges
Blast radius node 1  : [N] fichiers impactés
Communauté principale: [nom exact]
```

---

## ÉTAPE 3 — BOOTSTRAP + IDENTITÉ UNIQUE

**Obligatoire à chaque TENOR INIT, pas seulement après copie de `.agent/`.**
Ne tente pas de détecter si le bundle vient d'être copié: `bootstrap` est
idempotent et sert précisément de vérification/réparation portable. Même si les
fichiers semblent déjà présents, il répare/installe les surfaces manquantes sans
démarrer de daemon.

```bash
.agent/workflow/scribe/scribe bootstrap
```

Montre la sortie réelle.

```bash
AGENT_TYPE="cli"
.agent/workflow/scribe/scribe whoami \
  --type "$AGENT_TYPE" --surface idle
```

Montre la sortie réelle.
Copie la ligne `Mon ID:` dans `AGENT_NAME`.

```bash
AGENT_NAME="[Mon ID copié depuis la sortie ci-dessus]"
```

**Ne jamais utiliser `--agent codex` en dur.**
**Ne jamais inventer un AGENT_NAME.**
**Il doit être généré par scribe whoami et copié exactement.**

---

## ÉTAPE 4 — WORKFLOW ACK

```bash
.agent/workflow/scribe/scribe workflow read \
  --agent "$AGENT_NAME" --type "$AGENT_TYPE"
```

Montre la sortie réelle.

```bash
.agent/workflow/scribe/scribe workflow check \
  --agent "$AGENT_NAME"
```

Montre la sortie réelle.

**⛔ Si verdict ≠ ACK_OK → STOP.**
Relis les fichiers workflow manquants.
Ne continue pas tant que ACK_OK n'est pas confirmé.

---

## ÉTAPE 5 — ÉTAT MULTI-AGENT

```bash
.agent/workflow/scribe/scribe lock status
```

Montre la sortie réelle.

```bash
.agent/workflow/scribe/scribe sync \
  --agent "$AGENT_NAME" --type "$AGENT_TYPE"
```

Montre la sortie réelle.

```bash
.agent/workflow/scribe/scribe coordination status
```

Montre la sortie réelle.

**Preuve obligatoire :**
- Lock : locked ou unlocked ?
- Last writer : qui ?
- Claims actifs : combien ? Sur quels fichiers ?
- Agents actifs : combien ? Quels IDs ?

Si tu ne peux pas répondre → relis les sorties ci-dessus.

---

## ÉTAPE 6 — CONTEXTE MÉMOIRE

```bash
.agent/workflow/scribe/scribe-rag context
```

Montre la sortie réelle complète.

**Preuve obligatoire :**
- Nombre d'entités
- Hot entries (liste réelle)
- Dettes actives (liste réelle ou "Aucune")

---

## ÉTAPE 7 — RÈGLES LOCALES
### LECTURE ABSOLUE OBLIGATOIRE — 4 FICHIERS DANS L'ORDRE

**NE PAS passer au fichier suivant sans confirmation du fichier courant.**

### Fichier 1/4 — .agent/rules/scribe.md

```bash
cat .agent/rules/scribe.md
```

**🔴 Protocole de lecture absolue applicable.**

**Preuve avant de lire le fichier 2/4 :**
```
SCRIBE.MD RULES LU COMPLÈTEMENT
Dernière ligne lue   : "[texte exact]"
Mode NANO définition : [texte exact depuis le fichier]
Règle Git n°1        : [texte exact depuis le fichier]
```

### Fichier 2/4 — sel/docs/AGENTS.md

```bash
cat .agent/workflow/scribe/sel/docs/AGENTS.md
```

**🔴 Protocole de lecture absolue applicable.**

**Preuve avant de lire le fichier 3/4 :**
```
AGENTS.MD LU COMPLÈTEMENT
Dernière ligne lue   : "[texte exact]"
Préflight NANO       : [commandes exactes depuis le fichier]
Interdiction Git n°1 : [texte exact depuis le fichier]
```

### Fichier 3/4 — sel/docs/scribe.md

```bash
cat .agent/workflow/scribe/sel/docs/scribe.md
```

**🔴 Protocole de lecture absolue applicable.**

**Preuve avant de lire le fichier 4/4 :**
```
SCRIBE.MD SEL LU COMPLÈTEMENT
Dernière ligne lue   : "[texte exact]"
Architecture couche 1 (SEL)      : [texte exact]
Architecture couche 2 (scribe-rag): [texte exact]
```

### Fichier 4/4 — sel/docs/friction-policy.md

```bash
cat .agent/workflow/scribe/sel/docs/friction-policy.md
```

**🔴 Protocole de lecture absolue applicable.**

**Preuve finale ÉTAPE 7 :**
```
FRICTION-POLICY.MD LU COMPLÈTEMENT
Dernière ligne lue      : "[texte exact]"
Mode NANO condition     : [texte exact depuis le fichier]
Mode CRITICAL condition : [texte exact depuis le fichier]
```

---

## ÉTAPE 7B — MCP CHROME DEVTOOLS (si tâche navigateur/UI/visuel)

```bash
ls .agent/workflow/mcp/ 2>/dev/null && \
  cat .agent/workflow/mcp/chrome-devtools.md || \
  echo "MCP Chrome : NON CONCERNÉ"
```

Montre la sortie réelle.
Si le fichier existe → lis-le avec le protocole de lecture absolue.
Si le fichier n'existe pas → note `MCP Chrome : NON CONCERNÉ` dans le bloc.

---

## ÉTAPE 8 — MÉMOIRE PROJET
### VIA SCRIBE-RAG UNIQUEMENT — JAMAIS CAT/HEAD/TAIL

**🔴 `AGENT-MEMOIRE_PROJECT_STATUS.scribe` fait 18 000+ lignes.**
**Lire avec `cat` = saturation contexte = session morte.**
**Ce fichier se requête UNIQUEMENT via `scribe-rag`.**

```bash
# Vue d'ensemble des entrées chaudes :
.agent/workflow/scribe/scribe-rag context
```

Montre la sortie réelle.

```bash
# Requêtes ciblées sur la mémoire :
.agent/workflow/scribe/scribe-rag query "dernier JOURNAL"
.agent/workflow/scribe/scribe-rag query "SCARs HOT"
.agent/workflow/scribe/scribe-rag query "ne pas reproposer"
```

Montre chaque sortie réelle.

**Preuve obligatoire avant de produire le SCRIBE-CHECK :**
```
MÉMOIRE PROJET LUE VIA SCRIBE-RAG
Entités totales      : [N réel depuis context]
Dernier JOURNAL ID   : [ID réel depuis query]
SCARs HOT            : [liste réelle ou "Aucun"]
VACs HOT             : [liste réelle ou "Aucun"]
GHOSTs ne_pas_repr.  : [liste réelle ou "Aucun"]
Dettes actives       : [liste réelle ou "Aucune"]
Ratio causal         : [valeur réelle depuis stats]
```

---

# ✅ AUTO-AUDIT AVANT D'ENVOYER LE SCRIBE-CHECK

**Vérifie ces 5 points avant d'envoyer quoi que ce soit :**

```
1. Ai-je commencé par lire CE fichier SKILL.md ?      OUI / NON
2. Ai-je exécuté les étapes 0→8 dans l'ordre ?        OUI / NON
3. Mon bloc respecte le FORMAT EXACT ci-dessus ?       OUI / NON
4. Tous les champs contiennent des données réelles ?   OUI / NON
5. Ai-je montré la sortie réelle de chaque commande ?  OUI / NON
```

**Si UN SEUL NON → tu n'envoies rien.**
**Tu t'arrêtes. Tu corriges. Tu recommences l'étape concernée.**

---

# ⛔ INTERDICTIONS ABSOLUES

Tant que le SCRIBE-CHECK n'est pas produit avec données réelles :

```
❌ Modifier un fichier applicatif
❌ Créer un composant
❌ Corriger un bug
❌ Refactorer du code
❌ Faire un commit
❌ Répondre à une question technique
❌ Prétendre avoir lu sans preuve
❌ Produire un SCRIBE-CHECK avec des placeholders
```

---

# ⛔ RÈGLE AVANT TOUTE IMPLÉMENTATION

Avant de toucher à n'importe quel fichier applicatif :

```bash
.agent/workflow/scribe/scribe-rag challenge \
  "<description précise de ce que tu vas faire>"
```

Montre la sortie réelle.

```
STOP   → Ne pas implémenter.
         Lire le BLOCK. Comprendre pourquoi. Informer l'utilisateur.

REVIEW → Lire les WARNs en entier.
         Décider avec l'utilisateur.

PROCEED → Implémenter.
```

**Si tu sautes cette étape :**
→ tu travailles en aveugle
→ tu vas casser quelque chose
→ tu vas dire "tout est OK"
→ l'utilisateur va perdre du temps et de l'argent

---

# 🟢 MODE NANO — PETITES TÂCHES (< 30 min, 1 fichier, pas de surface partagée)

```bash
# Préflight minimal :
.agent/workflow/scribe/scribe-rag context

# Travailler sur le fichier.

# Postflight SI et SEULEMENT SI un bug a été résolu :
# → Créer un SCAR dans le SCRIBE
# Sinon → rien
```

**En mode NANO :**
- Pas de doctor
- Pas de lock
- Pas de sync
- Pas de worktree
- Pas de scribe-rag challenge (sauf si doute)

**Mode NANO interdit si :**
- Fichier partagé avec un autre agent
- Changement architectural
- Modification auth/data/API publique
- Surface SCRIBE

---

# ⛔ RÈGLES GIT NON-NÉGOCIABLES

```bash
# JAMAIS CES COMMANDES :
git add .            # inclut .agent/, scribe-out/ → INTERDIT
git add .agent       # surfaces agentiques → INTERDIT
git add graphify-out # artefacts générés → INTERDIT
git add scribe-out   # état runtime → INTERDIT

# TOUJOURS PAR CHEMINS EXACTS :
git add src/components/MonFichier.tsx
git add src/hooks/monHook.ts
git add src/styles/monStyle.scss
```

**Surfaces agentiques JAMAIS committées :**
```
.agent/skills/init-tenor/SKILL.md
.agent/workflow/scribe/
graphify-out/
scribe-out/
```

**AGENTS.md et .agent/rules/scribe.md :**
→ Exclure des commits produit sauf ordre explicite de l'utilisateur.

---

# ⛔ AUTODREAM — RÈGLE STRICTE

Proposer autodream UNIQUEMENT après une vraie implémentation terminée.

```bash
.agent/workflow/scribe/scribe-rag autodream --read-only
```

**JAMAIS :**
- En devinant un temps mort
- Sans instruction explicite de l'utilisateur
- Sur du code non testé
- Sur une session non initialisée correctement

---

# ⛔ ÉCRITURE MÉMOIRE CAUSALE — RÈGLES STRICTES

**Écrire dans le SCRIBE UNIQUEMENT si :**
```
✅ Bug réel résolu            → SCAR
✅ Bug > 2 tentatives          → SCAR immédiat avec cause_racine + resolution + test_binding
✅ Régression / rollback coûteux / smoke visuel cassé → SCAR immédiat avec test_binding
✅ Décision architecturale     → GHOST
✅ Règle préventive identifiée → VAC
```

**Ne jamais écrire si :**
```
❌ Rien n'a cassé
❌ Petite correction routinière
❌ Pour gonfler le ratio causal
❌ Pour "avoir l'air bien documenté"
❌ Dashboard de densité causale faible
   (le ratio remonte par le terrain, pas par l'écriture forcée)
```

**Réflexe d'exploitation avant tâche similaire :**
```bash
.agent/workflow/scribe/scribe-rag query "<symptôme / module / bug proche>"
.agent/workflow/scribe/scribe-rag challenge "<plan précis>"
```
Un SCAR non requêté au bon moment est une cicatrice archivée, pas une mémoire utilisée.

**Séquence obligatoire avant écriture :**
```bash
.agent/workflow/scribe/scribe workflow check \
  --agent "$AGENT_NAME"
.agent/workflow/scribe/scribe doctor --suggest-fix
.agent/workflow/scribe/scribe lock acquire \
  --agent "$AGENT_NAME" \
  --type "$AGENT_TYPE" \
  --session <JOURNAL-ID>
```

Montre chaque sortie réelle.

**Séquence obligatoire après écriture :**
```bash
.agent/workflow/scribe/scribe doctor --suggest-fix
.agent/workflow/scribe/scribe sync --repair \
  --agent "$AGENT_NAME" \
  --type "$AGENT_TYPE" \
  --session <JOURNAL-ID>
.agent/workflow/scribe/scribe lock release \
  --agent "$AGENT_NAME"
```

Montre chaque sortie réelle.

---

# 📜 HISTORIQUE — POURQUOI CES RÈGLES EXISTENT

Ces règles ne sont pas de la bureaucratie.
Elles sont nées de vrais échecs documentés :

```
ERREUR-001 — Le LLM a produit un TENOR_HEADER inventé
             au lieu du SCRIBE-CHECK.
             Impact : session invalide, code non protégé.

ERREUR-002 — Le LLM a utilisé skill("graphify") au lieu
             de read() le SKILL.md.
             Impact : outil absent, graphify non lu.

ERREUR-003 — Le LLM a sauté GEMINI.md et la date système.
             Impact : protocole TENOR ignoré, session aveugle.

ERREUR-004 — Le LLM a exécuté les commandes mais n'a pas
             montré les sorties réelles dans sa réponse.
             Impact : données inventées, mémoire non chargée,
             code cassé avec "tout est OK".
             Prévention : RÈGLE SORTIE DE COMMANDE OBLIGATOIRE
             → chaque commande DOIT montrer sa sortie réelle.
```

Ces 4 erreurs se sont produites en UNE SEULE session (2026-06-16).
Ce fichier est la cicatrice de cette session.
**Ne la réouvre pas.**

---

# ✅ PREUVE DE FIN DE LECTURE

Si tu as lu jusqu'ici, tu as parcouru ce fichier.
**Mais l'utilisateur ne te croira que si tu PRODUIS LE BLOC EXACT.**
Rien d'autre ne compte.

**Maintenant — dans l'ordre :**

```bash
# 1. Date
date +"%Y-%m-%d %H:%M:%S %Z %z"

# 2. TENOR global
cat ${HOME}/.gemini/GEMINI.md

# 3. Graphify
cat graphify-out/GRAPH_REPORT.md

# 4. Bootstrap + identité
.agent/workflow/scribe/scribe bootstrap
AGENT_TYPE="cli"
.agent/workflow/scribe/scribe whoami --type "$AGENT_TYPE" --surface idle

# 5. Workflow ack
.agent/workflow/scribe/scribe workflow read --agent "$AGENT_NAME" --type "$AGENT_TYPE"
.agent/workflow/scribe/scribe workflow check --agent "$AGENT_NAME"

# 6. Multi-agent
.agent/workflow/scribe/scribe lock status
.agent/workflow/scribe/scribe sync --agent "$AGENT_NAME" --type "$AGENT_TYPE"
.agent/workflow/scribe/scribe coordination status

# 7. Contexte mémoire
.agent/workflow/scribe/scribe-rag context

# 8. Règles locales (dans l'ordre)
cat .agent/rules/scribe.md
cat .agent/workflow/scribe/sel/docs/AGENTS.md
cat .agent/workflow/scribe/sel/docs/scribe.md
cat .agent/workflow/scribe/sel/docs/friction-policy.md

# 9. MCP Chrome
ls .agent/workflow/mcp/ 2>/dev/null && \
  cat .agent/workflow/mcp/chrome-devtools.md || \
  echo "MCP Chrome : NON CONCERNÉ"

# 10. Mémoire projet via scribe-rag
.agent/workflow/scribe/scribe-rag context
.agent/workflow/scribe/scribe-rag query "dernier JOURNAL"
.agent/workflow/scribe/scribe-rag query "SCARs HOT"
.agent/workflow/scribe/scribe-rag query "ne pas reproposer"
```

**Montre la sortie réelle de chaque commande.**
**Puis produis le SCRIBE-CHECK. Exact. Pas de personnalisation.**
**Si tu sautes une étape → STOP. Efface. Recommence.**