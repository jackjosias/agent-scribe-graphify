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
     -> COMMITTED_AND_FINISHED | ROLLED_BACK_AND_RETRYABLE | TERMINAL
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
- Any apply or validator failure restores every pre-transaction byte state.
- Incomplete transactions are recovered on the next server start.
- Reusing the same request id and payload returns the prior result; reusing it
  with a different payload is rejected.

## Identity and liveness invariants

- The successful bridge binds one agent identity to one MCP process.
- Task tools derive that identity server-side.
- Cross-agent task control and changeset application are refused.
- A daemon heartbeat reports process presence independently of model turns.
- Valid task activity renews a rolling TTL.
- A live PID receives bounded grace; a dead or abandoned process expires
  fail-closed.
- Parallel agents are observed, never heuristically retired as “ghosts.”

## Internal compatibility

Fine-grained V2.15/V2.16 primitives remain internal compatibility APIs. They
retain their fail-closed contracts and test coverage, including exact first-
write discovery. They are not advertised to the host and must not be
reassembled into a public manual workflow.
