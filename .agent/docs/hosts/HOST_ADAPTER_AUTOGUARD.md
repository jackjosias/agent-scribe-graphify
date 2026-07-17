# Host Adapter Auto-Guard — V2.16 compact task API

The adapter makes the host load the correct project-local MCP server, verifies
the nine-tool public surface and installs a marked instruction block without
overwriting unrelated project instructions.

## What is automated

1. Detect the host and manage only a verified project-local MCP entry.
2. Require reconnect when host configuration changes.
3. Verify bootstrap tools plus `tenor_task_start`,
   `tenor_apply_changeset`, `tenor_activity`, `tenor_task_control`.
4. Tell the host to use the two-call write path rather than the internal
   fine-grained state machine.
5. Deny native autonomous mutation where the host permission model allows it.

## Runtime guarantees

- Identity is bound to the successfully bridged MCP process.
- Task start performs targeted SCRIBE and Graphify internally.
- Changeset apply performs ordered locking, all-file hash preflight, atomic
  apply/rollback, bounded no-shell validation, SCRIBE evidence and closure.
- Activity consolidates process presence and current/last/next task state.
- Task control is owner-only.

## CLI diagnostics

```bash
python3 .agent/scripts/host_auto_guard.py preflight --host opencode
python3 .agent/scripts/host_auto_guard.py install-instructions --host opencode --target .
```

The older `guard` and `audit` subcommands remain compatibility diagnostics for
the internal protocol. They are not the public host workflow.

## Safety classification

- `UNSAFE`: required public tools absent, wrong root or uncontrolled mutation.
- `ACCEPTABLE`: public API works but native host writes remain freely usable.
- `SAFE_CANDIDATE`: root/API/permissions are correct, terrain transaction proof pending.
- `SAFE`: atomic commit and rollback terrain tests pass and bypass paths are denied or detected.

No adapter can neutralize an operating-system principal that has unrestricted
write access outside the host. That residual boundary must remain explicit.
