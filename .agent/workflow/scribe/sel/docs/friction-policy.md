# Agentic Friction Policy — V2.16

This policy selects the smallest safe task tier **after** the session has obtained `TENOR_INIT_READY`. It never replaces installation, host visibility, root binding, session bridge or the MCP mutation workflow.

## Session prerequisite

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

No tier authorizes work before:

```text
local installation ready
Graphify valid
host MCP tools visible
root binding proved
tenor_init_bridge OK
TENOR_INIT_READY
```

## Command convention

```bash
SCRIBE=.agent/workflow/scribe/scribe
SCRIBE_RAG=.agent/workflow/scribe/scribe-rag
```

SEL is the internal guard/write engine. SCRIBE-RAG is the canonical memory retrieval interface.

## Tiers

| Tier | Trigger | Required causal/structural work |
|---|---|---|
| `READ_ONLY` | Explanation, status, inspection, no mutation | Focused retrieval only when memory matters. No doctor, lock or SCRIBE write. |
| `NANO` | Very small known-surface task, usually one file | Targeted context/query; targeted Graphify only when structure matters. |
| `QUICK` | Simple feature/fix on a known surface | Focused preflight, relevant SCRIBE/Graphify evidence and focused validation. |
| `STANDARD` | Normal multi-file feature, fix, docs, tests or tooling | Full task context, plan challenge, blast-radius review and complete MCP workflow. |
| `CRITICAL` | Auth, data integrity, public API, migration, deletion, global refactor, shared contract, SCRIBE mutation or multi-agent conflict | Strict preflight, explicit contradictions, complete ownership controls, broader validation and causal memory decision. |

Time estimates are hints, not authority. Risk, blast radius and ownership determine the tier.

## Automatic selection

1. No file/state mutation → `READ_ONLY`.
2. Tiny isolated known-surface mutation → `NANO`.
3. Simple bounded feature/fix → `QUICK`.
4. Normal multi-file implementation/docs/tests/tooling → `STANDARD`.
5. Security, data, destructive, architectural, SCRIBE or coordination-sensitive work → `CRITICAL`.

Escalate when:

- Graphify shows wider blast radius;
- validation fails;
- a hot SCAR/VAC/GHOST applies;
- a shared semantic claim conflicts;
- direct-write bypass or root uncertainty appears.

## Mandatory task floor

Even NANO mutations still require the MCP safety path:

```text
before_task
targeted retrieval
pre_action_guard
action lease
lock/claim when required
file hash
patch queue
workspace audit
finish_task
```

The tier reduces unnecessary ceremony; it does not authorize native direct writes.

## READ_ONLY

Do not:

- run doctor unnecessarily;
- acquire write locks;
- write SCRIBE;
- generate a JOURNAL for a status relay;
- mutate Graphify/runtime.

Use focused retrieval only when it materially improves the answer.

## NANO

Use for a very small, isolated task with no shared contract or broad blast radius.

Required minimum:

```text
before_task
focused scribe_query when relevant
focused graphify_query when code structure matters
pre_action_guard
safe MCP mutation path
workspace_audit
finish_task
```

No automatic doctor, SCRIBE lock or memory write unless real causal value was discovered.

## QUICK

Use a focused preflight and challenge only when the plan is non-trivial or memory evidence applies. Keep tests targeted.

## STANDARD

Use targeted SCRIBE, Graphify blast radius, explicit plan, complete patch/ownership workflow and focused regression validation.

## CRITICAL

In addition to the full MCP task workflow:

- surface all relevant SCAR/GHOST/constraints;
- document rejected alternatives;
- use strict ownership and coordination;
- require explicit confirmation for deletion/destructive actions;
- run broad tests appropriate to the risk;
- use SCRIBE doctor/lock/sync only when canonical memory itself changes.

## SCRIBE writes

For canonical memory mutation:

```bash
$SCRIBE workflow read --agent <agent> --type <type>
$SCRIBE workflow check --agent <agent>
$SCRIBE sync --agent <agent> --type <type>
$SCRIBE doctor --suggest-fix
$SCRIBE lock acquire --agent <agent> --type <type> --session <JOURNAL-ID>
# incremental causal update
$SCRIBE doctor --suggest-fix
$SCRIBE sync --repair --agent <agent> --type <type> --session <JOURNAL-ID> --changed-id <ID> --write-kind <kind>
$SCRIBE lock release --agent <agent>
```

Do not run this sequence for ordinary product source writes.

## Causal value

A bug resolved after repeated attempts, regression, costly rollback, broken smoke, durable rejected approach or important decision may justify a SCAR/GHOST/PAT.

Do not create memory entries to:

- make a dashboard ratio green;
- document commands with no future value;
- restate Graphify structure;
- make every task appear important.

## Hybrid retrieval

BM25 remains canonical while it retrieves known relevant memories. Hybrid requires actual recall-loss evidence, not merely an installed embedding model.

## AutoDream

AutoDream is an explicit user-approved, read-only review after implementation:

```bash
$SCRIBE_RAG autodream --read-only
```

Any proposed memory write becomes a separate guarded task.

## Success criteria

The policy works when:

- small tasks stay lightweight but still use the safe mutation path;
- critical tasks pay the necessary proof cost;
- agents use SCRIBE-RAG, not SEL direct retrieval;
- Graphify remains structural and SCRIBE remains causal;
- no tier bypasses host/root/bridge readiness;
- no tier authorizes native direct writes;
- task closure produces `READY_FOR_NEXT_TASK`.

## Documentation synchronization

Any tier or workflow change must follow `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`. Do not preserve dated test counts as current architecture.
