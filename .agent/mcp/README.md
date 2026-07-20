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
3. `tenor_apply_changeset(task_id, changes, validators, request_id)` performs
   path/scope/hash/lock preflight for every file before the first write,
   applies the complete set, runs validator argv arrays without a shell, then
   commits all files or rolls all files back. At least one validator is
   mandatory. It records the runtime SCRIBE receipt, evaluates memory admission
   and closes only after `promote`, justified `runtime_only`, or an explicit
   user resolution.

The changeset supports structured `edit`, unified `patch`, full-file `replace`,
`create` and confirmed `delete` operations. `edit` applies multiple unique
exact anchors to one path. A destructive `replace` requires a confirmation
bound to path, base hash and new hash; `create` derives the internal new-file
sentinel. It rejects absolute paths, traversal, symlinks, duplicate targets,
stale hashes, out-of-scope paths, no-op mutations and unconfirmed deletions. A
stable `request_id` makes retry safe and detects conflicting reuse.

## Read and control flow

Use `tenor_task_control(action="finish")` to close a read task. The same tool
supports owner-only pause, resume and cancel. `tenor_activity` returns a
consolidated snapshot containing agents, presence, tasks, current action, last
action and next action.

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
