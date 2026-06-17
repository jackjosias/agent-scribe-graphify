# Agentic Friction Policy V4

Version: `2026-06-01`

This policy chooses the smallest safe workflow tier. It does not replace
`docs/scribe.md`; it defines when to pay the full ceremony cost.

Command convention from a host project root:

```bash
SCRIBE=.agent/workflow/scribe/scribe
SCRIBE_RAG=.agent/workflow/scribe/scribe-rag
```

SEL is the internal guard/write engine. scribe-rag is the only agent retrieval
interface.

## Decision Tiers

| Tier | Trigger | Required Memory Work |
| --- | --- | --- |
| `READ_ONLY` | Explain, inspect, status, no writes | `$SCRIBE_RAG preflight --tier READ_ONLY` only if memory matters. No challenge, no doctor, no SCRIBE write. |
| `NANO` | Correction < 30 min, 1 file, no shared surface | `$SCRIBE_RAG context` only. No doctor, no lock, no worktree, no sync. |
| `QUICK` | Simple feature or fix, 1-2h, known surface | `$SCRIBE_RAG preflight --tier QUICK "<plan>"`; challenge is required only when a plan is supplied or implementation is non-trivial. |
| `STANDARD` | Normal feature, fix, refactor, docs, tests, or tooling | Full preflight V5: `$SCRIBE_RAG preflight --tier STANDARD "<plan>"`, Graphify report, focused validation. |
| `CRITICAL` | Auth, data, public API, migrations, shared contracts, destructive or multi-agent coordination | Full preflight V5 with `$SCRIBE workflow read/check`, `$SCRIBE_RAG preflight --tier CRITICAL --strict "<plan>"` plus SEL guard/lock/sync for SCRIBE writes, all challenges documented, SCAR required when a bug is resolved. |

## Automatic Selection

1. No file writes and no state mutation -> `READ_ONLY`.
2. Correction < 30 min, 1 file, no shared contract -> `NANO`.
3. Simple feature or fix, 1-2h, known surface -> `QUICK`.
4. Normal multi-file code, docs, tests, or bundle tooling changes -> `STANDARD`.
5. Architecture, auth, data integrity, public API, destructive actions, SCRIBE mutations, or multi-agent coordination -> `CRITICAL`.

## Hard Skips

- `READ_ONLY`: do not run doctor, acquire locks, or write SCRIBE; `preflight --tier READ_ONLY` may refresh the compact read index only when memory matters.
- `NANO`: run only `$SCRIBE_RAG context`; skip doctor, lock, worktree, sync, and SCRIBE writes unless a real bug or decision was discovered.
- `QUICK`: keep validation focused; skip JOURNAL unless durable causal knowledge was learned.
- Dashboard causal-density warnings are informational; never create SCAR/GHOST/PAT only to improve the ratio.
- Real pain capture is mandatory: bug resolved after >2 attempts, regression, costly rollback, or broken browser/visual smoke => SCAR with `cause_racine`, `resolution`, and `test_binding`; before adjacent work, retrieve the related SCAR/VAC/GHOST with `scribe-rag query/explain/challenge`.
- Escalate only when blast radius grows, validation fails, or a hot SCAR/VAC/GHOST directly applies.

## Guardrails

- **REGLE ABSOLUE : preflight avant toute modification de fichier applicatif, sans exception.**
  NANO  → `$SCRIBE_RAG context`
  QUICK → `$SCRIBE_RAG preflight --tier QUICK "<plan>"`
  STANDARD/CRITICAL → `$SCRIBE_RAG preflight --tier <TIER> --strict "<plan>"` + Graphify
  Meme un fix lint de 30 secondes necessite au moins `$SCRIBE_RAG context`.
  La sortie de preflight/context DOIT etre visible dans la reponse.
