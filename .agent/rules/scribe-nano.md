---
trigger: always_on
priority: 1
version: "3.3"
replaces: scribe.md (full injected in every turn)
full_protocol: ".agent/rules/scribe.md"
---

# SCRIBE — ALWAYS-ON NANO (≤ 50 lignes actives)

> Protocole complet → `.agent/rules/scribe.md`  |  Docs → `.agent/workflow/scribe/sel/docs/scribe.md`

## Réflexe de démarrage

| Tier | Commandes |
|------|-----------|
| NANO (< 30 min, 1 fichier) | `.agent/workflow/scribe/scribe-rag context` |
| STANDARD | `scribe-rag build && scribe-rag context && scribe-rag challenge "<plan>"` |
| CRITICAL / mutation SCRIBE | `scribe workflow read --agent <n> --type <t>` → `workflow check` → `scribe-rag preflight --tier CRITICAL --strict "<plan>"` |

## 5 commandes critiques (seules)

```bash
.agent/workflow/scribe/scribe bootstrap          # idempotent, init manquants
.agent/workflow/scribe/scribe-rag context        # contexte RAG courant
.agent/workflow/scribe/scribe-rag query "<q>"   # retrieval ciblé
.agent/workflow/scribe/scribe doctor             # santé bundle
.agent/workflow/scribe/scribe-rag gate           # doit rester 8/8
```

## Invariants non-négociables

- `graphify-out/` et `scribe-out/` → **racine projet** (jamais dans `.agent/state/`)
- Écrire un SCAR si bug > 2 tentatives, régression, rollback coûteux.
- Ne pas écrire dans SCRIBE ce que Graphify déduit du code.
- `scribe-rag gate` doit rester `8/8` avant tout push.

## Preuve minimale par réponse non-triviale

```
SCRIBE_RAG_PROOF: preflight <tier> | eval X/8 | challenge <VERDICT>
```

## Réflexe bloqué (> 2 tentatives)

→ Consulter `scribe-rag query "<symptôme>"` immédiatement.
→ La solution existe peut-être déjà. Ne pas réinventer.
