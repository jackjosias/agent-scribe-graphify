
***

```markdown
# ════════════════════════════════════════════════════════════════════════════════
# ⚠️  RÈGLE DE PROTECTION VITALE — NE JAMAIS ÉCRASER CE FICHIER ⚠️
# ════════════════════════════════════════════════════════════════════════════════
# TOUTE MISE À JOUR DOIT ÊTRE INCRÉMENTALE ET ORGANIQUE.
# SI TU ÉCRASES L'HISTORIQUE, TU AS ÉCHOUÉ À TA MISSION.
# ════════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════
# 🧠 PROTOCOLE AXIAL-SCRIBE V4 — GRAPHIFY + SEL INTERNAL + SCRIBE-RAG
# ════════════════════════════════════════════════════════════════════════════════
# description: Protocole SCRIBE - Mémoire CAUSALE persistante pour LLMs
# version: V4 (Graphify-aware + SEL internal + scribe-rag canonical)
# schema_patch_date: 2026-05-26
# PRINCIPE FONDAMENTAL V4 :
#   Le SCRIBE ne documente QUE ce que Graphify ne peut pas déduire.
#   Graphify  = mémoire STRUCTURELLE (le QUOI / le OÙ / le COMMENT)
#   SEL       = moteur interne de garde/écriture, jamais interface agent retrieval
#   scribe-rag = interface agent unique pour lecture, contexte et challenge
# ════════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════
# 🗺️ PARTIE 0 — ARCHITECTURE MÉMORIELLE V4 — FINALE
# ════════════════════════════════════════════════════════════════════════════════
#
# COUCHE 1 — GRAPHIFY
#   graphify-out/              → graphe applicatif hôte.
#   scribe-out/bundle-graph/   → graphe du bundle SEL pour maintenance tooling.
#   Rôle                       → structure du code: imports, call graph, god-nodes,
#                                communautés, blast radius, relations EXTRACTED/INFERRED.
#   Commandes app              → graphify query "..." | graphify path "A" "B" |
#                                graphify explain "X" | graphify update .
#   Règle                      → Graphify ne connaît pas les surfaces multi-agent;
#                                les surfaces sont une règle SCRIBE/protocole.
#
# COUCHE 2 — SEL (moteur interne uniquement)
#   Dossier   : .agent/workflow/scribe/sel/
#   CLI       : .agent/workflow/scribe/scribe
#   Règle     : ne jamais appeler directement depuis AGENTS.md pour retrieval,
#               contexte, query, explain ou challenge agent.
#   Appelé par: scribe-rag via CLI canonique et export JSON.
#   Responsabilités:
#     - bootstrap portable
#     - doctor et validation SCRIBE
#     - workflow read ack, lock, state, sync multi-agent
#     - identite unique, presence idle/working et claims semantiques
#       pour coordination agent-pool
#     - export/archive
#     - graph bundle
#     - écritures SCRIBE et garde-fous d'écriture
#
# COUCHE 3 — SCRIBE-RAG BM25 (seule interface agent)
#   Dossier   : .agent/workflow/scribe/rag/
#   CLI       : .agent/workflow/scribe/scribe-rag
#   Rôle      : toutes les lectures mémoire agent passent par scribe-rag.
#   Commandes : preflight, build, context, query, explain, challenge, eval, doctor.
#   Mode      : BM25 par défaut, portable, sans dépendance externe.
#   Hybrid    : option opt-in seulement apres preuve de baisse de rappel; jamais
#               parce qu un modele local existe.
#   Preflight : la commande `scribe-rag preflight` est la preuve obligatoire
#               d exploitation mémoire: whoami + context + eval + challenge.
#   CI Gate   : `scribe-rag gate` et `hooks/pre-commit` échouent si eval
#               n est pas entièrement vert et au moins 8/8.
#   Ack agents: `scribe workflow read/check/status` force chaque agent à lire
#               le workflow courant avant lock SCRIBE ou surface partagée.
#   Lecture   : les agents ne lisent jamais AGENT-MEMOIRE_PROJECT_STATUS.scribe
#               directement; scribe-rag consomme SEL/export puis son index.
#
# SIGNAL HYBRID
#   BM25 reste canonique tant qu il retrouve les bonnes entrees SCRIBE.
#   Preuve de baisse de rappel = eval < 7/8, query qui manque une entree SCRIBE
#   connue pertinente, challenge qui ne remonte pas une SCAR/VAC/GHOST directement
#   liee au plan, ou resultats hors sujet repetes sur le vocabulaire normal du projet.
#   Seulement dans ce cas :
#     1. Installer sentence-transformers.
#     2. Lancer `scribe-rag build --with-embeddings --force`.
#     3. Le mode hybrid devient actif via l'index reconstruit.
#   Tant que eval >= 7/8 et que les souvenirs pertinents sortent, BM25 reste le
#   système canonique portable.
#
# RÈGLE ABSOLUE V4
#   Un agent ne doit JAMAIS appeler SEL directement pour du retrieval ou du contexte.
#   scribe-rag uniquement. SEL reste le moteur sous le capot.
#
# AUTODREAM
#   Les LLM ne connaissent pas les temps morts humains. Apres une vraie
#   implementation livree, l agent propose AutoDream explicitement a l utilisateur.
#   Commande: `scribe-rag autodream --read-only`. AutoDream est read-only:
#   digerer les surfaces du diff, compacter le contexte via l index RAG existant,
#   detecter les contradictions, prouver l absence d ecriture et proposer les
#   memoires candidates. Il ne modifie ni code, ni SCRIBE, ni artefact genere,
#   ne lance aucun daemon/build/doctor et ne commit jamais. Toute ecriture issue
#   d AutoDream est une tache separee avec validation humaine, workflow ack,
#   doctor, lock, sync et validation.
#
# SURFACES CANONIQUES
#   Apres toute evolution du workflow SCRIBE, synchroniser et verifier:
#   `AGENTS.md`, `.agent/rules/scribe.md`, `.agent/skills/init-tenor/SKILL.md`,
#   `.agent/workflow/scribe/README.md`, `.agent/workflow/scribe/rag/README.md`,
#   `.agent/workflow/scribe/sel/docs/AGENTS.md`,
#   `.agent/workflow/scribe/sel/docs/friction-policy.md`,
#   `.agent/workflow/scribe/sel/docs/scribe.md` et
#   `AGENT-MEMOIRE_PROJECT_STATUS.scribe`.
#   Les archives `.old` restent historiques et non canoniques.
#
# ÉTAT STABILISÉ 2026-06-01
#   SEL tests 81 OK, RAG tests 25 OK, gate/eval 8/8, doctor 0 error.
#   W009 legacy pré-V3.2 est cosmétique.
#   getpid, stale PID cleanup, TTL claims, expired/no-TTL claim cleanup,
#   IDs/PIDs simultanés et lock release ownership sont corrigés.
#   Backup : ~/backups/agent-scribe-stable-20260601.tar.gz.
#   Ratio causal mesuré ~17.5%, cible 35% : améliorer seulement avec de vraies
#   douleurs applicatives, jamais par SCAR/GHOST/PAT cosmétique.
#   Instruction finale : STOP .agent; retourner au projet produit sauf bug SCRIBE réel.
#
# HYGIÈNE VCS / PUSH
#   Le scope de commit/push par défaut est le produit applicatif hôte.
#   `AGENT-MEMOIRE_PROJECT_STATUS.scribe` peut être versionné quand l'équipe veut
#   partager la mémoire causale entre agents/humains.
#   `graphify-out/` ne se versionne pas par défaut : c'est un graphe généré.
#   `scribe-out/` ne se versionne pas par défaut : c'est de l'état runtime local.
#   `.agent/` ne se versionne que si l'équipe maintient volontairement l'outillage.
#
# RÈGLE ABSOLUE D'ÉCRITURE SCRIBE
#   ❌ NE PAS écrire : "Le fichier X importe Y"          → Graphify le sait
#   ❌ NE PAS écrire : "La fonction Z fait A"            → Graphify le sait
#   ❌ NE PAS écrire : "La lib X est en version Y"       → Graphify lit package.json
#   ✅ ÉCRIRE        : "On a choisi X plutôt que Y car"  → Graphify ne sait pas
#   ✅ ÉCRIRE        : "Ce bug arrive quand on oublie"   → Graphify ne sait pas
#   ✅ ÉCRIRE        : "Cette approche a échoué parce"   → Graphify ne sait pas
# ════════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════
# 🏛️ PARTIE 1 — PHILOSOPHIE TENOR (CODE GÉNÉTIQUE DE L'AGENT)
# ════════════════════════════════════════════════════════════════════════════════
#
# > RL_MANTRA : Le SCRIBE s'enrichit à chaque session. Tu es une entité apprenante.
# > DUALITÉ   : Coder sans mettre à jour le SCRIBE est un crime architectural.
# > IDENTITÉ  : Le SCRIBE est ton seul lien avec ton passé. Protège-le.
# > MÉMOIRE   : Documenter les échecs est AUSSI important que les succès.
# > CAUSALITÉ : Un bug résolu sans lien causal ne vaut rien.
# > ANTI-DÉRIVE : "Qu'est-ce qui fera souffrir le prochain LLM si je ne le documente pas ?"
#                 Si la réponse existe, écrire un SCAR ou un GHOST; sinon le JOURNAL suffit.
# > DENSITÉ CAUSALE : Une alerte dashboard de densité faible est un signal informatif,
#                     jamais une autorisation d'écrire un SCAR/GHOST/PAT cosmétique.
# > PERFECTION: La perfection = quand il n'y a plus rien à retirer.
# > GRAPHIFY  : Tout fait structurel déductible du code appartient à Graphify,
#               pas au SCRIBE. Le SCRIBE est léger par design.
# ════════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════
# 🔬 PARTIE 2 — PARADIGME AXIAL-SCRIBE V3.2 (ZONES COGNITIVES)
# ════════════════════════════════════════════════════════════════════════════════
#
# Le fichier .scribe est divisé en 3 zones :
#
# ZONE A : [AXIAL_NEXUS_CORE] — Haut du fichier
#   Format   : Grammaire Axiale (Memelang)
#   Contient : Invariants absolus, Index des Tiers
#   PAS de versions de libs  → Graphify lit package.json
#   PAS de liste de fichiers → Graphify connaît la structure
#
# ZONE B : [CAUSAL_CHAINING_YAML] — Milieu du fichier
#   Format   : YAML strict L0/L2
#   Contient : SCARs, VACs, PATs, GHOSTs, DEBTs — UNIQUEMENT le POURQUOI
#
# ZONE C : [EPISODIC_SCAFFOLDING] — Bas du fichier
#   Format   : Markdown narratif
#   Contient : Directive session courante, contexte immédiat
# ════════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════
# 🔄 PARTIE 3 — CYCLE DREAM (MUE DE LA MÉMOIRE)
# ════════════════════════════════════════════════════════════════════════════════
#
# 3.0 CYCLE OPÉRATIONNEL PAR TÂCHE RÉELLE — NON-NÉGOCIABLE
#   Pour chaque tâche concrète sur le projet hote :
#   1. Lire graphify-out/GRAPH_REPORT.md avant de comprendre le code
#   2. Lancer `scribe whoami --type <type> --surface idle`, puis `scribe workflow read/check` et `scribe coordination status`
#   3. Quand une tache arrive, prendre un `scribe coordination claim` semantique avant les fichiers partages
#   4. Choisir le plus petit tier sûr : NANO = `scribe-rag context`, STANDARD = build/context/challenge, CRITICAL = preflight strict.
#   5. Si le plan change après preflight : relancer `scribe-rag challenge "<plan révisé>"`
#   6. Implémenter de façon ciblée, sans régression
#   7. Valider par tests/lint/build adaptés au périmètre
#   8. Écrire le delta causal dans AGENT-MEMOIRE_PROJECT_STATUS.scribe seulement si une mémoire durable est utile
#      └── Question finale obligatoire :
#          "Qu'est-ce qui fera souffrir le prochain LLM si je ne le documente pas ?"
#          Réponse concrète → SCAR/GHOST. Pas de réponse → JOURNAL seulement.
#          Une alerte dashboard de densité causale ne déclenche aucune écriture sans douleur réelle.
#          Bug > 2 tentatives, régression, rollback coûteux ou smoke visuel cassé → SCAR immédiat avec cause_racine, resolution, test_binding.
#          Avant une tâche proche d une cicatrice connue → scribe-rag query/explain/challenge obligatoire pour exploiter la mémoire.
#   9. Relancer scribe doctor pour vérifier la mémoire après écriture
#      └── Les rapports Markdown doctor vont toujours dans `scribe-out/`.
#
# RÈGLE SIMPLE :
#   Graphify avant de comprendre le code.
#   whoami idle + workflow read/check + coordination status avant toute surface partagee.
#   coordination claim semantique avant les fichiers partages.
#   scribe-rag preflight avant de décider.
#   SCRIBE après avoir appris.
#   SEL doctor AVANT d'écrire la mémoire.
#   SEL doctor APRÈS avoir écrit la mémoire.
#   scribe doctor ne complète rien : il vérifie seulement.
#   scribe doctor écrit ses rapports Markdown dans `scribe-out/`, jamais à la racine.
#
# GARDE DOCTOR — OBLIGATOIRE POUR TOUTE ÉVOLUTION SCRIBE :
#   Toute modification de AGENT-MEMOIRE_PROJECT_STATUS.scribe suit ce protocole :
#   0. AVANT LOCK : `<SCRIBE> workflow check --agent <name>`
#      └── `lock acquire` refuse automatiquement ACK_REQUIRED ou ACK_STALE.
#   1. AVANT : `<SCRIBE> doctor --suggest-fix`
#      └── Rapport par défaut : `scribe-out/scribe-doctor-report.md`
#      └── Si ERRORS > 0 : STOP, corriger la mémoire existante avant d'ajouter quoi que ce soit.
#   2. ÉCRITURE : append/update incrémental uniquement, jamais overwrite global.
#   3. APRÈS : `<SCRIBE> doctor --suggest-fix`
#      └── Rapport par défaut : `scribe-out/scribe-doctor-report.md`
#      └── Si ERRORS > 0 : corriger immédiatement ou restaurer le patch mémoire fautif.
#   4. SI ÉCRITURE PAR COMMANDE : préférer `<SCRIBE> guard -- <command>`
#      └── Ce wrapper exécute doctor avant, lance la commande, puis exécute doctor après.
#      └── Rapports par défaut : `scribe-out/scribe-doctor-before-report.md` et `scribe-out/scribe-doctor-after-report.md`.
#
# 3.1 PHASE 1 : ORIENTATION (Début de session) — OBLIGATOIRE
#   ├── LIS graphify-out/GRAPH_REPORT.md          (carte structurelle)
#   ├── LIS [AXIAL_NEXUS_CORE] du .scribe         (invariants)
#   ├── LIS les l0_abstract Tier HOT du .scribe   (mémoire causale)
#   ├── LIS [EPISODIC_SCAFFOLDING] du .scribe     (contexte immédiat)
#   └── AFFICHE le SCRIBE-CHECK V4
#
# 3.1.5 CHALLENGE (Avant toute implémentation significative)
#   ├── NANO : `scribe-rag context` suffit; STANDARD/CRITICAL : lancer `scribe-rag preflight` ou `challenge` selon le risque.
#   ├── STOP   → ne pas implémenter; lire le BLOCK et corriger la stratégie
#   ├── REVIEW → lire les WARNs puis décider explicitement
#   ├── PROCEED → implémenter
#   └── Ne jamais remplacer ce preflight/challenge par un appel SEL direct.
#
# 3.2 PHASE 2 : GATHER SIGNAL (Pendant la tâche)
#   ├── Pour toute question structure/dépendance → graphify query "..."
#   ├── Pour tout cross-module                   → graphify path "A" "B"
#   └── Pour comprendre un nœud                  → graphify explain "X"
#
# 3.3 PHASE 3 : CONSOLIDATION (Fin de tâche)
#   ├── SI BUG RÉSOLU      → SCAR (le POURQUOI, pas la structure)
#   │   └── Si >2 tentatives, régression, rollback coûteux ou smoke visuel cassé: cause_racine + resolution + test_binding obligatoires.
#   ├── SI ERREUR ÉVITÉE   → VAC
#   ├── SI NOUVEAU PATTERN → PAT
#   ├── SI DETTE TECHNIQUE → DEBT
#   ├── SI DÉCISION ARCHI  → GHOST + ADR
#   └── Écrire dans [CAUSAL_CHAINING_YAML] du .scribe
#
# 3.4 PHASE 4 : PRUNING (Maintenance entropie)
#   ├── Compter journal[].hot_entries_consulted (IDs uniquement)
#   ├── hot → warm si sessions_sans_consultation >= 8 ET severite != CRITICAL
#   ├── warm → cold si sessions_sans_consultation >= 20 ET status != ACTIVE
#   ├── CRITICAL, DEBT ACTIVE, ne_pas_reproposer → jamais sous warm
#   ├── Fusionner PATs redondants
#   └── Mettre à jour Session_Count dans [AXIAL_NEXUS_CORE]
# ════════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════
# 📜 PARTIE 4 — SCRIBE-CHECK V4 (PREUVE DE LECTURE OBLIGATOIRE)
# ════════════════════════════════════════════════════════════════════════════════
#
# 📜 SCRIBE-CHECK V4 [AXIAL/HYBRIDE + GRAPHIFY]:
# ├── GRAPHIFY LU          : ✅ GRAPH_REPORT.md consulté
# ├── GOD NODES ACTIFS     : [Liste des god-nodes lus depuis GRAPH_REPORT.md]
# ├── NEXUS CORE           : ✅ Invariants chargés
# ├── L0_ABSTRACTS LUS     : [IDs SCAR/VAC pertinents pour la tâche]
# ├── L2_DETAILS REQUIS    : [ID si approfondissement nécessaire, sinon "Aucun"]
# ├── DETTES ACTIVES       : [Liste DEBT-XXX actives ou "Aucune"]
# ├── SCOPES V3.2          : [runtime/test/migration/security/dev/universal vérifiés]
# ├── EVIDENCE V3.2        : [OBSERVED/REASONED/ASSUMED cohérents]
# ├── CHALLENGE            : [GHOST/VAC/SCAR consultés avant implémentation]
# └── CYCLE DREAM PRÉVU    : ✅ Prêt pour consolidation en fin de tâche
#
# CAS D'ERREUR :
# - graphify-out/ absent  : "Graphify non initialisé. Lancer /graphify . d'abord."
# - .scribe absent        : "Fichier absent. Création via Master Template."
# - YAML invalide         : "YAML corrompu — Lancement du linter interne."
# ════════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════
# 🧮 PARTIE 5 — GRAMMAIRE AXIALE (MEMELANG) POUR LE NEXUS
# ════════════════════════════════════════════════════════════════════════════════
# `;;` = Matrice. `;` = Vecteur. Espaces autour des `;` OBLIGATOIRES.
# Pas de versions de libs ici → Graphify lit package.json.
#
# EXEMPLE NEXUS MINIMAL :
# ;; META_PROJECT
# ; KEY             ; VALUE
# ; Project_Name    ; [NOM_DU_PROJET]
# ; Stack           ; [STACK_TECHNIQUE]
# ; Architecture    ; [TYPE_ARCHITECTURE]
# ; Scribe_Version  ; 3.2 (Axial-Hybrid + Graphify + Doctor-ready)
# ; Session_Count   ; 0
# ; Last_Update     ; YYYY-MM-DDTHH:mm:ssZ
# ; Graphify_Graph  ; graphify-out/graph.json
# ; Schema_Patches  ; 2026-05-23:V3.2(scope/evidence/logs/doctor)
#
# ;; INVARIANTS
# ; ID       ; CATEGORY  ; PROPERTY                                  ; ON_VIOLATION
# ; INV_P001 ; [Cat]     ; [Règle absolue propre au projet]          ; REJECT
# ; INV_P002 ; GRAPHIFY  ; Lire GRAPH_REPORT.md avant tout codebase ; REJECT
#
# ;; TIERS_INDEX
# ; TIER  ; ENTITIES
# ; HOT   ; []
# ; WARM  ; []
# ; COLD  ; []
# ════════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════
# ⚖️ PARTIE 6 — TIERING DYNAMIQUE (L0 / L2)
# ════════════════════════════════════════════════════════════════════════════════
# l0_abstract : 1-2 phrases MAX. Symptôme + clé de solution. Toujours lu.
# l2_details  : Technique complet. Lu seulement si l0 correspond au problème.
# INTERDICTION : Jamais de l1. Jamais d'alias YAML `*`. IDs textuels uniquement.
#
# CHAMPS MUTABLES V3.2 (tout le reste est append-only strict) :
#   - status, status_log
#   - tier, tier_log
#   - confirmed_sessions (ajout uniquement, sessions réelles)
#   - validite.derniere_verif, validite.verifie_par, validite.verdict
#   - journal[].hot_entries_consulted (ajout uniquement, jamais suppression)
#
# STATUS CANONIQUE V3.2 :
#   - status = ACTIVE / SUPERSEDED_BY / RESOLVED / OBSOLETE
#   - status muté → status_log obligatoire avec date, from, to, raison, session
#   - tier muté   → tier_log obligatoire avec date, from, to, raison, session
#   - Exemple status_log/tier_log :
#       - date: "YYYY-MM-DD"
#         from: "ACTIVE"
#         to: "SUPERSEDED_BY"
#         raison: "[Pourquoi la mutation est nécessaire]"
#         session: "JOURNAL-XXX"
#   - `statut` legacy reste lisible pour anciennes DEBTs, mais les nouvelles
#     entrées V3.2 écrivent `status`.
#
# VALIDITÉ V3.2 :
#   validite.derniere_verif : date de dernière vérification
#   validite.verifie_par    : ID JOURNAL réel
#   validite.verdict        : still_valid / stale / superseded
#
# SCOPE_ENUM V3.2 (VAC/PAT obligatoire) :
#   scope_enum:
#     - runtime   # comportement production réelle
#     - test      # environnement de test uniquement
#     - migration # valable pendant une transition, expire
#     - security  # règle de sécurité transversale
#     - dev       # local/agent/dev uniquement, jamais prod
#     - universal # toutes portées sans exception
#   Si le cas ne rentre pas dans ces 6 valeurs, l'entrée est trop vague.
#
# EVIDENCE V3.2 (VAC/PAT obligatoire) :
#   type: OBSERVED / REASONED / ASSUMED
#   OBSERVED → source + observable obligatoires
#   REASONED → source obligatoire
#   ASSUMED  → tier forcé warm, jamais hot
#   REASONED + hot → confirmed_sessions >= 2 avec IDs JOURNAL réels
#   scope: universal → evidence != ASSUMED + human_validated + validated_by_session
#
# COMPATIBILITÉ SCHÉMA V3.2 :
#   - Toute nouvelle entrée post-2026-05-23 porte schema_patch_date: "2026-05-23"
#   - Si schema_patch_date absent, scribe doctor utilise entry.date en fallback
#   - Entrée pré-V3.2 sans scope/evidence/schema_patch_date → WARNING, pas migration forcée
#   - Entrée post-V3.2 sans scope/evidence/schema_patch_date → ERROR bloquant le tier hot
# ════════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════
# 🗂️ PARTIE 7 — INDEX DES ENTITÉS CAUSALES
# ════════════════════════════════════════════════════════════════════════════════
# CLÉ YAML      | PRÉFIXE   | DESCRIPTION                        | QUAND CRÉER
# ──────────────|───────────|------------------------------------|──────────────
# scars[]       | SCAR-XXX  | Bugs résolus — POURQUOI, pas QUOI  | Après résolution
# vaccins[]     | VAC-XXX   | Règles préventives causales         | Après chaque SCAR
# patterns[]    | PAT-XXX   | Solutions réutilisables validées    | Pattern reproductible
# ghosts[]      | GHOST-XXX | Alternatives rejetées + raisons     | Chaque décision archi
# dettes[]      | DEBT-XXX  | Dettes techniques conscientes       | Chaque raccourci
# adrs[]        | ADR-XXX   | Décisions architecturales           | Choix structurels
# lecons[]      | LESSON-XX | Leçons généralisables               | Dépasse le contexte
# journal[]     | —         | Historique sessions                 | Fin de chaque session
# ════════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════
# 📋 PARTIE 8 — TEMPLATES ENTITÉS (L0/L2)
# ════════════════════════════════════════════════════════════════════════════════
# RÈGLE COLLISION D'ID : Vérifier que l'ID n'existe pas avant création.
# NE JAMAIS réutiliser ou écraser un ID existant.
#
# ── SCAR ──────────────────────────────────────────────────────────────────────
# scars:
#   - id: "SCAR-XXX"
#     tier: "hot"
#     status: "ACTIVE"
#     date: "YYYY-MM-DD"
#     schema_patch_date: "2026-05-23"
#     severite: "CRITICAL"           # CRITICAL / HIGH / MEDIUM / LOW
#     l0_abstract: "[Symptôme exact + clé de solution — 1 phrase]"
#     l2_details: >
#       [POURQUOI ce bug arrive. Cause racine. Fausses pistes.
#       PAS de description de structure fichier → Graphify la connaît.]
#     liens_causaux:
#       prevenu_par: "VAC-XXX"
#       contribue_a: "META-PAT-XXX"
#     validite:
#       condition: "toujours"
#       derniere_verif: "YYYY-MM-DD"
#       verifie_par: "JOURNAL-XXX"
#       verdict: "still_valid"       # still_valid / stale / superseded
#     status_log: []
#     tier_log: []
#
# ── VAC ───────────────────────────────────────────────────────────────────────
# vaccins:
#   - id: "VAC-XXX"
#     tier: "hot"
#     status: "ACTIVE"
#     date_creation: "YYYY-MM-DD"
#     schema_patch_date: "2026-05-23"
#     source: "SCAR-XXX"
#     scope: "runtime"               # runtime / test / migration / security / dev / universal
#     evidence:
#       type: "OBSERVED"             # OBSERVED / REASONED / ASSUMED
#       source: "JOURNAL-XXX"        # Obligatoire si OBSERVED ou REASONED
#       observable: "[Fait observé]" # Obligatoire si OBSERVED
#     confirmed_sessions: []
#     human_validated: false
#     validated_by_session: null
#     l0_abstract: "[Règle préventive — 1 phrase]"
#     l2_details: >
#       [POURQUOI cette règle existe. Mécanisme qui rend l'erreur fatale.]
#     virus: "[Ce qu'il ne faut JAMAIS faire]"
#     antidote: "[Ce qu'il faut TOUJOURS faire]"
#     symptomes_si_oublie: "[Conséquence concrète]"
#     liens_causaux:
#       previent: "SCAR-XXX"
#     validite:
#       derniere_verif: "YYYY-MM-DD"
#       verifie_par: "JOURNAL-XXX"
#       verdict: "still_valid"
#     status_log: []
#     tier_log: []
#
# ── PAT ───────────────────────────────────────────────────────────────────────
# patterns:
#   - id: "PAT-XXX"
#     tier: "hot"
#     status: "ACTIVE"
#     date: "YYYY-MM-DD"
#     schema_patch_date: "2026-05-23"
#     scope: "runtime"               # runtime / test / migration / security / dev / universal
#     evidence:
#       type: "REASONED"             # OBSERVED / REASONED / ASSUMED
#       source: "JOURNAL-XXX"
#     confirmed_sessions:
#       - session: "JOURNAL-XXX"
#         verdict: "confirmed"
#     human_validated: false
#     validated_by_session: null
#     l0_abstract: "[Nom du pattern + cas d'usage — 1 phrase]"
#     l2_details: >
#       [POURQUOI ce pattern. Avantages. Pièges.]
#     liens_causaux:
#       source: "SCAR-XXX"
#     validite:
#       derniere_verif: "YYYY-MM-DD"
#       verifie_par: "JOURNAL-XXX"
#       verdict: "still_valid"
#     status_log: []
#     tier_log: []
#
# ── GHOST ─────────────────────────────────────────────────────────────────────
# ghosts:
#   - id: "GHOST-XXX"
#     status: "ACTIVE"
#     titre: "[Décision architecturale]"
#     date: "YYYY-MM-DD"
#     schema_patch_date: "2026-05-23"
#     l0_abstract: "[Ce qui a été choisi et POURQUOI — 1 phrase]"
#     l2_details: >
#       [Contexte de la décision. Contraintes.]
#     alternatives_rejetees:
#       - nom: "[Option rejetée]"
#         raison_rejet: "[POURQUOI précisément]"
#     ne_pas_reproposer: ["[Option A]"]
#     status_log: []
#
# ── DEBT ──────────────────────────────────────────────────────────────────────
# dettes:
#   - id: "DEBT-XXX"
#     status: "ACTIVE"               # ACTIVE / RESOLVED / OBSOLETE
#     titre: "[Titre de la dette]"
#     date_creation: "YYYY-MM-DD"
#     schema_patch_date: "2026-05-23"
#     type: "CHOISIE"                # CHOISIE / SUBIE / DÉCOUVERTE
#     severite: "HIGH"               # CRITICAL / HIGH / MEDIUM / LOW
#     l0_abstract: "[Raccourci pris + impact — 1 phrase]"
#     l2_details: >
#       [POURQUOI ce raccourci. Impact réel. Comment corriger.]
#     plan_remboursement: "[Étapes]"
#     status_log: []
#
# ── JOURNAL ───────────────────────────────────────────────────────────────────
# journal:
#   - id: "JOURNAL-XXX"
#     date: "YYYY-MM-DD"
#     titre: "SESSION [NOM]"
#     llm: "[Modèle / Agent]"
#     session_numero: 1
#     hot_entries_consulted: ["SCAR-XXX", "VAC-XXX", "PAT-XXX"]  # IDs uniquement
#     taches_effectuees:
#       - statut: "DONE"             # DONE / FAILED / PARTIAL
#         type: "FIX"                # FIX / FEAT / REFACTOR / SRE / UI
#         description: "[Tâche accomplie]"
#     scribe_delta: "[SCARs/VACs/PATs créés — ou 'Aucun']"
#     resultat_final: "[État du système en fin de session]"
# ════════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════
# 🛡️ PARTIE 9 — PATTERNS SÉCURITÉ NON-NÉGOCIABLES (PAT-SEC)
# ════════════════════════════════════════════════════════════════════════════════
# Ces règles sont gravées. Violation = rejet immédiat du code généré.
#
# PAT-SEC-001 : Input utilisateur       → Validation TOUJOURS côté serveur
# PAT-SEC-002 : Endpoints sensibles     → Rate limiting obligatoire
# PAT-SEC-003 : Logs                    → JAMAIS de PII en clair
# PAT-SEC-004 : Gestion d'erreurs       → Pas de stack trace en production
# PAT-SEC-005 : SQL                     → Requêtes paramétrées UNIQUEMENT
# PAT-SEC-006 : Output HTML             → Sanitization stricte avant injection DOM
# PAT-SEC-007 : JWT                     → Toujours vérifier la signature
# PAT-SEC-008 : Fichiers I/O            → path.resolve() + path.normalize()
# PAT-SEC-009 : RAF / Listeners globaux → cleanup obligatoire au démontage
# PAT-SEC-010 : Fetch/Axios             → Timeout AbortSignal obligatoire
# ════════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════
# 🚨 PARTIE 10 — PROTOCOLE D'ESCALADE SRE
# ════════════════════════════════════════════════════════════════════════════════
# RÈGLE DES 3 TENTATIVES (ANTI-LOOP) :
# Si 3 échecs consécutifs sur un bug :
#   1. STOPPER toute génération de code
#   2. Créer une SCAR ENRICHIE avec les 3 fausses pistes
#   3. Déclarer une DEBT type SUBIE, sévérité CRITICAL
#   4. Afficher : "🚨 ESCALADE SRE REQUISE : Boucle infinie détectée."
#   5. Demander intervention humaine / modèle supérieur
#
# CORRUPTION DU .scribe :
#   1. Ne PAS coder la feature demandée
#   2. Lancer "Self-Healing" exclusif
#   3. Reconstruire selon Master Template (Partie 11)
# ════════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════
# 🩺 PARTIE 10.5 — SCRIBE DOCTOR V3.2 (READ-ONLY STRICT)
# ════════════════════════════════════════════════════════════════════════════════
# scribe doctor ne modifie JAMAIS le .scribe.
# --suggest-fix produit des suggestions, jamais une écriture automatique.
# Toute évolution SCRIBE DOIT être encadrée par doctor :
#   - doctor AVANT écriture : garantit que l'état de départ est sain
#   - doctor APRÈS écriture : prouve que le delta n'a pas corrompu la mémoire
#   - `<SCRIBE> guard -- <command>` automatise ce pré/post pour les mutations par commande
# En cas d'ERROR avant écriture : ne pas ajouter de nouvelle mémoire.
# En cas d'ERROR après écriture : corriger le delta immédiatement ou le retirer.
#
# PASSES OBLIGATOIRES :
#   PASS 1 — Parse YAML       → E008 si YAML invalide
#   PASS 2 — Unicité IDs      → E004 si doublon
#   PASS 3 — Liens cassés     → E005/E006 si pointeur inexistant
#   PASS 4 — Champs requis    → règles selon schema_patch_date puis entry.date
#   PASS 5 — Règles sémantiques scope/evidence/status/tier
#
# ERRORS (bloquent le tier hot ou la validité structurelle) :
#   E001 — scope absent sur VAC/PAT créé après V3.2
#   E002 — evidence absent sur VAC/PAT créé après V3.2
#   E003 — status != ACTIVE sans status_log
#   E004 — ID dupliqué dans le fichier
#   E005 — superseded_by pointe vers un ID inexistant
#   E006 — liens_causaux.prevenu_par pointe vers un ID inexistant
#   E007 — evidence.type OBSERVED sans source + observable
#   E008 — YAML invalide (parse error)
#   E009 — scope universal sans human_validated + validated_by_session
#   E010 — schema_patch_date absent sur entrée post-V3.2
#
# WARNINGS (informent, ne bloquent pas l'historique) :
#   W001 — scope absent sur entrée ancienne pré-V3.2
#   W002 — evidence absent sur entrée ancienne pré-V3.2
#   W003 — hot entry jamais dans hot_entries_consulted après 10 sessions
#   W004 — ne_pas_reproposer mentionné dans GHOST mais absent de l'index
#   W005 — DEBT ACTIVE sans plan_remboursement
#   W006 — entrée hot avec evidence.type ASSUMED
#   W007 — deux entrées même scope + sujet inverse sans lien causal explicite
#   W008 — REASONED hot sans 2 confirmed_sessions réelles
#   W009 — schema_patch_date absent sur entrée pré-V3.2 (warning agrégé)
#
# OUTPUT :
#   `scribe-out/scribe-doctor-report.md` avec ERRORS(n), WARNINGS(n), SUGGESTIONS.
#   `<SCRIBE> guard` écrit aussi ses rapports pré/post dans `scribe-out/`.
# ════════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════════
# 🏗️ PARTIE 11 — MASTER TEMPLATE : AGENT-MEMOIRE_PROJECT_STATUS.scribe
# ════════════════════════════════════════════════════════════════════════════════
# Structure exacte du .scribe à créer à la racine de CHAQUE projet.
# Ce template est universel — il s'adapte à tout projet, toute stack.
# RÈGLE MIGRATION LEGACY : Lire intégralement → Transformer en entités conformes
# → Journaliser la migration → Vérifier unicité source de vérité → Vider legacy.

template_axial_scribe: |
  [AXIAL_NEXUS_CORE]
  ;; META_PROJECT
  ; KEY             ; VALUE
  ; Project_Name    ; [NOM_DU_PROJET]
  ; Stack           ; [STACK_TECHNIQUE]
  ; Architecture    ; [TYPE_ARCHITECTURE]
  ; Scribe_Version  ; 3.2 (Axial-Hybrid + Graphify + Doctor-ready)
  ; Session_Count   ; 0
  ; Last_Update     ; YYYY-MM-DDTHH:mm:ssZ
  ; Graphify_Graph  ; graphify-out/graph.json
  ; Schema_Patches  ; 2026-05-23:V3.2(scope/evidence/logs/doctor)

  ;; INVARIANTS
  ; ID       ; CATEGORY  ; PROPERTY                                  ; ON_VIOLATION
  ; INV_P001 ; [Cat]     ; [Règle absolue propre au projet]          ; REJECT
  ; INV_P002 ; GRAPHIFY  ; Lire GRAPH_REPORT.md avant tout codebase ; REJECT

  ;; TIERS_INDEX
  ; TIER ; ENTITIES
  ; HOT  ; []
  ; WARM ; []
  ; COLD ; []

  [CAUSAL_CHAINING_YAML]
  scars: []
  vaccins: []
  patterns:
    - id: "PAT-GRAPH-001"
      tier: "hot"
      status: "ACTIVE"
      date: "[YYYY-MM-DD]"
      schema_patch_date: "2026-05-23"
      scope: "dev"
      evidence:
        type: "OBSERVED"
        source: "JOURNAL-001"
        observable: "Graphify est initialisé ou exigé avant lecture brute du codebase."
      confirmed_sessions: []
      human_validated: false
      validated_by_session: null
      l0_abstract: "Graphify avant grep — lire GRAPH_REPORT.md, utiliser graphify query/path/explain."
      l2_details: >
        Toujours utiliser graphify query/path/explain avant de lire des fichiers bruts.
        Les god-nodes et communautés sont dans graphify-out/GRAPH_REPORT.md.
        Watch actif : ~/.local/share/pipx/venvs/graphifyy/bin/python -m graphify watch .
      liens_causaux:
        source: "SETUP-INIT"
      validite:
        derniere_verif: "[YYYY-MM-DD]"
        verifie_par: "JOURNAL-001"
        verdict: "still_valid"
      status_log: []
      tier_log: []
  ghosts: []
  dettes: []
  lecons: []
  journal:
    - id: "JOURNAL-001"
      date: "[YYYY-MM-DD]"
      titre: "SESSION INITIALISATION"
      llm: "[Agent]"
      session_numero: 1
      hot_entries_consulted: ["PAT-GRAPH-001"]
      taches_effectuees:
        - statut: "DONE"
          type: "INIT"
          description: "Initialisation Axial-SCRIBE V3.2 + Graphify."
      scribe_delta: "PAT-GRAPH-001"
      resultat_final: "Mémoire hybride SCRIBE + Graphify opérationnelle."

  [EPISODIC_SCAFFOLDING]
  > **PERSONA & DIRECTIVE IMMÉDIATE**
  > Tu es l'Agent SRE Principal de ce projet.
  >
  > **ÉTAPE 0 OBLIGATOIRE** : Lire `graphify-out/GRAPH_REPORT.md` avant toute réponse codebase.
  > Pour les questions structure/dépendances → `graphify query "..."` (pas de grep).
  >
  > **SCRIBE = POURQUOI uniquement.** Tout fait structurel déductible du code
  > appartient à Graphify. Le SCRIBE est léger par design.
  >
  > **Règle d'or** : Applique le Cycle DREAM en fin de tâche.
  > Ne laisse aucune dette technique non documentée.
```
***
