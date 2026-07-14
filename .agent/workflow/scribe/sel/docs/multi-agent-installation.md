# Multi-Agent Installation and Operation — V2.16

## Purpose

Install, relocate and operate the `.agent` bundle in a host project so several agents can share one codebase without competing roots, stale state, hidden direct writes or ownership confusion.

Live coordination details remain in `live-coordination.md`. Do not create a parallel `.agent/workflow/multi-agent/` root.

## Canonical entry

Every host-agent session starts from the project root with the human trigger:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

The deterministic command is:

```bash
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown> --host <host-id|auto>
```

`tenor-init` is the only public V2.16 installation/relocation/recovery authority. `bootstrap` is internal/legacy and must not be used as a normal startup or bypass.

## Installation authority

TENOR INIT resolves the current root, classifies the installation, purges only old project-bound state when relocation is proven, adopts or creates destination SCRIBE, verifies Graphify, finalizes the local manifest, registers a distinct session and emits machine proof.

Classifications:

```text
TENOR_INIT_NEW_INSTALLATION
TENOR_INIT_SAME_PROJECT
TENOR_INIT_RELOCATED_PROJECT
TENOR_INIT_LEGACY_INSTALLATION
TENOR_INIT_CORRUPT_INSTALLATION
```

Memory actions:

```text
SCRIBE_MEMORY_ADOPT
SCRIBE_MEMORY_CREATE
```

## Relocation rules

When `.agent` is copied from project A to project B:

- preserve B's `AGENT-MEMOIRE_PROJECT_STATUS.scribe` when present;
- purge only copied `.agent/state/` bound to A;
- reject A's sessions, proofs, claims, locks and root bindings;
- preserve copied canonical outputs byte-for-byte but refuse to trust Graphify until B's root/fingerprint validation passes;
- preserve the portable engine;
- write B's manifest in `preparing` then `ready` only after all local gates pass;
- rebuild Graphify for B when required.

Never mutate the source project to manufacture relocation proof.

## Graphify

Canonical outputs live under:

```text
.agent/state/outputs/graphify-out/
```

Real Graphify currently produces NetworkX node-link data with `nodes + links`; the historical supported representation is `nodes + edges`.

The canonical path above is the only application graph authority. A bound host
rebuilds through `graphify_project_build`; before binding use the bounded
SCRIBE project-build command. Never run standalone `graphify update .` in the
product root.

If Graphify is not ready, run only the bounded action returned by TENOR INIT:

```bash
.agent/workflow/scribe/scribe graph --project-build --timeout 180
```

A larger bound may be supplied explicitly for a large codebase. Do not launch hidden or unbounded builds.

## Local MCP gate

After local readiness:

```bash
python3 .agent/mcp/server_entry.py --list-tools
```

This proves only the local server. A manually piped JSON-RPC call does not prove the host model sees the tools either.

## Host gate

For the actual host:

1. detect the exact host and read its matching guide;
2. let TENOR configure only a verified project-local OpenCode/Claude/Codex entry;
3. reconnect/restart and rerun TENOR when required;
4. prove tools visible in the host LLM interface and validate the binding receipt/config hash;
5. prove root binding with matching sentinel/hash;
6. call `tenor_init_bridge`; its one-time proof is consumed server-side;
7. obtain `TENOR_INIT_READY` only from the actual host after all gates.

Until then:

```text
HOST_MCP_UNBOUND
LOCAL_INIT_READY_HOST_MCP_UNBOUND
```

## Six-terminal startup

Each terminal independently runs TENOR INIT.

Expected behavior:

- one shared initialization transaction at a time;
- six distinct `agent_id` values;
- six distinct server-side one-time proofs;
- six distinct leases;
- one shared runtime SQLite;
- one shared canonical SCRIBE;
- one shared Graphify map;
- `TENOR_INIT_SAME_PROJECT` never purges other active sessions.

The init lock is nonce-owned, heartbeat-based and root-bound. A waiter may not delete a live owner's lock. Partial fresh lock files fall back to filesystem `mtime`, never epoch zero.

## Task coordination

Before a concrete task:

```text
workflow_next
before_task
targeted scribe_query
targeted graphify_query
```

Before mutation:

```text
pre_action_guard
resource_lock_claim
claim_resource
file_hash
propose_patch
apply_patch
```

Before closure:

```text
workspace_audit
scribe_record or auditable causal skip
release claim
resource_lock_release
finish_task
workflow_next -> READY_FOR_NEXT_TASK
```

Each stable `agent_id` may own only one active task. Retirement is refused while
tasks, claims, locks or pending patches remain. Multi-file work stays in the
same task and rescopes one exact file at a time after an applied-patch receipt.

Rules:

- semantic claims describe the work intent, not only filenames;
- the same function/semantic claim, deletion, rename or global refactor is a conflict;
- different claims may touch a shared file only with reread/rebase and correct locks;
- one agent never uses another agent's lease, proof, claim or lock;
- no agent reverts files it did not intentionally own;
- closing an agent must leave no claim, lock or pending patch owned by it.

## Direct-write protection

Native host shell/edit/write paths are not valid substitutes for MCP.

Test and control:

```text
shell/bash
write_file/edit/apply_patch
>, >>, tee
sed -i/perl -pi
rm/mv/cp
```

An unexpected mutation without the corresponding MCP path must yield:

```text
DIRECT_WRITE_BYPASS_DETECTED
```

## SCRIBE maintenance

Normal retrieval uses `.agent/workflow/scribe/scribe-rag` or MCP `scribe_query`.

For canonical memory mutation:

```bash
SCRIBE=.agent/workflow/scribe/scribe
$SCRIBE workflow read --agent <agent> --type <type>
$SCRIBE workflow check --agent <agent>
$SCRIBE sync --agent <agent> --type <type>
$SCRIBE doctor --suggest-fix
$SCRIBE lock acquire --agent <agent> --type <type> --session <JOURNAL-ID>
# incremental memory update
$SCRIBE doctor --suggest-fix
$SCRIBE sync --repair --agent <agent> --type <type> --session <JOURNAL-ID> --changed-id <ID> --write-kind <kind>
$SCRIBE lock release --agent <agent>
```

Do not fabricate causal entries to silence dashboard warnings.

## Atomic shared-surface writes

Shared metadata and generated host instructions must use exclusively created temporary files, `fsync` and atomic replacement. Timestamp-derived temporary names are not sufficient under concurrency.

## Acceptance checklist

Local engine:

```text
installation classification correct
manifest preparing -> ready
SCRIBE preserved or created honestly
Graphify real and root-bound
second TENOR INIT idempotent
local MCP tools listable
```

Host terrain:

```text
tools visible to the host LLM
root binding match
tenor_init_bridge OK
TENOR_INIT_READY
complete MCP micro-write
direct-write bypass refused/detected
```

Six-terminal terrain:

```text
six independent identities/proofs
parallel reads
independent writes on different resources
conflict rejection on same resource
cross-agent lease rejection
interrupted-agent recovery
zero orphan claims/locks/patches
```

Release:

```text
portable matrix green
Linux deep validation green
checkout clean after tests
full diff reviewed
docs and generators synchronized
PR body current
```

## Recovery

If the project drifts:

1. run TENOR INIT, never raw bootstrap as a public recovery shortcut;
2. inspect the installation verdict and next action;
3. rebuild Graphify only when requested;
4. re-prove local MCP, host visibility and root binding;
5. audit active agents, claims, locks and pending patches;
6. follow `.agent/docs/DOCUMENTATION_SYNC_POLICY.md` when the drift is documentary.
