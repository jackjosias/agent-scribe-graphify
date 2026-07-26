# TENOR V2.16 orchestration contract

## Authority boundary

The host model chooses the objective, reads code, designs a bounded change and
provides validator commands. TENOR owns mechanical governance: identity,
targeted memory/graph retrieval, task state, ordered locks, hash preflight,
atomic apply/rollback, runtime evidence and terminal closure.

This division is intentional. A small model must not manually drive dozens of
stateful MCP calls or invent replacement agents when one step fails.

## Public state machine

```text
TENOR_INIT_READY
  -> tenor_task_start
     -> READY_FOR_CHANGESET | READY_FOR_READ_FINISH | BLOCKED
  -> tenor_apply_changeset | tenor_task_control
     -> COMMITTED_AND_FINISHED | ROLLED_BACK_AND_RETRYABLE
        | ROLLBACK_CONFLICT_PRESERVED | TERMINAL
```

`tenor_activity` is read-only and may be called at any point after bridge.

## Multi-file transaction invariants

- At most 64 unique project-relative files per changeset.
- No absolute path, traversal, symlink or scope escape.
- Fresh base hash required for every operation, including create/delete.
- Every file and relevant legacy ownership surface is checked before writing.
- Exclusive locks are acquired in deterministic sorted order.
- Staging and backups are durable before replacement.
- Validators are argv arrays, use no shell, have bounded timeout/output and run
  after all files are applied.
- Rollback first preflights every current hash. It restores only a target still
  equal to the changeset `new_hash`; later bytes are never overwritten.
- A rollback hash mismatch is terminal evidence
  `TENOR_CHANGESET_ROLLBACK_CONFLICT`, not permission to destroy a newer write.
- Fenced incomplete transactions are recovered only by the exact recovery
  instance after an atomic fence transfer. Generic recovery never uses PID
  visibility as distributed authority.
- Reusing the same request id and payload returns the prior result; reusing it
  with a different payload is rejected.

## Identity and liveness invariants

- The successful bridge binds one agent identity to one MCP process.
- Task tools derive that identity server-side.
- Cross-agent task control and changeset application are refused.
- A daemon heartbeat reports process presence independently of model turns.
- Valid task activity renews a rolling TTL.
- Job authority is a non-expired SQLite lease plus the exact
  `(job_id, worker_instance_id, fence_token)`.
- A PID is diagnostic only. It cannot authorize heartbeat, publication,
  rollback, lock release or recovery.
- Recovery atomically increments the fence and preserves one logical
  `attempt_count`; an older instance fails closed at every critical boundary.
- Retry exhaustion never bypasses recovery: a terminal recovery fence resolves
  the incomplete changeset before `TENOR_JOB_RETRY_EXHAUSTED` is published.
- Parallel agents are observed, never heuristically retired as “ghosts.”

## Graphify causal publication

- Workspace identity is `relative-path-size-content-sha256-v2`; timestamps do
  not participate in graph identity.
- Source enumeration, bytes and parallel workers are bounded. Each file is
  checked before and after hashing; concurrent mutation fails closed.
- A build refuses every active changeset, revalidates the same fingerprint
  before publication and emits a monotone `graph_epoch`.
- At most one Graphify job is active. A queued rebuild has launch priority and
  fences new changesets until readiness is current.

## SCRIBE promotion and tripwire proof

- Canonical promotion serializes writers through the coordination database.
- File replacement, deterministic `(entry_id, source path, source digest)`
  proof, tripwire receipt and task-context flag commit as one recoverable
  operation; a failed SQL commit restores the previous bytes.
- Canonical `summary` and `l0_abstract` are identical, and successful promotion
  requires semantic retrieval of the entry.
- A concurrent TENOR mutation is exempt from tripwire suspicion only when its
  current hash, declared `new_hash`, write intent, transaction, exclusive lock,
  live job lease, worker instance and fence token all agree.

## Internal compatibility

Fine-grained V2.15/V2.16 primitives remain internal compatibility APIs. They
retain their fail-closed contracts and test coverage, including exact first-
write discovery. They are not advertised to the host and must not be
reassembled into a public manual workflow.
