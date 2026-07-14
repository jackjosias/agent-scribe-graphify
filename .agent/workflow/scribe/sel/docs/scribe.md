# AXIAL-SCRIBE / TENOR Protocol — V2.16

## Protection rule

`AGENT-MEMOIRE_PROJECT_STATUS.scribe` is append/incremental causal memory. Never replace the whole file to “clean it up”, regenerate it from code structure, or erase historical pain.

Graphify owns structural facts. SCRIBE owns causal facts that cannot be deduced from code.

## Canonical system layers

### TENOR — procedural authority

TENOR decides the safe order of installation, host binding and task execution.

Canonical human/LLM trigger:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Canonical command:

```bash
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown> --host <host-id|auto>
```

`bootstrap` is internal/legacy and is not the public V2.16 start.

### Graphify — structural compression

Canonical application graph:

```text
.agent/state/outputs/graphify-out/
```

The portable project build is
`.agent/workflow/scribe/scribe graph --project-build --timeout 180`, or MCP
`graphify_project_build` after host binding. It builds from an isolated mirror.
Root `graphify-out/`, `graphify update .` and `graphify watch` are legacy-only
and forbidden in an application project.

It answers:

```text
what exists
where it lives
how components relate
central nodes
communities
blast radius
```

Real Graphify uses NetworkX node-link JSON (`nodes + links`). Historical supported fixtures may use `nodes + edges`.

### SCRIBE — causal memory

Canonical memory:

```text
AGENT-MEMOIRE_PROJECT_STATUS.scribe
```

It answers:

```text
why a decision exists
which bug hurt before
what approach was rejected
what must not be proposed again
which regression test protects the fix
which debt remains active
```

### SEL — internal maintenance engine

Canonical CLI:

```bash
.agent/workflow/scribe/scribe
```

SEL performs doctor, lock, sync, canonical writes, export and maintenance. It is not the normal agent retrieval interface.

### SCRIBE-RAG — agent retrieval

Canonical CLI:

```bash
.agent/workflow/scribe/scribe-rag
```

Normal agents use `context`, `query`, `explain`, `challenge`, `preflight`, `eval` and `gate` through SCRIBE-RAG or equivalent MCP tools.

## Installation identity

TENOR INIT resolves and classifies the project before memory adoption.

Inputs:

```text
current resolved root
installation manifest
previous recorded root
project marker fingerprint
```

Classifications:

```text
TENOR_INIT_NEW_INSTALLATION
TENOR_INIT_SAME_PROJECT
TENOR_INIT_RELOCATED_PROJECT
TENOR_INIT_LEGACY_INSTALLATION
TENOR_INIT_CORRUPT_INSTALLATION
```

Memory actions occur only after classification:

```text
SCRIBE_MEMORY_ADOPT
SCRIBE_MEMORY_CREATE
```

A relocation may purge copied project-bound `.agent/state/`, never the target memory.

## Graphify readiness

Required files:

```text
graph.json
GRAPH_REPORT.md
graph.html
GRAPHIFY_READY.json
```

Readiness requires:

- parseable JSON;
- list `nodes`;
- supported list `links` or `edges`;
- no contradictory dual edge representation;
- non-empty report/HTML;
- real or explicitly authorized manifest kind;
- current root and workspace fingerprint match;
- no forbidden smoke/stub marker;
- non-empty graph for a non-empty project.

If not ready, TENOR INIT emits a bounded build action. Run it and rerun TENOR INIT.

## Host readiness

Local tool listing is not host visibility proof.

Global readiness requires:

```text
local installation ready
Graphify valid
local MCP ready
host tools visible to the LLM
root binding proved
tenor_init_bridge OK
```

Only then:

```text
TENOR_INIT_READY
```

## Causal memory schema

SCRIBE keeps distinct causal entry types.

### SCAR

A resolved wound with concrete root cause and protection.

Minimum useful fields:

```text
id
symptom
cause_racine
resolution
test_binding
tier
status
```

Create a SCAR after a real bug requiring repeated attempts, regression repair, costly rollback or broken smoke that future agents could repeat.

### VAC / vaccin

A reusable prevention rule derived from a proven failure. It must point to causal evidence rather than generic advice.

### GHOST

A rejected approach, hidden wrong assumption or path that must not be reintroduced.

Useful fields:

```text
id
rejected_approach
why_rejected
ne_pas_reproposer
replacement_or_constraint
```

