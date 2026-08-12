# agent-scribe-graphify MCP — V2.16 public surface

The MCP runtime exposes nine tools to a host model.

Bootstrap/init tools:

- `file_hash`
- `tenor_init_bridge`
- `portability_check`
- `graphify_required_check`
- `graphify_project_build`

Normal task tools:

- `tenor_task_start`
- `tenor_apply_changeset`
- `tenor_activity`
- `tenor_task_control`

## Normal write flow

1. `tenor_task_start(objective, intent, resources, scope)` creates or resumes
   one task for the process-bound agent and performs targeted SCRIBE and
   Graphify retrieval internally. It returns a decision capsule bound to the
   retrieved evidence, canonical-memory hash, Graphify-manifest hash and exact
   resources.
2. The host inspects the relevant code and prepares all intended file changes.
3. `tenor_apply_changeset(task_id, changes, validators, request_id)` verifies
   ownership, task context and the decision capsule, persists an idempotent job,
   launches an isolated worker and returns `TENOR_CHANGESET_ACCEPTED` without
   holding the MCP stdio loop. This verdict is explicitly non-terminal.
4. After `poll_after_ms`, call `tenor_activity`. The worker preflights every
   path/scope/hash/lock before the first write, applies the complete set, runs
   bounded validator argv arrays without a shell, then commits or performs a
   conditional non-destructive rollback. Its terminal job result carries validator evidence,
   SCRIBE admission, capsule resolution and task closure. Never resubmit or
   call task control while the job is queued, recovering, launching or running.

Worker authority is the live SQLite lease plus the exact
`(job_id, worker_instance_id, fence_token)`. The PID is diagnostic only.
Heartbeat and terminal publication reject an expired lease atomically. Recovery
transfers a monotone fence; an older worker cannot publish, rollback or release
the replacement's locks. Infrastructure takeover does not inflate the logical
`attempt_count`. Even when the retry budget is exhausted, the runtime first
acquires a terminal recovery fence, resolves any incomplete changeset and only
then publishes `TENOR_JOB_RETRY_EXHAUSTED`.

The changeset supports structured `edit`, unified `patch`, full-file `replace`,
`create` and confirmed `delete` operations. `edit` applies multiple unique
exact anchors to one path. A destructive `replace` requires a confirmation
bound to path, base hash and new hash; `create` derives the internal new-file
sentinel. It rejects absolute paths, traversal, symlinks, duplicate targets,
stale hashes, out-of-scope paths, no-op mutations and unconfirmed deletions. A
stable `request_id` makes retry safe and detects conflicting reuse.

Rollback first preflights every target. It restores a file only while the
current hash still equals that changeset's `new_hash`; a later write is kept and
reported as `TENOR_CHANGESET_ROLLBACK_CONFLICT`. Transaction evidence and
backups remain available for diagnosis when rollback cannot be proven safe.

## Read and control flow

Use `tenor_task_control(action="finish")` to close a read task. The same tool
supports owner-only pause, resume and cancel, but returns
`TENOR_TASK_CONTROL_JOB_ACTIVE` while a changeset worker is active.
A process-bound replacement session may call
`tenor_task_control(action="reclaim", task_id, expected_owner_agent_id)` for a
non-terminal task whose recorded owner is proven dead and whose heartbeat grace
period, publication leases, claims and locks have all expired. The CAS keeps
the original task id and history, rotates the context token and monotone
`recovery_epoch`, invalidates the old owner, and emits `tenor.task_reclaimed`.
It is idempotent for the new owner and fails closed with
`TENOR_TASK_OWNER_ALIVE`, `TENOR_TASK_OWNER_CHANGED`,
`TENOR_TASK_TERMINAL`, `TENOR_TASK_ACTIVE_PUBLICATION` or
`TENOR_TASK_RECLAIM_FORBIDDEN` when its invariants are not met.
`tenor_activity` recovers dead workers, launches queued work and returns tasks,
presence and redacted durable job states/results.

`graphify_project_build` follows the same acceptance model when a rebuild is
required: `GRAPHIFY_BUILD_ACCEPTED` is non-terminal and
`graphify_required_check` is the polling endpoint. A current graph still
returns `GRAPHIFY_ALREADY_READY` synchronously. Graph identity uses
`relative-path-size-content-sha256-v2`, never `mtime`. Hashing is bounded and
fails closed if a source changes while read. The readiness manifest carries a
monotone `graph_epoch`; rebuilds wait for active changesets and queued rebuilds
have launch priority over new writers.

Canonical SCRIBE promotion serializes concurrent writers and publishes the
file replacement, deterministic source-digest proof, direct-filesystem receipt
and task-context flag under one SQLite transaction with compensating file
restore on failure. `summary` is mirrored into `l0_abstract`, so a successful
promotion must be retrievable by its content as well as its canonical ID.

A failed uncommitted task is cancelled without a no-op changeset. If concurrent
canonical memory or Graphify publication makes the decision capsule stale, the
identical `tenor_task_start` refreshes it inside the same task id. Manual patch
fallback and replacement identities are forbidden.

## Identity and compatibility

After `tenor_init_bridge`, task identity is bound to that MCP server process.
Normal task tools do not accept caller-supplied `agent_id` or context tokens.
One process cannot control another process's task.

The older fine-grained tools remain registered internally so existing runtime
tests and compatibility adapters can call them, but `tools/list` does not
advertise them to host models. They are implementation primitives, not a
workflow for a small model to orchestrate.

## SQLite coordination policy

Every runtime path, including the patch queue, uses one connection policy.
The default is `journal_mode=DELETE`, `synchronous=FULL`, foreign keys enabled
and a bounded 30-second busy timeout. This rollback-journal default is slower
than WAL at peak write concurrency but remains valid when a raw-copied `.agent`
runs on an unknown local, mounted, synchronized or container filesystem.

WAL is never selected heuristically. An operator may opt in only after
filesystem and crash-recovery qualification:

```bash
AGENT_SQLITE_JOURNAL_MODE=WAL python3 .agent/mcp/server_entry.py --list-tools
```

Supported explicit values are `DELETE`, `TRUNCATE` and `WAL`. An invalid value,
an unexpected SQLite application id or an effective-mode mismatch fails closed.
Do not mix journal policies between MCP server processes sharing one project.

## Local diagnostics

```bash
python3 .agent/mcp/server_entry.py --list-tools
```

This proves only that the project-local server starts. It does not prove host
visibility, root binding or a bridged task identity.
