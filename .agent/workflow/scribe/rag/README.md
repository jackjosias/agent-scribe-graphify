# scribe-rag — Canonical Agent Retrieval Layer

## Role

`scribe-rag` is the canonical memory-read interface for agents. Agents must not read `AGENT-MEMOIRE_PROJECT_STATUS.scribe` directly during normal work.

SEL remains the internal maintenance/write engine for doctor, lock, sync, state, export, archive and canonical SCRIBE mutation.

## Place in V2.16

A host session begins with:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

TENOR INIT classifies installation, adopts or creates SCRIBE, verifies Graphify and emits the session machine proof. `scribe-rag` is then used for targeted causal retrieval inside the task workflow.

`scribe-rag preflight` is memory-use proof, not installation authority. `bootstrap` is not the V2.16 public entry.

## Commands

```bash
.agent/workflow/scribe/scribe-rag preflight [--tier STANDARD] [--strict] ["plan"]
.agent/workflow/scribe/scribe-rag build [--with-embeddings] [--force]
.agent/workflow/scribe/scribe-rag context
.agent/workflow/scribe/scribe-rag query "<text>"
.agent/workflow/scribe/scribe-rag explain <ID>
.agent/workflow/scribe/scribe-rag challenge "<plan>"
.agent/workflow/scribe/scribe-rag eval [--force]
.agent/workflow/scribe/scribe-rag gate [--min-passed 8]
.agent/workflow/scribe/scribe-rag autodream --read-only [--format text|json]
.agent/workflow/scribe/scribe-rag doctor
.agent/workflow/scribe/scribe-rag whoami
```

Equivalent MCP retrieval uses `scribe_query` after `before_task` and before sensitive action leases.

## Retrieval contract

A query is not a checkbox. Retrieved SCAR, GHOST, VAC, PAT, invariant, decision, debt or `ne_pas_reproposer` must:

- change the plan;
- add a protective test or constraint;
- reject a previously failed approach;
- or be explicitly challenged as irrelevant/contradictory.

Executing a query and ignoring its output is false memory usage.

## Graphify separation

- Graphify: structure, dependencies, communities, centrality and blast radius.
- SCRIBE-RAG: causal pain, decisions, failures, prohibitions and durable lessons.

Do not store structural facts in SCRIBE when Graphify can infer them. Do not use SCRIBE-RAG as a replacement for Graphify architecture queries.

## Modes

### BM25

Default, portable and dependency-light. Keep BM25 while it retrieves known relevant memories and protocol eval remains acceptable.

### Hybrid

Opt-in only after actual recall-loss evidence, such as:

- eval falling below the accepted gate;
- a known relevant entry repeatedly missing;
- challenge failing to surface a directly related SCAR/VAC/GHOST;
- repeated off-topic retrieval for normal project wording.

The mere presence of `sentence-transformers` or a local embedding model is not a reason to switch.

## Workflow tiers

Use the smallest safe tier selected by risk.

### NANO

Focused context for a bounded read-only or one-file low-risk task.

### STANDARD

Significant implementation: context, targeted query and plan challenge.

### CRITICAL

Auth/data/public API, deletion, global refactor, shared surface or SCRIBE mutation. Use strict preflight and full ownership controls.

A tier does not replace the MCP mutation workflow.

## Gate

```bash
.agent/workflow/scribe/scribe-rag gate
```

The gate rebuilds the compact index, runs protocol evaluation and exits non-zero when the required score is not met.

For full V2.16 validation, also run the portability matrix and Linux deep validation. A green RAG gate does not prove host MCP visibility.

## AutoDream

AutoDream is a user-approved, bounded, read-only post-implementation review:

```bash
.agent/workflow/scribe/scribe-rag autodream --read-only
```

It may inspect diff surfaces, current RAG context, coordination state and docs to propose memory candidates. It must not edit source, mutate SCRIBE, modify generated outputs, install dependencies, start daemons or commit.

Any approved memory write becomes a separate guarded task.

## Local state

`scribe-rag whoami` is read-only and may report last writer/session, lock status, index mode and eval command. It does not create a host bridge and does not produce `TENOR_INIT_READY`.

## Causal quality

Do not create SCAR/GHOST/PAT entries to improve dashboard ratios. Record only concrete future value:

```text
root cause
regression protection
rejected approach
important decision
active debt
forbidden recurrence
```

Before closure, ask what will hurt the next LLM if undocumented.

## Documentation synchronization

All retrieval behavior changes must follow `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`.

Update this file together with the skill, machine rules, always-on rules, SEL docs, generators, tests and PR body. Dated test counts are historical evidence, not permanent architecture.
