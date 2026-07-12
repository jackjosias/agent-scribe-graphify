# Live Multi-Agent Coordination — V2.16

## Purpose

This document governs several LLM/CLI agents working concurrently in the same repository. It prevents stale mental models, wrong-root sessions, conflicting ownership, cross-agent lease reuse, direct-write bypass and orphan runtime state.

The canonical workflow root remains `.agent/workflow/scribe/`. A sibling `.agent/workflow/multi-agent/` directory is non-canonical.

## Session prerequisite

Every terminal begins with the canonical human/LLM trigger:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

The deterministic command is:

```bash
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown>
```

`bootstrap` is an internal/legacy primitive and is not the public V2.16 multi-agent start.

Before product work, each terminal must prove:

```text
local installation ready
Graphify ready
local MCP ready
host tools visible
root binding correct
tenor_init_bridge OK
TENOR_INIT_READY
```

Without host/root/bridge proof, the terminal remains:

```text
LOCAL_INIT_READY_HOST_MCP_UNBOUND
```

and may not mutate product files.

## Shared versus private state

Agents share:

```text
project root
runtime SQLite
canonical SCRIBE memory
Graphify map
claims and resource-lock registry
patch queue
coordination events
```

Agents never share:

```text
agent_id
proof token
action lease
claim ownership
resource-lock ownership
pending patch identity
```

One agent cannot borrow another agent's proof, lease, claim or lock.

## Initialization serialization

Shared TENOR initialization is protected by `.agent/.tenor-init.lock`.

The lock is nonce-owned and records PID, hostname, root, stage, creation time and heartbeat.

Rules:

- a live owner's lock is never deleted;
- a fresh partially written lock uses filesystem `mtime` as age fallback, never epoch zero;
- stale recovery re-reads and removes only the exact observed nonce;
- `TENOR_INIT_SAME_PROJECT` never purges active shared runtime;
- each terminal registers an independent session after shared initialization.

## Presence

TENOR INIT registers session presence and emits the `Agent session` and `Proof token` used for host bridging.

After bridge, MCP tools are the canonical live coordination interface. Legacy `scribe whoami`/`scribe coordination` commands may remain useful for diagnostics, but they do not replace the session proof, action lease or MCP ownership records.

Each active agent should maintain a fresh heartbeat appropriate to the runtime. A dead PID or expired heartbeat makes presence recoverable according to the bounded stale policy.

## Before every task

```text
workflow_next
before_task
targeted scribe_query
targeted graphify_query when code/architecture is involved
```

The task receives a distinct `task_id` and `context_token`. The agent must inspect relevant active ownership and current workspace state before planning a mutation.

SCRIBE and Graphify output must influence the plan or be explicitly challenged.

## Before mutation

```text
pre_action_guard
resource_lock_claim
claim_resource
file_hash
propose_patch
apply_patch
```

`pre_action_guard` issues an action lease only after required context and workflow state are satisfied.

Rules:

- semantic claim describes the intent, for example `indicator:X` or `auth:token-refresh`;
- resource lock protects the concrete mutation surface;
- the same semantic claim or exact function is a conflict;
- deletion, rename and global refactor require explicit coordination;
- shared files may be touched by different non-conflicting claims only after reread/rebase;
- file hash prevents applying a patch to an obsolete version;
- patches are proposed and applied through MCP, never by blind native edit.

## Direct-write prohibition

The following are not valid mutation paths outside MCP:

```text
shell/bash writes
>, >>
tee
sed -i
perl -pi
write_file/edit/apply_patch native host tools
rm, mv, cp
custom scripts that mutate project files directly
```

If a file changes without the expected MCP receipts:

```text
DIRECT_WRITE_BYPASS_DETECTED
```

Stop, list the affected files and require rollback or explicit user handling.

## Concurrent work scenarios

### Different resources

Two agents may write concurrently when they hold different claims and resource locks and their Graphify blast radii do not create a hidden shared contract conflict.

### Same file, different semantics

Allowed only when:

- claims are different and compatible;
- both agents reread current content before applying;
- hash/patch queue detects stale base versions;
- final validation proves coexistence.

### Same function or semantic claim

Conflict. One agent proceeds; the other waits, changes scope or receives a different task.

### Deletion, rename or global refactor

Exclusive coordination is required before mutation.

## Interrupted-agent recovery

Recovery must be evidence-based:

1. confirm the owner PID/heartbeat is dead or expired;
2. inspect active claim, lock and pending patch records;
3. never reuse the dead agent's lease or proof;
4. abandon/recover ownership through the runtime's explicit recovery path;
5. reread workspace, SCRIBE and Graphify before continuing;
6. create a new agent/task/lease identity for resumed work;
7. audit for partial direct writes or unapplied patches.

Do not delete runtime files manually to “unstick” the system.

## SCRIBE memory mutation

Canonical memory is a separate locked surface.

Before SCRIBE mutation:

```bash
SCRIBE=.agent/workflow/scribe/scribe
$SCRIBE workflow read --agent <agent> --type <type>
$SCRIBE workflow check --agent <agent>
$SCRIBE sync --agent <agent> --type <type>
$SCRIBE doctor --suggest-fix
$SCRIBE lock acquire --agent <agent> --type <type> --session <JOURNAL-ID>
```

After incremental mutation:

```bash
$SCRIBE doctor --suggest-fix
$SCRIBE sync --repair --agent <agent> --type <type> --session <JOURNAL-ID> --changed-id <ID> --write-kind <kind>
$SCRIBE lock release --agent <agent>
```

A runtime `scribe_record` receipt is not automatically a canonical memory append. Promote only real causal value.

## Task closure

Before closing:

```text
workspace_audit
scribe_record or auditable causal skip
release claim
resource_lock_release
finish_task
workflow_next -> READY_FOR_NEXT_TASK
```

Closure is invalid when the agent still owns:

```text
active claim
resource lock
unconsumed action lease
pending patch
unresolved direct-write bypass
```

## Six-terminal terrain acceptance

A real six-terminal replay must prove:

1. six independent TENOR sessions;
2. six distinct proofs and bridges;
3. shared root, SCRIBE, Graphify and runtime;
4. parallel read-only tasks;
5. two independent writes on different resources;
6. same-resource conflict rejection;
7. cross-agent lease rejection;
8. one interrupted-agent recovery;
9. no orphan claims, locks or pending patches;
10. each terminal finishes at `READY_FOR_NEXT_TASK`.

CI concurrency tests are necessary but do not replace this terrain proof.

## Red flags

Stop immediately when:

- host tools are not visible or root binding is unknown;
- an agent presents another agent's proof or lease;
- `pre_action_guard` refuses the action;
- the target hash changed since planning;
- another owner holds the same semantic claim or exact resource;
- a direct mutation appears without MCP receipts;
- Graphify is stale, wrong-root, stub or missing;
- SCRIBE changed since the last memory sync;
- closure would leave runtime ownership behind.

## Short formula

```text
multi-agent V2.16 =
TENOR_INIT_READY per terminal
+ distinct identity/proof/lease
+ shared runtime/SCRIBE/Graphify
+ targeted context
+ guard + lock + claim + patch queue
+ workspace audit
+ clean release and finish
```

## Documentation maintenance

Any coordination change must update code, tests, this file, multi-agent installation docs, machine rules, host rules and PR body according to `.agent/docs/DOCUMENTATION_SYNC_POLICY.md`.