### PAT

A durable pattern whose value is supported by real sessions. Do not mark a pattern hot without actual evidence.

### DEBT

A consciously accepted risk with reason, scope, consequence and future exit condition.

### JOURNAL

Session/event trace. A JOURNAL is not automatically a SCAR, GHOST or PAT. Activity does not equal causal value.

## Memory-writing rules

Write only information Graphify cannot infer.

Do not write:

```text
file X imports Y
function Z calls A
library version listed in package files
folder structure already visible in the graph
```

Write:

```text
why X was chosen instead of Y
which condition triggered a hidden failure
why an apparently valid approach was rejected
which regression test prevents recurrence
which user-visible pain justified the rule
```

Never create causal entries to improve dashboard ratios or silence warnings.

## Retrieval contract

Before significant work:

```text
targeted scribe_query
targeted graphify_query when code/architecture is involved
```

The agent must state how retrieved evidence changes the plan. If evidence is irrelevant or contradictory, document that decision explicitly.

Reading the `.scribe` file directly is not normal retrieval.

## Workflow tiers

Use the smallest safe tier.

### NANO

Read-only or tightly bounded one-file work without shared mutation. Focused context only.

### STANDARD

Significant implementation. Retrieve context, challenge the plan and inspect blast radius.

### CRITICAL

Authentication, data integrity, public API, deletion, global refactor, SCRIBE mutation, shared agent surfaces or other high-impact work. Require strict preflight and full ownership controls.

A tier is determined by risk, not by desire to perform ceremony.

## Task mutation workflow

```text
discipline_ping
workflow_next
before_task
targeted scribe_query
targeted graphify_query
pre_action_guard
resource_lock_claim
claim_resource
file_hash
propose_patch
apply_patch
workspace_audit
scribe_record or auditable causal skip
release claim and lock
finish_task
workflow_next -> READY_FOR_NEXT_TASK
```

Native host shell/edit/write paths do not satisfy this workflow.

Use only machine intents `read`, `write`, `delete`. One stable identity owns one
active task. A `FIXED` record without an MCP applied-patch receipt is refused.

## Multi-agent ownership

Each agent owns:

```text
agent_id
server-side one-time proof
action leases
claims
resource locks
pending patch identities
```

These are never transferable between agents.

Agents share:

```text
runtime SQLite
canonical SCRIBE
Graphify map
coordination state
```

`TENOR_INIT_SAME_PROJECT` never purges active shared runtime.

## SCRIBE mutation procedure

Before editing canonical memory:

```bash
SCRIBE=.agent/workflow/scribe/scribe
$SCRIBE workflow read --agent <agent> --type <type>
$SCRIBE workflow check --agent <agent>
$SCRIBE sync --agent <agent> --type <type>
$SCRIBE doctor --suggest-fix
$SCRIBE lock acquire --agent <agent> --type <type> --session <JOURNAL-ID>
```

Edit incrementally.

After editing:

```bash
$SCRIBE doctor --suggest-fix
$SCRIBE sync --repair --agent <agent> --type <type> --session <JOURNAL-ID> --changed-id <ID> --write-kind <kind>
$SCRIBE lock release --agent <agent>
```

If doctor reports an error, repair or remove the new delta before proceeding.

## Atomic shared writes

Shared metadata and generated instructions use exclusive temporary creation, `fsync` and atomic replacement. `time.time_ns()` is not accepted as a uniqueness guarantee under concurrent writers.

## Graphify hook safety

PreToolUse hooks must consume stdin before exit and must not emit unsupported `additionalContext`/`hookSpecificOutput` structures.

After Graphify reinstall or upgrade, reapply hook fixes and simulate the active Codex/Gemini commands with representative JSON stdin.

## Closure and anti-drift

Before closing:

> What will hurt the next LLM if this is not documented?

If the answer is concrete causal pain, write the correct causal entry. Otherwise a JOURNAL or explicit skip is enough.

A task is not complete until:

```text
workspace audited
claims/locks released
pending patches resolved
canonical memory decision recorded
finish_task succeeded
READY_FOR_NEXT_TASK returned
```

## Documentation synchronization

Any protocol evolution must follow:

```text
.agent/docs/DOCUMENTATION_SYNC_POLICY.md
```

Update code, tests, canonical docs, generators and PR body together. Dated baselines and `.old` files are historical evidence, not current authority.