- Use `$SCRIBE_RAG preflight --tier STANDARD "<plan>"` before significant implementation; its proof line must be surfaced to the user or working notes.
- Use `$SCRIBE_RAG preflight` for grounding, then `$SCRIBE_RAG query/explain/challenge` for focused retrieval; do not use SEL direct `context`, `query`, or `explain` as an agent interface.
- Use `$SCRIBE_RAG eval --force` before and after retrieval scoring/ranking changes; use `$SCRIBE_RAG gate` before delivery of bundle changes.
- Keep BM25 canonical while it retrieves the right memories; test hybrid embeddings only after concrete recall-loss evidence.
- Use `$SCRIBE workflow read/check` before SCRIBE writes or shared-surface locks; `lock acquire` refuses missing/stale ack.
- Use `$SCRIBE doctor --suggest-fix`, `$SCRIBE lock`, and `$SCRIBE sync` only for SCRIBE writes and maintenance.
- Use `$SCRIBE worktree` before delivery to separate source changes from generated noise.
- Use `$SCRIBE graph --build` only when the bundle architecture matters.
- Do not run doctor for pure read-only answers.
- Do not add journal entries for command relays, status answers, or trivial edits.

## MODE NANO

Use `NANO` for a new task under 30 minutes, one file, and no shared surface.

```bash
# PREFLIGHT
$SCRIBE_RAG context

# WORK
# Do the small fix.

# POSTFLIGHT
# If a bug was resolved, use `$SCRIBE_RAG query` and write a SCAR.
# If a durable decision was made, write a GHOST.
# Otherwise write nothing.
```

No doctor. No lock. No worktree. No sync.

## AutoDream Review Tier

AutoDream is a post-implementation read-only review suggested by the agent only
after a completed implementation. Trigger requires explicit user approval; there
is no automatic idle detector. Use `scribe-rag autodream --read-only`. The runner
uses bounded evidence: current diff surfaces, existing RAG index/context,
coordination state, existing local reports when present, and docs/SCRIBE rule
scans. Output is a report: digested modifications, stale context to drop,
contradictions, read-only proof, and memory candidates. It must not write files
or SCRIBE. If a candidate memory is approved, escalate to CRITICAL as a separate
SCRIBE mutation.

## Canonical Surface Sync

Any SCRIBE workflow evolution must be reflected across the full canonical set:
`AGENTS.md`, `.agent/rules/scribe.md`, `.agent/skills/init-tenor/SKILL.md`,
`.agent/workflow/scribe/README.md`, `.agent/workflow/scribe/rag/README.md`,
`.agent/workflow/scribe/sel/docs/AGENTS.md`,
`.agent/workflow/scribe/sel/docs/friction-policy.md`,
`.agent/workflow/scribe/sel/docs/scribe.md`, and
`AGENT-MEMOIRE_PROJECT_STATUS.scribe`. Do not treat `.old` archive files as
current rule surfaces.

## Tier Quick Reference

- `NANO` -> correction < 30 min, 1 file.
- `QUICK` -> simple feature, 1-2h, known surface.
- `STANDARD` -> complex or multi-file feature.
- `CRITICAL` -> architecture, multi-agent, auth, data, public API, destructive operations.

## Hybrid Signal

Hybrid is not activated because a local model is available. It is tested only
after recall-loss evidence:

- `$SCRIBE_RAG eval --force` drops below `7/8`;
- `$SCRIBE_RAG query "<question>"` misses a known relevant SCRIBE entry;
- `$SCRIBE_RAG challenge "<plan>"` fails to surface a directly related SCAR, VAC, or GHOST;
- BM25 repeatedly returns off-topic results for normal project wording.

```bash
pip install sentence-transformers --break-system-packages
$SCRIBE_RAG build --with-embeddings --force
```

BM25 remains canonical while eval stays `>= 7/8` and known memories are found.

## Success Criteria

The policy is working when:

- small fixes do not pay the full ritual cost;
- critical changes still run doctor/lock/sync around SCRIBE writes;
- agents never call SEL directly for retrieval;
- CI/pre-commit can enforce `$SCRIBE_RAG gate` and fail below 8/8;
- multi-agent sessions can enforce workflow acks before any SCRIBE or shared-surface write;
- app Graphify remains focused on application code;
- bundle architecture remains available through `scribe-out/bundle-graph/`;
- agents spend more time changing the right code and less time rereading policy.
