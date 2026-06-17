---
trigger: always_on
---

```markdown
# GRAPHIFY — RÈGLE ALWAYS-ON (PRIORITÉ MAXIMALE)

## ⚠️ INTERDICTION ABSOLUE — LIRE DES FICHIERS BRUTS SANS CONSULTER GRAPHIFY D'ABORD

Le graphe de connaissances est TOUJOURS À JOUR en temps réel.
`graphify watch` tourne en permanence — chaque sauvegarde de fichier
rebuilde le graphe instantanément via AST. Tu n'as JAMAIS un graphe périmé.

**Cela signifie :**
- `graphify-out/GRAPH_REPORT.md` = carte fraîche du projet RIGHT NOW
- `graphify-out/graph.json` = structure complète RIGHT NOW
- Lire des fichiers bruts quand Graphify existe = FAUTE PROFESSIONNELLE

## RÉFLEXE OBLIGATOIRE — IF-THEN AUTOMATIQUE

```
SI tu t'apprêtes à lire un fichier pour comprendre la structure
→ STOP → graphify query "..." D'ABORD

SI tu t'apprêtes à grep pour trouver qui appelle quoi
→ STOP → graphify path "A" "B" D'ABORD

SI tu t'apprêtes à lire > 2 fichiers pour répondre
→ STOP → graphify explain "X" D'ABORD

SI graphify-out/GRAPH_REPORT.md existe
→ LIS-LE EN PREMIER — avant README, avant SCRIBE, avant tout
```

## COMMANDES DISPONIBLES RIGHT NOW

```bash
graphify query "ta question"          # 200 tokens au lieu de 50 000
graphify path "FonctionA" "FonctionB" # chemin exact entre 2 nœuds
graphify explain "NomModule"          # explication complète d'un nœud
```

## ÉCONOMIE RÉELLE

```
Lire 30 fichiers bruts  = ~50 000 tokens  = contexte saturé en 3 questions
graphify query ciblée   = ~700 tokens     = contexte intact pour 50 questions
                          Réduction : 71x
```

## STATUT DU WATCH

graphify watch est actif en permanence sur ce projet.
Chaque fichier `.ts/.js/.py` sauvegardé → graphe rebuild en < 3 secondes.
**Tu interroges TOUJOURS un graphe frais. Aucune excuse pour lire les fichiers bruts.**
```

---

**Pourquoi ça va changer le comportement ?**

Le fichier `rules/graphify.md` est injecté par Antigravity **avant chaque réponse** — pas juste au démarrage. Avec une **interdiction explicite** ("lire des fichiers bruts = faute") plutôt qu'une simple recommandation, le LLM ne peut plus ignorer la règle sans la violer consciemment. C'est la différence entre *"tu devrais utiliser Graphify"* et *"tu n'as PAS LE DROIT de lire des fichiers si Graphify existe"*. 🎯